"""
Schema drift monitor — detects when an adapter's response structure changes.

Without this, silent schema changes from undocumented APIs will corrupt the
signals table with nulls or wrong types.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import structlog

_log = structlog.get_logger("aegis.scrape.schema_guard")

# Required fields every signal dict must contain
REQUIRED_SIGNAL_FIELDS = frozenset([
    "title", "url", "platform", "scraped_at",
])

# Fields that must be present but can be None
NULLABLE_SIGNAL_FIELDS = frozenset([
    "author", "score", "views", "likes", "comments", "shares",
    "saves", "sentiment", "raw_json",
])

ALL_SIGNAL_FIELDS = REQUIRED_SIGNAL_FIELDS | NULLABLE_SIGNAL_FIELDS


def validate_signal(signal: dict[str, Any], platform: str) -> tuple[bool, list[str]]:
    """Returns (is_valid, list_of_violations)."""
    violations = []
    for f in REQUIRED_SIGNAL_FIELDS:
        if f not in signal or signal[f] is None:
            violations.append(f"Missing required field: {f}")
    return len(violations) == 0, violations


def validate_batch(signals: list[dict[str, Any]], platform: str) -> list[dict[str, Any]]:
    """
    Filter out invalid signals and log violations.
    Returns only valid signals.
    """
    valid = []
    for i, sig in enumerate(signals):
        ok, violations = validate_signal(sig, platform)
        if ok:
            valid.append(sig)
        else:
            _log.warning(
                "signal_schema_violation",
                platform=platform,
                index=i,
                violations=violations,
            )
    if len(valid) < len(signals):
        _log.error(
            "batch_schema_drift_detected",
            platform=platform,
            total=len(signals),
            valid=len(valid),
            dropped=len(signals) - len(valid),
        )
    return valid


def fingerprint_response(data: dict[str, Any] | list[Any], platform: str) -> str:
    """
    Hash the structural shape of a JSON response (keys only, not values).
    Use to detect schema drift between runs.
    """
    def extract_shape(obj: Any, depth: int = 0) -> Any:
        if depth > 3:
            return "..."
        if isinstance(obj, dict):
            return {k: extract_shape(v, depth + 1) for k, v in sorted(obj.items())}
        if isinstance(obj, list) and obj:
            return [extract_shape(obj[0], depth + 1)]
        return type(obj).__name__

    shape = extract_shape(data)
    shape_str = json.dumps(shape, sort_keys=True)
    return hashlib.sha256(shape_str.encode()).hexdigest()[:16]


__all__ = [
    "ALL_SIGNAL_FIELDS",
    "NULLABLE_SIGNAL_FIELDS",
    "REQUIRED_SIGNAL_FIELDS",
    "fingerprint_response",
    "validate_batch",
    "validate_signal",
]
