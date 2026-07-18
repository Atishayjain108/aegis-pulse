"""
src/aegis/testing/__init__.py — AEGIS Pulse Phase 13: Reusable Test Helpers.

Public API for the aegis.testing namespace.  Import these in any test file:

    from aegis.testing import assert_structlog_event, wait_for_redis_key, ...

Architecture position:
  This is a first-class package inside src/aegis/ so it ships with the project
  and can be used from any package (including aegis-phase4, aegis-harden) without
  path hacks.

Design rules:
  - No test framework imports at module level (pytest, hypothesis etc.) — helpers
    must be usable in production health-check mode too.
  - All network helpers time out at 30 s and raise descriptive AssertionErrors.
  - All datetime values are UTC-aware.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING, Any
import uuid

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "PANDERA_AVAILABLE",
    "aegis_error_code",
    "assert_dict_subset",
    "assert_no_structlog_errors",
    "assert_structlog_event",
    "assert_utc_datetime",
    "assert_valid_uuid",
    "eventually",
    "is_aegis_error",
    "normalize_uuids",
    "requires_gpu",
    "requires_minio",
    "requires_ollama",
    "requires_postgres",
    "requires_redis",
    "strip_timestamps",
    "wait_for_condition",
    "wait_for_redis_key",
]

# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------


def assert_structlog_event(
    log_output: str | list[str],
    event: str,
    *,
    level: str | None = None,
    **extra_fields: Any,
) -> None:
    """Assert that a structlog event was emitted with the expected fields.

    Args:
        log_output: Captured structlog output (str or list of lines).
        event: The ``event`` field value to match.
        level: Optional log level to match (e.g. "warning", "error").
        **extra_fields: Additional key=value pairs that must appear in the log line.

    Raises:
        AssertionError: If no matching log line is found.
    """
    lines = log_output if isinstance(log_output, list) else log_output.splitlines()
    for line in lines:
        if event not in line:
            continue
        if level and level.lower() not in line.lower():
            continue
        if all(str(v) in line for v in extra_fields.values()):
            return
    fields_desc = ", ".join(f"{k}={v!r}" for k, v in extra_fields.items())
    msg = f"Expected structlog event {event!r}"
    if level:
        msg += f" at level {level!r}"
    if extra_fields:
        msg += f" with fields [{fields_desc}]"
    msg += "\nActual lines:\n" + "\n".join(lines[-20:])
    raise AssertionError(msg)


def assert_no_structlog_errors(log_output: str | list[str]) -> None:
    """Assert no ERROR or CRITICAL lines appear in structlog output."""
    lines = log_output if isinstance(log_output, list) else log_output.splitlines()
    errors = [ln for ln in lines if "[error]" in ln.lower() or "[critical]" in ln.lower()]
    if errors:
        msg = "Unexpected error log lines:\n" + "\n".join(errors)
        raise AssertionError(msg)


def assert_dict_subset(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    """Assert that all keys in *expected* exist in *actual* with the same values."""
    missing = {}
    wrong = {}
    for key, expected_val in expected.items():
        if key not in actual:
            missing[key] = expected_val
        elif actual[key] != expected_val:
            wrong[key] = {"expected": expected_val, "actual": actual[key]}
    if missing or wrong:
        parts = []
        if missing:
            parts.append(f"Missing keys: {missing}")
        if wrong:
            parts.append(f"Wrong values: {wrong}")
        raise AssertionError("dict is not a superset of expected:\n" + "\n".join(parts))


def assert_valid_uuid(value: Any) -> None:
    """Assert that *value* is a valid UUID string or UUID object."""
    try:
        uuid.UUID(str(value))
    except (ValueError, AttributeError) as exc:
        msg = f"Expected a valid UUID, got: {value!r}"
        raise AssertionError(msg) from exc


def assert_utc_datetime(value: Any) -> None:
    """Assert that *value* is a UTC-aware datetime or ISO8601 string with UTC offset."""
    from datetime import datetime

    if isinstance(value, datetime):
        if value.tzinfo is None:
            msg = f"Expected UTC-aware datetime, got naive: {value!r}"
            raise AssertionError(msg)
        return
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                msg = f"Expected UTC-aware ISO8601, got naive: {value!r}"
                raise AssertionError(msg)
            return
        except ValueError as exc:
            msg = f"Not a valid ISO8601 datetime: {value!r}"
            raise AssertionError(msg) from exc
    msg = f"Expected datetime or ISO8601 string, got: {type(value).__name__}"
    raise AssertionError(msg)


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


async def wait_for_condition(
    predicate: Callable[[], bool | Any],
    *,
    timeout: float = 10.0,
    interval: float = 0.1,
    message: str = "Condition not met within timeout",
) -> None:
    """Poll *predicate* until it returns truthy or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError(f"{message} (waited {timeout}s)")


