"""Time utilities for Phase 4.

All datetimes in Phase 4 are timezone-aware UTC. Every place that needs
"now" must call `utc_now()` (or accept a clock callable) — never use
`datetime.now()` directly.

Tests use `set_clock()` to inject a deterministic clock.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now(UTC)


_clock: Clock = _real_clock


def utc_now() -> datetime:
    """Return the current UTC time (injectable for tests)."""
    return _clock()


def set_clock(clock: Clock) -> None:
    """Inject a deterministic clock (test-only)."""
    global _clock  # noqa: PLW0603
    _clock = clock


def reset_clock() -> None:
    """Restore the real clock."""
    global _clock  # noqa: PLW0603
    _clock = _real_clock


__all__: Final = ["Clock", "reset_clock", "set_clock", "utc_now"]
