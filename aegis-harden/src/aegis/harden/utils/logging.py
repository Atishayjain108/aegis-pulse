"""
Structured logging — wraps structlog. Same pattern as `aegis.agents`.

Phase 5 uses kw-arg log calls only: `_log.info("event", key=val)`.
"""

from __future__ import annotations

import structlog

# Configure once on import. Tests reconfigure via structlog directly when
# they need to capture output.
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
    cache_logger_on_first_use=True,
)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound logger for `name`. Idiomatic: 'aegis.harden.<module>'."""
    return structlog.get_logger(name)


__all__ = ["get_logger"]
