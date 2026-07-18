"""
aegis.security.integration - Phase 12 integration bridges for Phases 0-4.

Provides thin adapter functions that apply Phase 12 security primitives
to the existing AEGIS pipeline without modifying Phase 0-4 source files.

Integration points
------------------
Phase 0 (Scrape)
    ``secure_scrape_signal(raw_dict)``
        Apply PII scrubbing before any DB write.

Phase 1 (Persistence)
    ``secure_db_write(pool, signal, audit_logger)``
        Wrap asyncpg writes with audit logging and PII confirmation.

Phase 2 (Agents)
    ``sign_agent_message(payload, hmac_key)``
        HMAC-sign inter-agent Redis Stream messages.
    ``verify_agent_message(payload, hmac_key, signature)``
        Verify incoming agent messages.

Phase 3 (Predict)
    ``sign_prediction(prediction_dict, ed25519_private_pem)``
        Ed25519-sign a prediction record for the audit trail.

Phase 4 (Execute)
    ``audit_alert_dispatch(alert, actor, audit_logger)``
        Log every alert dispatch event to the audit chain.

All functions are pure (no I/O) or explicitly async where I/O is required.
No imports from Phases 0-4 are made here; this module is imported BY them.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from aegis.security.audit.logger import AuditLogger
from aegis.security.crypto import hmac_sha256, hmac_verify, sign_ed25519, verify_ed25519
from aegis.security.pii.scrubber import PIIScrubber, ScrubReport

_log = structlog.get_logger(__name__)

# ── Phase 0: Scrape signal PII scrubbing ──────────────────────────────────── #

# Fields most likely to contain PII in raw scraped signals
_SIGNAL_TEXT_FIELDS: set[str] = {
    "title",
    "body",
    "content",
    "description",
    "author_name",
    "author_bio",
    "comment_text",
    "raw_text",
    "url",  # may contain email params
}


def secure_scrape_signal(
    raw_signal: dict[str, Any],
    *,
    scrubber: PIIScrubber | None = None,
) -> tuple[dict[str, Any], ScrubReport]:
    """Apply PII scrubbing to a raw scraped signal dict.

    Call this in every scrape adapter's ``fetch()`` method BEFORE
    calling any DB write, cache set, or MinIO upload.

    Parameters
    ----------
    raw_signal:
        The raw signal dict as returned by a scrape adapter.
    scrubber:
        Pre-built PIIScrubber; a default instance is created if omitted.

    Returns
    -------
    tuple[dict[str, Any], ScrubReport]
        ``(scrubbed_signal, report)``
    """
    if scrubber is None:
        scrubber = PIIScrubber()

    scrubbed, report = scrubber.scrub_dict(raw_signal, text_fields=_SIGNAL_TEXT_FIELDS)

    if report.was_modified:
        _log.debug(
            "integration.pii_removed",
            replacements=report.replacements,
            ner_removed=report.ner_entities_removed,
            platform=raw_signal.get("platform", "unknown"),
        )

    return scrubbed, report


# ── Phase 1: DB write with audit ──────────────────────────────────────────── #


async def audit_db_write(
    *,
    table: str,
    record_id: str,
    actor: str,
    outcome: str = "success",
    audit_logger: AuditLogger | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log a DB write event to the audit chain.

    Parameters
    ----------
    table:
        Target table name (e.g. ``"signals"``).
    record_id:
        The inserted/updated record's primary key.
    actor:
        Service or user that triggered the write (e.g. ``"service:scraper"``).
    outcome:
        ``"success"`` | ``"failure"`` | ``"partial"``.
    audit_logger:
        Audit logger; no-op if ``None``.
    metadata:
        Additional context (PII-free).
    """
    if audit_logger is None:
        return
    await audit_logger.log(
        f"db.{table}.write",
        actor=actor,
        resource=f"{table}/{record_id}",
        outcome=outcome,
        metadata=metadata or {},
    )


# ── Phase 2: Agent message signing ────────────────────────────────────────── #


def sign_agent_message(
    payload: dict[str, Any],
    hmac_key: str,
) -> str:
    """Compute HMAC-SHA256 signature over a canonical agent message payload.

    This replaces the ad-hoc signing in ``aegis.agents.messaging`` and
    provides a single authoritative implementation.

    Parameters
    ----------
    payload:
        The message dict (must be JSON-serialisable).
    hmac_key:
        HMAC secret key.

    Returns
    -------
    str
        64-character hex HMAC-SHA256 signature.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hmac_sha256(hmac_key, canonical)


def verify_agent_message(
    payload: dict[str, Any],
    hmac_key: str,
    signature: str,
) -> bool:
    """Verify an inter-agent HMAC-SHA256 signature.

    Parameters
    ----------
    payload:
        The received message dict.
    hmac_key:
        HMAC secret key (must match the signing key).
    signature:
        Expected hex HMAC signature.

    Returns
    -------
    bool
        ``True`` if signature is valid; ``False`` if tampered or wrong key.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hmac_verify(hmac_key, canonical, signature)


# ── Phase 3: Prediction signing ───────────────────────────────────────────── #


