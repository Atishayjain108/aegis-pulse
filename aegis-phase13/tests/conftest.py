"""
conftest.py — AEGIS Pulse Phase 13: Master Test Configuration & Shared Fixtures.

This is the top-level conftest.py.  Every test in tests/ can access these fixtures
without importing.  Sub-conftest files in unit/, integration/, e2e/, perf/ add
scope-specific fixtures on top.

Architecture position:
  Phase 13 (Testing) → consumes schemas/constants from Phases 0-11 without
  importing implementation details; stubs/mocks live here, real impls in integration/.

Design rules:
  - Every fixture is typed.
  - Every async fixture uses anyio backend="asyncio".
  - No fixture touches the real filesystem under /home unless explicitly in tmp_path.
  - Infra fixtures (pg, redis) are session-scoped in integration tests only;
    unit tests always receive in-memory stubs.
  - All datetimes are UTC-aware.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
import json
from typing import Any
from unittest.mock import MagicMock
import uuid

from faker import Faker
from pydantic import BaseModel
import pytest
import structlog

# ---------------------------------------------------------------------------
# Logging: capture structlog output during tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_structlog() -> Generator[None, None, None]:
    """Ensure structlog is configured in test mode (plain text, no timestamps)."""
    structlog.reset_defaults()
    structlog.configure(
        processors=[structlog.dev.ConsoleRenderer(colors=False)],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
    yield
    structlog.reset_defaults()


# ---------------------------------------------------------------------------
# Faker seed — deterministic fake data
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def fake() -> Faker:
    """Session-scoped Faker with fixed seed for reproducibility."""
    f = Faker(["en_US", "en_IN"])
    Faker.seed(42)
    return f


# ---------------------------------------------------------------------------
# UTC datetime helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def utcnow() -> datetime:
    """Current UTC datetime (timezone-aware)."""
    return datetime.now(tz=UTC)


@pytest.fixture()
def yesterday() -> datetime:
    """Yesterday at midnight UTC."""
    return (
        datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        - timedelta(days=1)
    )


# ---------------------------------------------------------------------------
# Tenant / identity fixtures
# ---------------------------------------------------------------------------

DEV_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture(scope="session")
def dev_tenant_id() -> uuid.UUID:
    """The standard dev tenant UUID used across all AEGIS phases."""
    return DEV_TENANT_ID


@pytest.fixture()
def random_tenant_id() -> uuid.UUID:
    """A random tenant UUID for isolation tests."""
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# Signal / TrendCandidate factories
# ---------------------------------------------------------------------------

def _make_raw_signal(
    *,
    platform: str = "hacker_news",
    title: str | None = None,
    score: float = 0.75,
    tenant_id: uuid.UUID | None = None,
    fake_inst: Faker | None = None,
) -> dict[str, Any]:
    """Build a minimal raw signal dict matching the canonical schema."""
    f = fake_inst or Faker()
    return {
        "id": str(uuid.uuid4()),
        "tenant_id": str(tenant_id or DEV_TENANT_ID),
        "platform": platform,
        "external_id": f.uuid4(),
        "title": title or f.sentence(nb_words=8),
        "url": f.url(),
        "author": f.user_name(),
        "score": score,
        "views": f.random_int(100, 50000),
        "likes": f.random_int(10, 5000),
        "comments": f.random_int(0, 500),
        "shares": f.random_int(0, 1000),
        "saves": f.random_int(0, 200),
        "content_hash": f.sha256(),
        "scraped_at": datetime.now(tz=UTC).isoformat(),
        "published_at": (datetime.now(tz=UTC) - timedelta(hours=2)).isoformat(),
        "raw_json": json.dumps({"extra": "data"}),
    }


@pytest.fixture()
def raw_signal(fake: Faker) -> dict[str, Any]:
    """A single raw signal dict."""
    return _make_raw_signal(fake_inst=fake)


@pytest.fixture()
def raw_signal_batch(fake: Faker) -> list[dict[str, Any]]:
    """A batch of 20 raw signals across 3 platforms."""
    platforms = ["hacker_news", "reddit_rss", "github_trending"]
    return [
        _make_raw_signal(
            platform=platforms[i % 3],
            score=round(0.5 + (i % 5) * 0.1, 2),
            fake_inst=fake,
        )
        for i in range(20)
    ]


def _make_trend_candidate(
    *,
    trend_id: str | None = None,
    title: str | None = None,
    signal_count: int = 15,
    velocity_24h: float = 1.5,
    sentiment: float = 0.6,
    commercial_intent: float = 0.7,
    novelty: float = 0.8,
    coordination_risk: float = 0.1,
) -> dict[str, Any]:
    """Build a TrendCandidate-compatible dict (matches Pydantic v2 frozen model)."""
    return {
        "trend_id": trend_id or f"trend-{uuid.uuid4().hex[:8]}",
        "title": title or "AI-powered inventory management startup",
        "signal_count": signal_count,
        "unique_authors": max(1, signal_count // 3),
        "platforms": ["hacker_news", "reddit_rss"],
        "velocity_1h": velocity_24h / 24,
        "velocity_6h": velocity_24h / 4,
        "velocity_24h": velocity_24h,
        "sentiment": sentiment,
        "commercial_intent": commercial_intent,
        "novelty": novelty,
        "coordination_risk": coordination_risk,
    }


@pytest.fixture()
def trend_candidate() -> dict[str, Any]:
    """A single TrendCandidate-compatible dict."""
    return _make_trend_candidate()


@pytest.fixture()
def high_confidence_trend() -> dict[str, Any]:
    """A trend with metrics that should pass confidence gates."""
    return _make_trend_candidate(
        signal_count=50,
        velocity_24h=8.5,
        sentiment=0.82,
        commercial_intent=0.91,
        novelty=0.88,
        coordination_risk=0.05,
    )


@pytest.fixture()
def low_confidence_trend() -> dict[str, Any]:
    """A trend that should fail confidence gates (score below 0.85)."""
    return _make_trend_candidate(
        signal_count=3,
        velocity_24h=0.2,
        sentiment=0.3,
        commercial_intent=0.2,
        novelty=0.15,
        coordination_risk=0.75,
    )


# ---------------------------------------------------------------------------
# GraphResult / AgentDecision factories
# ---------------------------------------------------------------------------

def _make_agent_decision(
    *,
    node_name: str = "scout",
    verdict: str = "proceed",
    score: float = 0.82,
    confidence: float = 0.75,
) -> dict[str, Any]:
    return {
        "node_name": node_name,
        "verdict": verdict,
        "score": score,
        "confidence": confidence,
        "reasoning": f"[{node_name}] Automated test decision.",
        "metadata": {},
        "timestamp": datetime.now(tz=UTC).isoformat(),
    }


@pytest.fixture()
def agent_decision() -> dict[str, Any]:
    """A single AgentDecision-compatible dict."""
    return _make_agent_decision()


@pytest.fixture()
def graph_result(trend_candidate: dict[str, Any]) -> dict[str, Any]:
    """A complete GraphResult-compatible dict for the trend_candidate fixture."""
    nodes = ["scout", "sentinel", "analyst", "validator", "strategist",
             "overseer", "archivist", "herald", "auditor", "supervisor"]
    return {
        "trend_id": trend_candidate["trend_id"],
        "final_verdict": "proceed",
        "final_score": 0.82,
        "final_confidence": 0.79,
        "final_priority": 1,
        "halt_reason": None,
        "blocked_by": None,
        "decisions": [_make_agent_decision(node_name=n) for n in nodes],
        "started_at": datetime.now(tz=UTC).isoformat(),
        "finished_at": datetime.now(tz=UTC).isoformat(),
        "duration_ms": 142,
        "raw_verdict": "proceed",
        "data_confidence": 0.91,
    }


# ---------------------------------------------------------------------------
# FeatureWindow factory (Phase 3)
# ---------------------------------------------------------------------------

FEATURE_DIM = 20  # matches aegis.predict.FEATURE_DIM


def _get_feature_names() -> tuple[str, ...]:
    """Return the canonical FEATURE_NAMES from aegis.predict, or fallback stubs."""
    try:
        from aegis.predict import FEATURE_NAMES  # type: ignore[import-untyped]
        return FEATURE_NAMES
    except ImportError:
        return tuple(f"feat_{i:02d}" for i in range(FEATURE_DIM))


@pytest.fixture()
def feature_window(trend_candidate: dict[str, Any]) -> dict[str, Any]:
    """A FeatureWindow-compatible dict for Phase 3 predict tests.

    Uses actual FeatureWindow field names:
      values (not features), captured_at (not computed_at),
      window_size + feature_dim + correlation_id are required.
    """
    import random
    rng = random.Random(42)
    return {
        "trend_id": trend_candidate["trend_id"],
        "tenant_id": str(DEV_TENANT_ID),
        "correlation_id": str(uuid.uuid4()),
        "window_size": 1,
        "feature_dim": FEATURE_DIM,
        "values": [round(rng.uniform(-1.0, 1.0), 4) for _ in range(FEATURE_DIM)],
        "feature_names": list(_get_feature_names()),
        "captured_at": datetime.now(tz=UTC),
    }


# ---------------------------------------------------------------------------
# Redis mock fixtures (unit tests — no real Redis)
# ---------------------------------------------------------------------------

class _FakeRedis:
    """In-memory Redis stub — no network required."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}
        self._streams: dict[str, list[dict[str, Any]]] = {}
        self._expiries: dict[str, datetime] = {}

    async def get(self, key: str) -> str | None:
        exp = self._expiries.get(key)
        if exp and datetime.now(tz=UTC) > exp:
            self._store.pop(key, None)
            return None
        return self._store.get(key)

    async def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        if nx and key in self._store:
            return False
        self._store[key] = value
        if ex:
            self._expiries[key] = datetime.now(tz=UTC) + timedelta(seconds=ex)
        return True

    async def delete(self, *keys: str) -> int:
        removed = sum(1 for k in keys if k in self._store)
        for k in keys:
            self._store.pop(k, None)
        return removed

    async def exists(self, *keys: str) -> int:
        return sum(1 for k in keys if k in self._store)

    async def hset(self, name: str, mapping: dict[str, Any]) -> int:
        if name not in self._store:
            self._store[name] = {}
        self._store[name].update(mapping)
        return len(mapping)

    async def hget(self, name: str, key: str) -> Any:
        return self._store.get(name, {}).get(key)

    async def hgetall(self, name: str) -> dict[str, Any]:
        return dict(self._store.get(name, {}))

    async def xadd(self, name: str, fields: dict[str, Any]) -> str:
        if name not in self._streams:
            self._streams[name] = []
        entry_id = f"{int(datetime.now(tz=UTC).timestamp() * 1000)}-0"
        self._streams[name].append({"id": entry_id, **fields})
        return entry_id

    async def xlen(self, name: str) -> int:
        return len(self._streams.get(name, []))

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        pass

    def pipeline(self) -> _FakeRedisPipeline:
        return _FakeRedisPipeline(self)


