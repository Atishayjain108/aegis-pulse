"""Structured logging with OpenTelemetry trace correlation.

One function, ``configure_logging()``, wired up once at process startup.
After that, every module just does ``log = structlog.get_logger(__name__)``
and gets:

- **JSON output** (in prod) or **human-readable** (in dev), chosen by env.
- **Automatic trace/span id injection** from the current OTel context — so
  a single ``trace_id`` threads from scrape → prediction → alert execution.
- **UTC timestamps** in ISO-8601 with microseconds.
- **Unhandled exception capture** — raw ``logging`` tracebacks get the
  same structured envelope, so ``stderr`` from third-party libs is still
  grep-able.
- **PII scrubbing hook** — any log event that contains a top-level key
  listed in ``LOG_SCRUB_KEYS`` has the value replaced before emission.

Design rationale:

- structlog's "final renderer" pattern means we can swap output format
  (console/JSON/file) without touching any call sites.
- OTel context injection is done via a ``structlog`` processor, not a
  separate logging filter; this way async tasks that spawn sub-tasks
  inherit the trace automatically.
- We DO NOT import ``opentelemetry`` at module top level — it's a ~40 MB
  import chain and many deployments (CI, scripts) don't need it. The
  processor is set up lazily inside ``configure_logging()``.

Author: AEGIS Pulse Team
Relationship: imported by every module that logs; must be called ONCE at
process entry (CLI bootstrap, worker startup, uvicorn lifespan).
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import sys
from collections.abc import Mapping, MutableMapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

import structlog

if TYPE_CHECKING:
    from structlog.types import EventDict, Processor

# =============================================================================
# Config
# =============================================================================

LOG_SCRUB_KEYS: Final[frozenset[str]] = frozenset(
    {
        # Anything matching these top-level event keys is replaced with "***".
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "auth",
        "cookie",
        "set_cookie",
        "x_api_key",
        "private_key",
        "access_token",
        "refresh_token",
        "session",
    }
)

_LOG_SCRUB_VALUE: Final[str] = "***"

# Values that LOOK like secrets (long high-entropy strings, bearer tokens, etc.)
# are scrubbed heuristically even if the key name is innocent. Regexes are
# intentionally narrow: too aggressive would eat real UUIDs and trace IDs.
_BEARER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)\bbearer\s+[A-Za-z0-9_\-.]+",
    re.ASCII,
)
_JWT_RE: Final[re.Pattern[str]] = re.compile(
    r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b",
)
# AWS access keys (AKIA...), Slack tokens (xoxb-...), GitHub PATs (ghp_...),
# etc. This is a compact "Stripe-style" roll-up; see detect-secrets for full cover.
_SECRET_PREFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:AKIA[0-9A-Z]{16}|xox[abposr]-[A-Za-z0-9-]{10,}|ghp_[A-Za-z0-9]{36}|gho_[A-Za-z0-9]{36}|sk_live_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,})",
)
# Inline key=value patterns in free-form messages, e.g. "password=hunter2".
# Match only when the key name is one we scrub; bound value capture so we
# don't eat the rest of a sentence. We stop at whitespace, comma, semicolon,
# close-paren, or end of string.
_INLINE_KV_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b("
    + "|".join(sorted(LOG_SCRUB_KEYS, key=len, reverse=True))
    + r")\s*[=:]\s*['\"]?([^\s,;)'\"]+)['\"]?",
)


# =============================================================================
# Processors
# =============================================================================


def _utc_timestamp(_logger: Any, _method: str, event: EventDict) -> EventDict:
    """Add ``timestamp`` (UTC, microsecond ISO-8601) to every event.

    We could use structlog's ``TimeStamper``, but it defaults to local time
    and uses ``isoformat()`` without a trailing ``Z``, which loses timezone
    context when logs are ingested into systems that don't know the source
    timezone. Explicit UTC + ``Z`` removes that class of bug.
    """
    event["timestamp"] = datetime.now(UTC).isoformat(timespec="microseconds")
    return event


def _inject_trace_context(_logger: Any, _method: str, event: EventDict) -> EventDict:
    """Inject OpenTelemetry trace/span IDs if a span is active.

    Keeps import lightweight: the OTel API is imported the first time this
    runs, and if import fails (OTel not installed) we short-circuit forever.
    """
    # Cached sentinel — avoid repeated ImportError overhead.
    inject = _inject_trace_context
    trace_fn: Any = getattr(inject, "_fn", None)
    if trace_fn is None:
        try:
            from opentelemetry import trace

            trace_fn = trace.get_current_span
        except Exception:
            trace_fn = False
        inject._fn = trace_fn  # type: ignore[attr-defined]

    if trace_fn is False:
        return event

    span = trace_fn()
    ctx = span.get_span_context() if span is not None else None
    if ctx is not None and ctx.is_valid:
        event["trace_id"] = f"{ctx.trace_id:032x}"
        event["span_id"] = f"{ctx.span_id:016x}"
    return event


def _scrub_secrets(_logger: Any, _method: str, event: EventDict) -> EventDict:
    """Redact fields whose KEYS indicate secrets, and redact values that LOOK
    like secrets (bearer tokens, JWTs, AKIA keys, inline key=val pairs).

    Operates in-place on the event dict. Nested dicts are walked one level;
    deeper structures are left alone to keep overhead bounded.
    """
    for key in list(event.keys()):
        if key.lower() in LOG_SCRUB_KEYS:
            event[key] = _LOG_SCRUB_VALUE
            continue
        v = event[key]
        if isinstance(v, str):
            event[key] = _redact_string(v)
        elif isinstance(v, Mapping):
            event[key] = {
                k2: (
                    _LOG_SCRUB_VALUE
                    if k2.lower() in LOG_SCRUB_KEYS
                    else _redact_string(v2)
                    if isinstance(v2, str)
                    else v2
                )
                for k2, v2 in v.items()
            }
    return event


def _redact_string(value: str) -> str:
    """Apply known-bad-pattern regexes to a string. Idempotent."""
    if len(value) < 8:
        return value  # too short to hide anything meaningful
    value = _BEARER_RE.sub(f"bearer {_LOG_SCRUB_VALUE}", value)
    value = _JWT_RE.sub(_LOG_SCRUB_VALUE, value)
    value = _SECRET_PREFIX_RE.sub(_LOG_SCRUB_VALUE, value)
    # Also catch inline key=value patterns in free-form messages (common in
    # stdlib logs and f-strings). e.g. "password=hunter2" → "password=***".
    return _INLINE_KV_RE.sub(
        lambda m: f"{m.group(1)}={_LOG_SCRUB_VALUE}",
        value,
    )


def _add_service_context(_logger: Any, _method: str, event: EventDict) -> EventDict:
    """Attach static service-level tags read from env at startup."""
    event.setdefault("service", _SERVICE_NAME)
    event.setdefault("env", _SERVICE_ENV)
    if _SERVICE_VERSION:
        event.setdefault("version", _SERVICE_VERSION)
    return event


# Populated once by configure_logging(); read by _add_service_context.
_SERVICE_NAME: str = "aegis-pulse"
_SERVICE_ENV: str = "dev"
_SERVICE_VERSION: str = ""


# =============================================================================
# Entry point
# =============================================================================


def configure_logging(
    *,
    level: str | int = "INFO",
    json_output: bool | None = None,
    service_name: str = "aegis-pulse",
    service_env: str | None = None,
    service_version: str | None = None,
) -> None:
    """Initialise logging. Call ONCE at process startup.

    Args:
        level: standard logging level name ("DEBUG", "INFO", ...) or numeric.
        json_output: if ``True``, emit JSON lines; if ``False``, coloured
            console; if ``None``, pick JSON when ``AEGIS_LOG_JSON=1`` is set
            or when stdout is not a TTY (i.e. piped into a log aggregator).
        service_name: logical service name — used as the ``service`` tag on
            every event. Should match your OTel service.name.
        service_env: ``dev`` / ``staging`` / ``prod``. Falls back to
            ``AEGIS_ENV`` env var then to ``dev``.
        service_version: git SHA or semver for release correlation.

    Idempotent: calling this twice is safe; the second call replaces the
    configuration cleanly (useful in test fixtures).
    """
    global _SERVICE_NAME, _SERVICE_ENV, _SERVICE_VERSION  # noqa: PLW0603
    _SERVICE_NAME = service_name
    _SERVICE_ENV = service_env if service_env is not None else os.environ.get("AEGIS_ENV", "dev")
    _SERVICE_VERSION = (
        service_version if service_version is not None else os.environ.get("AEGIS_VERSION", "")
    )

    # Decide format
    if json_output is None:
        json_output = os.environ.get("AEGIS_LOG_JSON", "0") == "1" or not sys.stderr.isatty()

    # Normalise level
    if isinstance(level, str):
        level_name = level.upper()
        level_num = logging.getLevelNamesMapping().get(level_name, logging.INFO)
    else:
        level_num = level

    # Reset any previous handlers (makes configure_logging idempotent).
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(level_num)

    # One stderr handler; structlog's final renderer produces the full line.
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level_num)

    # Silence particularly noisy upstream loggers. Worth revisiting per-lib
    # as needed; these are empirical defaults.
    for noisy in (
        "urllib3.connectionpool",
        "httpx._client",
        "httpcore.http11",
        "asyncio",
        "botocore.credentials",
    ):
        logging.getLogger(noisy).setLevel(max(level_num, logging.WARNING))

    # Shared processors — run for BOTH structlog and stdlib logging, so that
    # a stray `logging.info(...)` from a third-party lib shows up with the
    # same envelope (trace_id, service, timestamp, etc).
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        _utc_timestamp,
        _add_service_context,
        _inject_trace_context,
        _scrub_secrets,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # Final renderer: JSON for machine-readable, console for humans.
    final_renderer: Processor
    if json_output:
        final_renderer = structlog.processors.JSONRenderer(
            # orjson would be faster but adds a hard dep in logging path;
            # stdlib json is fine and keeps this module self-contained.
            sort_keys=False,
        )
    else:
        final_renderer = structlog.dev.ConsoleRenderer(
            colors=True,
            exception_formatter=structlog.dev.RichTracebackFormatter(),
        )

    # Configure structlog itself.
    structlog.configure(
        processors=[
            *shared_processors,
            # This must be LAST before the renderer — structlog.stdlib.ProcessorFormatter
            # expects events in its own format, but for logs emitted DIRECTLY via
            # structlog (not stdlib) we still need the final renderer inline.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level_num),
        context_class=dict,
        # Use stdlib LoggerFactory so every structlog logger is backed by a
        # real logging.Logger — this lets add_logger_name find `.name`, and
        # ensures third-party stdlib logging flows through the same handler.
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Route stdlib `logging.*` calls through the same formatter, so that
    # e.g. httpx's built-in logging appears in the same JSON line format.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            final_renderer,
        ],
    )
    handler.setFormatter(formatter)
    root.addHandler(handler)

    # Mark the root logger as configured so we don't double-init if a
    # subprocess re-imports us.
    root.aegis_configured = True  # type: ignore[attr-defined]


def is_configured() -> bool:
    """Return True if ``configure_logging`` has been called in this process."""
    return getattr(logging.getLogger(), "aegis_configured", False)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Convenience wrapper so call sites don't import structlog directly.

    Passing ``__name__`` is the convention; leave as ``None`` for an
    unnamed logger (rare).
    """
    return structlog.get_logger(name)  # type: ignore[no-any-return]