def sign_prediction(
    prediction: dict[str, Any],
    private_key_pem: bytes,
) -> str:
    """Ed25519-sign a prediction record for the cryptographic audit trail.

    The signature covers the canonical JSON of the prediction dict
    (sorted keys, no whitespace). Store the signature alongside the
    record in the ``predictions`` table.

    Parameters
    ----------
    prediction:
        Prediction record dict (from ``aegis.predict.schemas.Prediction``).
    private_key_pem:
        Ed25519 private key in PEM format.

    Returns
    -------
    str
        Base64-encoded 64-byte Ed25519 signature.
    """
    canonical = json.dumps(prediction, sort_keys=True, separators=(",", ":"))
    sig = sign_ed25519(private_key_pem, canonical.encode("utf-8"))
    _log.debug(
        "integration.prediction_signed",
        trend_id=prediction.get("trend_id", "?"),
    )
    return sig


def verify_prediction_signature(
    prediction: dict[str, Any],
    public_key_pem: bytes,
    signature: str,
) -> bool:
    """Verify the Ed25519 signature on a prediction record.

    Parameters
    ----------
    prediction:
        Prediction record (must match the dict that was signed exactly).
    public_key_pem:
        Ed25519 public key in PEM format.
    signature:
        Base64-encoded signature from ``sign_prediction()``.

    Returns
    -------
    bool
        ``True`` if valid, ``False`` if tampered.
    """
    canonical = json.dumps(prediction, sort_keys=True, separators=(",", ":"))
    return verify_ed25519(public_key_pem, canonical.encode("utf-8"), signature)


# ── Phase 4: Alert dispatch audit ─────────────────────────────────────────── #


async def audit_alert_dispatch(
    *,
    alert_id: str,
    trend_id: str,
    verdict: str,
    channel: str,
    outcome: str,
    actor: str = "service:execute",
    audit_logger: AuditLogger | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Audit-log an alert dispatch event from Phase 4.

    Call this in ``aegis.execute.notifiers.*`` after each dispatch attempt.

    Parameters
    ----------
    alert_id:
        UUID of the ``AlertEnvelope``.
    trend_id:
        Trend that triggered the alert.
    verdict:
        ``"ENTER"`` | ``"HOLD"`` | ``"BLOCK"``.
    channel:
        Notification channel used (``"telegram"``, ``"discord"``, etc.).
    outcome:
        ``"success"`` | ``"failure"`` | ``"skipped"`` | ``"blocked"``.
    actor:
        Service name.
    audit_logger:
        Audit logger; no-op if ``None``.
    metadata:
        Additional fields (score, confidence, etc.).
    """
    if audit_logger is None:
        return
    await audit_logger.log(
        f"alert.{channel}.dispatch",
        actor=actor,
        resource=f"alerts/{alert_id}",
        outcome=outcome,
        metadata={
            "alert_id": alert_id,
            "trend_id": trend_id,
            "verdict": verdict,
            "channel": channel,
            **(metadata or {}),
        },
    )


# ── Utility: secure Redis publish ─────────────────────────────────────────── #


async def secure_redis_publish(
    redis_client: Any,  # noqa: ANN401
    stream: str,
    payload: dict[str, Any],
    hmac_key: str,
    *,
    field: str = "body",
) -> None:
    """Publish a signed message to a Redis Stream.

    Adds ``"signature"`` to the payload before publishing.
    Compatible with the Phase 2 ``aegis:phase2:graph_results`` stream.

    Parameters
    ----------
    redis_client:
        Async Redis client.
    stream:
        Redis Stream key.
    payload:
        Message dict to publish.
    hmac_key:
        HMAC key for signing.
    field:
        Redis Stream field name for the payload (default ``"body"``).
    """
    sig = sign_agent_message(payload, hmac_key)
    payload_with_sig = {**payload, "signature": sig}
    body = json.dumps(payload_with_sig)
    await redis_client.xadd(stream, {field: body})
    _log.debug("integration.redis_publish_signed", stream=stream)


async def secure_redis_consume(
    entry_body: str | bytes,
    hmac_key: str,
    *,
    verify_signature: bool = True,
) -> dict[str, Any] | None:
    """Parse and optionally verify a signed Redis Stream entry.

    Parameters
    ----------
    entry_body:
        Raw JSON string from the ``"body"`` field of the stream entry.
    hmac_key:
        HMAC key for verification.
    verify_signature:
        If True, reject entries with invalid or missing signatures.

    Returns
    -------
    dict[str, Any] | None
        Parsed payload dict, or ``None`` if signature verification fails.
    """
    if isinstance(entry_body, bytes):
        entry_body = entry_body.decode("utf-8")

    try:
        data: dict[str, Any] = json.loads(entry_body)
    except json.JSONDecodeError as exc:
        _log.error("integration.redis_parse_error", error=str(exc))
        return None

    if not verify_signature:
        return data

    sig = data.pop("signature", None)
    if sig is None:
        _log.warning("integration.redis_no_signature")
        return None

    if not verify_agent_message(data, hmac_key, sig):
        _log.error("integration.redis_signature_invalid")
        return None

    return data