async def wait_for_redis_key(
    redis: Any,
    key: str,
    *,
    timeout: float = 10.0,
    interval: float = 0.1,
) -> str:
    """Wait until a Redis key exists and return its value."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        val = await redis.get(key)
        if val is not None:
            return val  # type: ignore[return-value]
        await asyncio.sleep(interval)
    msg = f"Redis key {key!r} did not appear within {timeout}s"
    raise AssertionError(msg)


async def eventually(
    coro_factory: Callable[[], Any],
    *,
    timeout: float = 10.0,
    interval: float = 0.2,
    ignore_exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Any:
    """Retry an async coroutine factory until it succeeds or timeout."""
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return await coro_factory()
        except ignore_exceptions as exc:
            last_exc = exc
            await asyncio.sleep(interval)
    if last_exc:
        raise last_exc
    msg = "eventually() timed out with no exception (unreachable)"
    raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Data normalisation helpers
# ---------------------------------------------------------------------------

_TS_KEYS = frozenset({
    "created_at", "updated_at", "scraped_at", "published_at",
    "started_at", "finished_at", "computed_at", "timestamp",
})

_UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


def strip_timestamps(data: dict[str, Any]) -> dict[str, Any]:
    """Remove timestamp fields from a dict for stable snapshot comparisons."""
    return {k: v for k, v in data.items() if k not in _TS_KEYS}


def normalize_uuids(data: dict[str, Any]) -> dict[str, Any]:
    """Replace all UUID-looking string values with a fixed placeholder."""
    result = {}
    for k, v in data.items():
        if isinstance(v, str) and _UUID_PATTERN.fullmatch(v):
            result[k] = "00000000-0000-0000-0000-000000000000"
        elif isinstance(v, dict):
            result[k] = normalize_uuids(v)
        else:
            result[k] = v
    return result


# ---------------------------------------------------------------------------
# Marker decorators
# ---------------------------------------------------------------------------


def _make_skip_marker(env_var: str, service_name: str) -> Any:
    """Factory for service-availability skip markers."""
    import os

    import pytest

    def decorator(fn: Any) -> Any:
        available = os.getenv(env_var, "0").strip().lower() in ("1", "true", "yes")
        return pytest.mark.skipif(
            not available,
            reason=f"{service_name} not available (set {env_var}=1 to enable)",
        )(fn)

    return decorator


def requires_postgres(fn: Any) -> Any:
    """Skip test if AEGIS_TEST_POSTGRES=1 is not set."""
    return _make_skip_marker("AEGIS_TEST_POSTGRES", "PostgreSQL")(fn)


def requires_redis(fn: Any) -> Any:
    """Skip test if AEGIS_TEST_REDIS=1 is not set."""
    return _make_skip_marker("AEGIS_TEST_REDIS", "Redis")(fn)


def requires_minio(fn: Any) -> Any:
    """Skip test if AEGIS_TEST_MINIO=1 is not set."""
    return _make_skip_marker("AEGIS_TEST_MINIO", "MinIO")(fn)


def requires_ollama(fn: Any) -> Any:
    """Skip test if AEGIS_TEST_OLLAMA=1 is not set."""
    return _make_skip_marker("AEGIS_TEST_OLLAMA", "Ollama")(fn)


def requires_gpu(fn: Any) -> Any:
    """Skip test if CUDA GPU is not available."""

    def decorator(fn2: Any) -> Any:
        try:
            import torch  # type: ignore[import-untyped]
            has_cuda = torch.cuda.is_available()
        except ImportError:
            has_cuda = False
        import pytest
        return pytest.mark.skipif(
            not has_cuda,
            reason="CUDA GPU not available",
        )(fn2)

    return decorator(fn)


# ---------------------------------------------------------------------------
# Error code helpers
# ---------------------------------------------------------------------------

_ERROR_CODE_PATTERN = re.compile(r"AEGIS-[A-Z]+-\d{4}")

# Exposed as a constant for tests that need to know if pandera is installed
try:
    import pandera  # type: ignore[import-untyped]  # noqa: F401
    PANDERA_AVAILABLE = True
except ImportError:
    PANDERA_AVAILABLE = False


def aegis_error_code(phase: str, number: int) -> str:
    """Construct a canonical AEGIS error code string.

    Args:
        phase: Phase abbreviation in UPPER_SNAKE (e.g. "SCRAPE", "PREDICT").
        number: Zero-padded 4-digit error number.

    Returns:
        Error code string, e.g. ``"AEGIS-PREDICT-0042"``.

    Example::

        >>> aegis_error_code("PREDICT", 42)
        'AEGIS-PREDICT-0042'
    """
    return f"AEGIS-{phase.upper()}-{number:04d}"


def is_aegis_error(exc: Exception, *, phase: str | None = None) -> bool:
    """Return True if *exc* message contains an AEGIS error code.

    Args:
        exc: The exception to inspect.
        phase: If given, also check the phase prefix (e.g. "PREDICT").
    """
    msg = str(exc)
    if not _ERROR_CODE_PATTERN.search(msg):
        return False
    return not (phase and f"AEGIS-{phase.upper()}-" not in msg)
