"""
Injectable clock — same pattern as `aegis.execute.utils.clock` in Phase 4.

Tests inject a `FixedClock` for determinism; production uses `SystemClock`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Time abstraction. Always returns timezone-aware UTC datetimes."""

    def now(self) -> datetime: ...


class SystemClock:
    """Wall-clock time in UTC."""

    __slots__ = ()

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Test clock — returns the same instant every call until `set()`."""

    __slots__ = ("_t",)

    def __init__(self, t: datetime) -> None:
        if t.tzinfo is None:
            raise ValueError("FixedClock requires tz-aware datetime")
        self._t = t

    def now(self) -> datetime:
        return self._t

    def set(self, t: datetime) -> None:
        if t.tzinfo is None:
            raise ValueError("FixedClock.set requires tz-aware datetime")
        self._t = t


__all__ = ["Clock", "FixedClock", "SystemClock"]