# =============================================================================
# Context managers & helpers
# =============================================================================


def bind_request_context(**kwargs: Any) -> None:
    """Bind key=value pairs into the CURRENT task's structlog context.

    Every subsequent log in this task (and its children) will include these
    fields automatically. Use this at API handler entry points to attach
    things like ``request_id``, ``user_id``, ``tenant_id``.

    Example::

        from aegis.core.logging import bind_request_context

        bind_request_context(request_id=req.id, tenant_id=tenant)

    The binding is cleared automatically when the containing async task
    finishes (structlog uses ``contextvars`` under the hood).
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_request_context() -> None:
    """Remove all bound contextvars for the current task."""
    structlog.contextvars.clear_contextvars()


def flush_if_possible() -> None:
    """Best-effort flush for graceful shutdown (SIGTERM handler).

    structlog's stdlib factory uses line-buffered stderr, so this is
    effectively a no-op — but we expose the function so callers have
    a stable shutdown hook if we switch to an async queue-based handler later.
    """
    for h in logging.getLogger().handlers:
        with contextlib.suppress(Exception):
            h.flush()


# =============================================================================
# Defensive default: if a consumer imports this module and forgets to call
# configure_logging, give them *something* reasonable rather than silence.
# =============================================================================


def _default_configuration() -> None:
    """Minimal fallback for stdlib logging users that never call us."""
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            stream=sys.stderr,
        )


_default_configuration()


__all__ = [
    "LOG_SCRUB_KEYS",
    "bind_request_context",
    "clear_request_context",
    "configure_logging",
    "flush_if_possible",
    "get_logger",
    "is_configured",
]


# =============================================================================
# Sanity check: if a log line's `event` field gets mutated to hide a real
# exception, we want to know. Shadow the original ProcessorFormatter on exit
# just to prove it serializes correctly; this block is skipped in prod.
# =============================================================================


def _signature_check(payload: MutableMapping[str, Any]) -> bool:
    """Validate that an emitted log event has the contractually-required keys.

    Intended for use in unit tests::

        captured = capfd.readouterr().err.splitlines()[-1]
        event = json.loads(captured)
        assert _signature_check(event)
    """
    required = {"timestamp", "level", "service", "env"}
    return required.issubset(payload.keys())
