"""
Shared types for tools.

`ToolResult` is what every tool returns. Agents inspect `.ok` and
either consume `.data` or fall through to a backup path. We never
raise out of a tool because doing so makes the agent code uglier
without adding information.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import functools
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar

import structlog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_log = structlog.get_logger("aegis.agents.tools")

P = ParamSpec("P")
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ToolError:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool
    data: Any = None
    error: ToolError | None = None
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, data: Any, *, duration_ms: float = 0.0, **metadata: Any) -> ToolResult:
        return cls(ok=True, data=data, duration_ms=duration_ms, metadata=metadata)

    @classmethod
    def failure(
        cls,
        code: str,
        message: str,
        *,
        duration_ms: float = 0.0,
        **metadata: Any,
    ) -> ToolResult:
        return cls(
            ok=False,
            error=ToolError(code=code, message=message),
            duration_ms=duration_ms,
            metadata=metadata,
        )


def tool_call(
    name: str,
) -> Callable[[Callable[P, Awaitable[ToolResult]]], Callable[P, Awaitable[ToolResult]]]:
    """Decorator that wraps a tool with timing + structured logging.

    The wrapped tool MUST already return `ToolResult`. The decorator
    only adds duration measurement and a single trace log entry per
    call. We catch BaseException and convert to a failure result so
    the agent never sees an exception.
    """

    def decorator(func: Callable[P, Awaitable[ToolResult]]) -> Callable[P, Awaitable[ToolResult]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> ToolResult:
            start = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
            except Exception as exc:
                duration_ms = (time.perf_counter() - start) * 1000.0
                _log.exception("tool.exception", tool=name, duration_ms=duration_ms)
                return ToolResult.failure(
                    code=f"AEGIS-TOOL-{name.upper()}-EXC",
                    message=f"{type(exc).__name__}: {exc}",
                    duration_ms=duration_ms,
                )
            duration_ms = (time.perf_counter() - start) * 1000.0
            ok = result.ok
            _log.info("tool.call", tool=name, ok=ok, duration_ms=duration_ms)
            # Return a copy with the actual measured duration.
            return ToolResult(
                ok=result.ok,
                data=result.data,
                error=result.error,
                duration_ms=duration_ms,
                metadata=result.metadata,
            )

        return wrapper

    return decorator
