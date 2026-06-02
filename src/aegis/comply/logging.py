"""Logging shim: prefer ``structlog`` (kwarg-only calls), fall back to stdlib.

The deterministic core must import without ``structlog`` installed, so this
module provides a tiny adapter that mimics the ``structlog`` keyword-argument
API on top of stdlib ``logging`` when ``structlog`` is absent.
"""

from __future__ import annotations

from typing import Any

try:  # pragma: no cover - exercised in both branches across environments
    import structlog

    _STRUCTLOG = True
except Exception:
    _STRUCTLOG = False


if _STRUCTLOG:
    import sys

    # Route structured logs to stderr so stdout stays clean for machine-readable
    # data output (CLI --json, piped results). Configure once, idempotently.
    if not structlog.is_configured():
        structlog.configure(
            processors=[
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.dev.ConsoleRenderer(colors=False),
            ],
            logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
            cache_logger_on_first_use=True,
        )

    def get_logger(name: str) -> Any:
        """Return a structlog logger bound to ``name`` (emits to stderr)."""
        return structlog.get_logger(name)

else:  # pragma: no cover - only when structlog missing
    import logging

    class _KwargLogger:
        """Minimal kwarg-accepting wrapper around a stdlib logger."""

        def __init__(self, name: str) -> None:
            self._log = logging.getLogger(name)

        def _emit(self, level: int, event: str, **kw: Any) -> None:
            extra = " ".join(f"{k}={v!r}" for k, v in kw.items())
            self._log.log(level, "%s %s", event, extra)

        def debug(self, event: str, **kw: Any) -> None:
            self._emit(logging.DEBUG, event, **kw)

        def info(self, event: str, **kw: Any) -> None:
            self._emit(logging.INFO, event, **kw)

        def warning(self, event: str, **kw: Any) -> None:
            self._emit(logging.WARNING, event, **kw)

        def error(self, event: str, **kw: Any) -> None:
            self._emit(logging.ERROR, event, **kw)

    def get_logger(name: str) -> Any:
        """Return a kwarg-accepting stdlib-backed logger bound to ``name``."""
        return _KwargLogger(name)
