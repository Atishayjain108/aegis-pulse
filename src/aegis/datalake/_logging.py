"""Structured-log helper for Phase 10.

Mirrors the pattern used in `aegis.agents.*` — every module gets a logger
via ``get_logger(__name__)`` and emits keyword-argument events.

Falls back to stdlib ``logging`` if structlog is unavailable so unit tests
in a minimal environment still produce coherent output.
"""

from __future__ import annotations

import logging
from typing import Any

try:  # structlog is the preferred backend (matches Phase 2)
    import structlog

    _HAS_STRUCTLOG = True
except ImportError:  # pragma: no cover — stdlib fallback
    _HAS_STRUCTLOG = False


def get_logger(name: str) -> Any:
    """Return a logger that supports `.info("event", key=value)` style calls.

    Args:
        name: Module name, usually ``__name__``.

    Returns:
        A structlog BoundLogger when structlog is installed, otherwise a
        thin wrapper around the stdlib logger that accepts kwargs.
    """
    if _HAS_STRUCTLOG:
        return structlog.get_logger(name)
    return _StdlibKwargsAdapter(logging.getLogger(name))


class _StdlibKwargsAdapter:
    """Minimal adapter so kwargs-style calls work without structlog."""

    __slots__ = ("_log",)

    def __init__(self, log: logging.Logger) -> None:
        self._log = log

    def _emit(self, level: int, event: str, **kwargs: Any) -> None:
        if not kwargs:
            self._log.log(level, event)
            return
        kv = " ".join(f"{k}={v!r}" for k, v in kwargs.items())
        self._log.log(level, "%s %s", event, kv)

    def debug(self, event: str, **kwargs: Any) -> None:
        self._emit(logging.DEBUG, event, **kwargs)

    def info(self, event: str, **kwargs: Any) -> None:
        self._emit(logging.INFO, event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._emit(logging.WARNING, event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self._emit(logging.ERROR, event, **kwargs)

    def exception(self, event: str, **kwargs: Any) -> None:
        # stdlib .exception captures exc_info automatically
        if kwargs:
            kv = " ".join(f"{k}={v!r}" for k, v in kwargs.items())
            self._log.exception("%s %s", event, kv)
        else:
            self._log.exception(event)


__all__ = ["get_logger"]