class _FakeRedisPipeline:
    """Minimal pipeline stub."""

    def __init__(self, redis: _FakeRedis) -> None:
        self._redis = redis
        self._queue: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def set(self, key: str, value: str, **kwargs: Any) -> _FakeRedisPipeline:
        self._queue.append(("set", (key, value), kwargs))
        return self

    def delete(self, *keys: str) -> _FakeRedisPipeline:
        self._queue.append(("delete", keys, {}))
        return self

    async def execute(self) -> list[Any]:
        results = []
        for method, args, kwargs in self._queue:
            fn = getattr(self._redis, method)
            results.append(await fn(*args, **kwargs))
        self._queue.clear()
        return results

    async def __aenter__(self) -> _FakeRedisPipeline:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


@pytest.fixture()
def fake_redis() -> _FakeRedis:
    """In-memory Redis stub — no network required."""
    return _FakeRedis()


# ---------------------------------------------------------------------------
# PostgreSQL mock fixtures (unit tests)
# ---------------------------------------------------------------------------

class _FakeAsyncpgRecord:
    """Minimal asyncpg Record stub."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def keys(self) -> list[str]:
        return list(self._data.keys())

    def items(self) -> list[tuple[str, Any]]:
        return list(self._data.items())


class _FakeAsyncpgConnection:
    """Minimal asyncpg connection stub for unit tests."""

    def __init__(self) -> None:
        self._executed: list[tuple[str, tuple[Any, ...]]] = []
        self._fetchall_result: list[_FakeAsyncpgRecord] = []
        self._fetchone_result: _FakeAsyncpgRecord | None = None

    def set_fetchall(self, rows: list[dict[str, Any]]) -> None:
        self._fetchall_result = [_FakeAsyncpgRecord(r) for r in rows]

    def set_fetchone(self, row: dict[str, Any] | None) -> None:
        self._fetchone_result = _FakeAsyncpgRecord(row) if row else None

    async def execute(self, query: str, *args: Any) -> str:
        self._executed.append((query, args))
        return "OK"

    async def fetch(self, query: str, *args: Any) -> list[_FakeAsyncpgRecord]:
        self._executed.append((query, args))
        return self._fetchall_result

    async def fetchrow(self, query: str, *args: Any) -> _FakeAsyncpgRecord | None:
        self._executed.append((query, args))
        return self._fetchone_result

    async def fetchval(self, query: str, *args: Any) -> Any:
        self._executed.append((query, args))
        if self._fetchone_result:
            vals = list(self._fetchone_result._data.values())
            return vals[0] if vals else None
        return None

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()

    @property
    def executed_queries(self) -> list[str]:
        return [q for q, _ in self._executed]


class _FakeTransaction:
    async def __aenter__(self) -> _FakeTransaction:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


class _FakeAsyncpgPool:
    """Minimal asyncpg pool stub."""

    def __init__(self) -> None:
        self._conn = _FakeAsyncpgConnection()

    @property
    def connection(self) -> _FakeAsyncpgConnection:
        return self._conn

    def acquire(self) -> _PoolContextManager:
        return _PoolContextManager(self._conn)

    async def close(self) -> None:
        pass


class _PoolContextManager:
    def __init__(self, conn: _FakeAsyncpgConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeAsyncpgConnection:
        return self._conn

    async def __aexit__(self, *_: Any) -> None:
        pass


@pytest.fixture()
def fake_pg_pool() -> _FakeAsyncpgPool:
    """In-memory asyncpg pool stub — no DB required."""
    return _FakeAsyncpgPool()


@pytest.fixture()
def fake_pg_conn(fake_pg_pool: _FakeAsyncpgPool) -> _FakeAsyncpgConnection:
    """Direct access to the connection inside the fake pool."""
    return fake_pg_pool.connection


# ---------------------------------------------------------------------------
# LLM Gateway mock (unit tests — no Ollama/cloud required)
# ---------------------------------------------------------------------------

class _FakeLLMGateway:
    """Stub LLMGateway that returns deterministic responses without any provider."""

    DEFAULT_RESPONSE = (
        '{"verdict": "proceed", "score": 0.82, "reasoning": "Test stub response."}'
    )

    def __init__(self, response: str | None = None) -> None:
        self._response = response or self.DEFAULT_RESPONSE
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1000,
        **kwargs: Any,
    ) -> BaseModel:
        self.calls.append({"messages": messages, "model": model, "temperature": temperature})
        # Return a minimal LLMResponse-compatible object
        return _FakeLLMResponse(content=self._response)

    async def health(self) -> dict[str, Any]:
        return {"status": "ok", "providers": []}

    async def aclose(self) -> None:
        pass


class _FakeLLMResponse:
    def __init__(self, content: str) -> None:
        self.content = content
        self.provider = "stub"
        self.model = "stub-model"
        self.usage = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
        self.latency_ms = 5.0


@pytest.fixture()
def fake_llm_gateway() -> _FakeLLMGateway:
    """LLM gateway stub — returns deterministic JSON without any network call."""
    return _FakeLLMGateway()


@pytest.fixture()
def failing_llm_gateway() -> _FakeLLMGateway:
    """LLM gateway that always raises RuntimeError — tests circuit breaker paths."""

    class _AlwaysFail(_FakeLLMGateway):
        async def complete(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
            msg = "AEGIS-LLM-0001: All providers failed (stub)"
            raise RuntimeError(msg)

    return _AlwaysFail()


# ---------------------------------------------------------------------------
# MinIO / object-store mock (unit tests)
# ---------------------------------------------------------------------------

class _FakeMinIOClient:
    """In-memory MinIO/S3 stub for unit tests."""

    def __init__(self) -> None:
        self._buckets: dict[str, dict[str, bytes]] = {}

    def bucket_exists(self, bucket: str) -> bool:
        return bucket in self._buckets

    def make_bucket(self, bucket: str) -> None:
        self._buckets.setdefault(bucket, {})

    def put_object(
        self,
        bucket: str,
        object_name: str,
        data: Any,
        length: int,
        content_type: str = "application/octet-stream",
    ) -> MagicMock:
        raw = data.read() if hasattr(data, "read") else bytes(data)
        self._buckets.setdefault(bucket, {})[object_name] = raw
        result = MagicMock()
        result.object_name = object_name
        return result

    def get_object(self, bucket: str, object_name: str) -> Any:
        import io
        data = self._buckets.get(bucket, {}).get(object_name)
        if data is None:
            msg = f"Object not found: {bucket}/{object_name}"
            raise KeyError(msg)
        return io.BytesIO(data)

    def list_objects(self, bucket: str, prefix: str = "") -> list[MagicMock]:
        objects = []
        for name in self._buckets.get(bucket, {}):
            if name.startswith(prefix):
                obj = MagicMock()
                obj.object_name = name
                objects.append(obj)
        return objects

    def remove_object(self, bucket: str, object_name: str) -> None:
        self._buckets.get(bucket, {}).pop(object_name, None)

    def stat_object(self, bucket: str, object_name: str) -> MagicMock:
        if object_name not in self._buckets.get(bucket, {}):
            msg = f"Object not found: {bucket}/{object_name}"
            raise KeyError(msg)
        stat = MagicMock()
        stat.size = len(self._buckets[bucket][object_name])
        return stat


@pytest.fixture()
def fake_minio() -> _FakeMinIOClient:
    """In-memory MinIO stub — no docker required for unit tests."""
    client = _FakeMinIOClient()
    client.make_bucket("aegis-signals")
    client.make_bucket("aegis-models")
    client.make_bucket("aegis-datalake")
    client.make_bucket("aegis-backups")
    return client


# ---------------------------------------------------------------------------
# HTTP client mock (respx-based for httpx calls)
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_httpx(respx_mock: Any) -> Any:
    """Alias for the respx_mock fixture — cleaner naming in tests."""
    return respx_mock


# ---------------------------------------------------------------------------
# Clock / time fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def frozen_time() -> Generator[datetime, None, None]:
    """Freeze time at a known UTC datetime for deterministic tests."""
    import time_machine
    fixed = datetime(2026, 5, 27, 9, 0, 0, tzinfo=UTC)
    with time_machine.travel(fixed, tick=False):
        yield fixed


# ---------------------------------------------------------------------------
# Env variable patching helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure test environment variables are set to safe test defaults."""
    monkeypatch.setenv("AEGIS_ENV", "test")
    monkeypatch.setenv("AEGIS_DISABLE_OLLAMA", "1")
    monkeypatch.setenv("AEGIS_AGENT_HMAC_KEY", "test-key-do-not-use-in-prod")
    monkeypatch.setenv("AEGIS_DEFAULT_TENANT_ID", str(DEV_TENANT_ID))
    monkeypatch.setenv("AEGIS_EXECUTE_MODE", "advisory")
    monkeypatch.setenv("AEGIS_ENABLE_HARDEN", "false")


