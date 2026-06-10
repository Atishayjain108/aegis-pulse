"""
aegis.security.vault._circuit_breaker — Sliding-window circuit breaker.

States: CLOSED → OPEN → HALF-OPEN → CLOSED
"""
from __future__ import annotations

import time
from collections import deque
from enum import Enum, auto
from threading import Lock


class _State(Enum):
    CLOSED = auto()
    OPEN = auto()
    HALF_OPEN = auto()


class CircuitBreaker:
    """Thread-safe, time-windowed circuit breaker."""

    def __init__(
        self,
        *,
        error_rate_threshold: float = 0.30,
        window_s: int = 60,
        half_open_after_s: int = 120,
        min_requests: int = 5,
    ) -> None:
        self._threshold = error_rate_threshold
        self._window_s = window_s
        self._half_open_after_s = half_open_after_s
        self._min_requests = min_requests

        self._state: _State = _State.CLOSED
        self._tripped_at: float = 0.0
        self._events: deque[tuple[float, bool]] = deque()
        self._lock = Lock()

    def allow_request(self) -> bool:
        with self._lock:
            self._expire_old_events()
            if self._state == _State.CLOSED:
                return True
            if self._state == _State.OPEN:
                if time.monotonic() - self._tripped_at >= self._half_open_after_s:
                    self._state = _State.HALF_OPEN
                    return True
                return False
            return True  # HALF_OPEN: allow probe

    def record_success(self) -> None:
        with self._lock:
            self._events.append((time.monotonic(), False))
            if self._state == _State.HALF_OPEN:
                self._state = _State.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._events.append((now, True))
            self._expire_old_events()
            if self._state == _State.HALF_OPEN:
                self._state = _State.OPEN
                self._tripped_at = now
                return
            if self._state == _State.CLOSED:
                total = len(self._events)
                if total >= self._min_requests:
                    errors = sum(1 for _, e in self._events if e)
                    if errors / total > self._threshold:
                        self._state = _State.OPEN
                        self._tripped_at = now

    @property
    def state(self) -> str:
        return self._state.name

    def _expire_old_events(self) -> None:
        cutoff = time.monotonic() - self._window_s
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()
