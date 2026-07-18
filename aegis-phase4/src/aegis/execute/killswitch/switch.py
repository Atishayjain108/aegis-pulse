"""Global emergency halt.

A single Redis key controls whether the dispatcher is allowed to send
alerts. Trip the switch and within < 1 s every drainer cycle stops.

Fail-closed semantics: if Redis is unreachable, `is_tripped()` returns
True. This is the safe default — we'd rather lose latency than send wrong
alerts during an outage.

State values stored in Redis:
    "TRIPPED" → halt
    "ARMED" or missing → permit

Every toggle writes an audit row to `killswitch_audit` (separate concern,
done by the API layer, not here).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import structlog

from aegis.execute.constants import (
    KILLSWITCH_KEY_DEFAULT,
    KILLSWITCH_STATE_ARMED,
    KILLSWITCH_STATE_TRIPPED,
)
from aegis.execute.errors import (
    EXEC_KILLSWITCH_BACKEND_UNAVAILABLE,
    EXEC_KILLSWITCH_TRIPPED,
    AegisExecuteError,
)

_log = structlog.get_logger(__name__)


@runtime_checkable
class _RedisLike(Protocol):
    async def get(self, name: str) -> bytes | str | None: ...
    async def set(self, name: str, value: str) -> bool | None: ...
    async def delete(self, *names: str) -> int: ...


class KillSwitch:
    """Fail-closed kill switch.

    `is_tripped()` is hot-path; design optimises for that.
    """

    __slots__ = ("_fail_closed", "_key", "_redis")

    def __init__(
        self,
        *,
        redis_client: _RedisLike | None = None,
        key: str = KILLSWITCH_KEY_DEFAULT,
        fail_closed: bool = True,
    ) -> None:
        self._redis = redis_client
        self._key = key
        self._fail_closed = fail_closed

    async def is_tripped(self) -> bool:
        """Return True if dispatch is currently halted."""
        if self._redis is None:
            # No Redis configured: treat as armed in dev; fail-closed in prod
            # is set by caller via fail_closed flag at construction time.
            return self._fail_closed
        try:
            raw = await self._redis.get(self._key)
        except Exception as exc:
            _log.error(
                "killswitch.backend_unavailable",
                error=str(exc),
                fail_closed=self._fail_closed,
            )
            return self._fail_closed
        if raw is None:
            return False
        val = raw.decode() if isinstance(raw, bytes | bytearray) else str(raw)
        return val.strip().upper() == KILLSWITCH_STATE_TRIPPED

    async def trip(self, *, reason: str = "manual") -> None:
        """Trip the switch (halt dispatch)."""
        if self._redis is None:
            raise AegisExecuteError(
                EXEC_KILLSWITCH_BACKEND_UNAVAILABLE,
                context={"action": "trip", "reason": reason},
            )
        await self._redis.set(self._key, KILLSWITCH_STATE_TRIPPED)
        _log.warning("killswitch.tripped", reason=reason)

    async def arm(self, *, reason: str = "manual") -> None:
        """Arm the switch (resume dispatch)."""
        if self._redis is None:
            raise AegisExecuteError(
                EXEC_KILLSWITCH_BACKEND_UNAVAILABLE,
                context={"action": "arm", "reason": reason},
            )
        await self._redis.set(self._key, KILLSWITCH_STATE_ARMED)
        _log.warning("killswitch.armed", reason=reason)

    async def raise_if_tripped(self) -> None:
        """Raise AegisExecuteError(EXEC_KILLSWITCH_TRIPPED) if tripped."""
        if await self.is_tripped():
            raise AegisExecuteError(EXEC_KILLSWITCH_TRIPPED)

    async def state(self) -> str:
        """Return the human-readable state string."""
        return (
            KILLSWITCH_STATE_TRIPPED
            if await self.is_tripped()
            else KILLSWITCH_STATE_ARMED
        )


__all__ = ["KillSwitch"]