# ---------------------------------------------------------------------------
# Async event-loop policy for test isolation
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def event_loop_policy() -> asyncio.DefaultEventLoopPolicy:
    """Session-scoped event loop policy (required by pytest-asyncio ≥ 0.24)."""
    return asyncio.DefaultEventLoopPolicy()


# ---------------------------------------------------------------------------
# Phase 4 alert factories
# ---------------------------------------------------------------------------

def _make_alert_envelope(
    *,
    trend_id: str | None = None,
    verdict: str = "ENTER",
    score: float = 0.82,
    confidence: float = 0.75,
    tenant_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Build a nested AlertEnvelope-compatible dict matching actual Phase 4 schemas.

    AlertEnvelope wraps Alert: { envelope_version, alert: Alert, signature_hex }.
    Alert requires: alert_id, tenant_id, trend_id, verdict, priority, score, confidence, source, title.
    """
    alert_data = {
        "alert_id": str(uuid.uuid4()),
        "trend_id": trend_id or f"trend-{uuid.uuid4().hex[:8]}",
        "tenant_id": str(tenant_id or DEV_TENANT_ID),
        "verdict": verdict,
        "score": score,
        "confidence": confidence,
        "priority": 1,
        "source": "phase2_only",
        "title": "Test alert: AI-powered logistics",
    }
    # BLOCK verdict requires halt_reason or blocked_by per Alert.model_validator
    if verdict == "BLOCK":
        alert_data["halt_reason"] = "test block signal"
    return {
        "alert": alert_data,
        "signature_hex": None,
    }


@pytest.fixture()
def alert_envelope() -> dict[str, Any]:
    """A single AlertEnvelope-compatible dict."""
    return _make_alert_envelope()


@pytest.fixture()
def enter_alert() -> dict[str, Any]:
    return _make_alert_envelope(verdict="ENTER", score=0.91, confidence=0.88)


@pytest.fixture()
def block_alert() -> dict[str, Any]:
    return _make_alert_envelope(verdict="BLOCK", score=0.15, confidence=0.92)


# ---------------------------------------------------------------------------
# Hypothesis profiles
# ---------------------------------------------------------------------------

from hypothesis import HealthCheck, settings  # noqa: E402

settings.register_profile(
    "ci",
    max_examples=50,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    deadline=5000,
)
settings.register_profile(
    "dev",
    max_examples=20,
    suppress_health_check=[HealthCheck.too_slow],
    deadline=2000,
)
settings.register_profile(
    "thorough",
    max_examples=500,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    deadline=None,
)

import os  # noqa: E402

settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "dev"))
