"""Tests for core/logging.py, core/metrics.py, and scrape/base.py run loop."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from aegis.core.logging import (
    LOG_SCRUB_KEYS,
    _redact_string,
    _scrub_secrets,
    _utc_timestamp,
    bind_request_context,
    clear_request_context,
    configure_logging,
    flush_if_possible,
    get_logger,
    is_configured,
)
from aegis.schemas.enums import (
    ContentModality,
    IntentType,
    Platform,
    ScrapeMethod,
    SourceTier,
    ToSRisk,
)
from aegis.schemas.signal import (
    ConfidenceMetadata,
    ProductSignal,
    ScrapeProvenance,
    compute_content_hash,
)
from aegis.scrape.base import AdapterConfig, ScrapeContext, SourceAdapter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# ---------------------------------------------------------------------------
# Logging — pure processors
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_utc_timestamp_adds_key():
    event = {"event": "test"}
    result = _utc_timestamp(None, "info", event)
    assert "timestamp" in result
    assert "T" in result["timestamp"]  # ISO-8601 with T


@pytest.mark.unit
def test_scrub_secrets_redacts_key():
    event = {"event": "test", "password": "supersecret"}
    result = _scrub_secrets(None, "info", event)
    assert result["password"] == "***"


@pytest.mark.unit
def test_scrub_secrets_redacts_nested_dict():
    event = {"event": "test", "auth_data": {"token": "abc123", "user": "alice"}}
    result = _scrub_secrets(None, "info", event)
    assert result["auth_data"]["token"] == "***"
    assert result["auth_data"]["user"] == "alice"


@pytest.mark.unit
def test_scrub_secrets_leaves_safe_keys():
    event = {"event": "login_attempt", "user_id": "usr123", "ip": "1.2.3.4"}
    result = _scrub_secrets(None, "info", event)
    assert result["user_id"] == "usr123"
    assert result["ip"] == "1.2.3.4"


@pytest.mark.unit
def test_redact_string_bearer_token():
    s = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.secret.sig"
    result = _redact_string(s)
    assert "eyJhbGci" not in result
    assert "***" in result


@pytest.mark.unit
def test_redact_string_inline_kv_password():
    s = "user=alice password=hunter2 host=db"
    result = _redact_string(s)
    assert "hunter2" not in result
    assert "***" in result


@pytest.mark.unit
def test_redact_string_too_short_unchanged():
    s = "abc"
    assert _redact_string(s) == "abc"


@pytest.mark.unit
def test_scrub_keys_contains_expected():
    assert "password" in LOG_SCRUB_KEYS
    assert "token" in LOG_SCRUB_KEYS
    assert "api_key" in LOG_SCRUB_KEYS


@pytest.mark.unit
def test_configure_logging_runs_without_error():
    configure_logging(level="WARNING", json_output=False)
    assert is_configured()


@pytest.mark.unit
def test_configure_logging_idempotent():
    configure_logging(level="DEBUG", json_output=False)
    configure_logging(level="INFO", json_output=False)
    assert is_configured()


@pytest.mark.unit
def test_get_logger_returns_bound_logger():
    log = get_logger("test.module")
    assert log is not None


@pytest.mark.unit
def test_bind_clear_request_context():
    bind_request_context(request_id="req-1", tenant="t1")
    clear_request_context()  # should not raise


@pytest.mark.unit
def test_flush_if_possible():
    flush_if_possible()  # should not raise


# ---------------------------------------------------------------------------
# scrape/base.py — run loop with a minimal concrete adapter
# ---------------------------------------------------------------------------


_NOW = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)


def _make_signal(external_id: str, platform: Platform = Platform.HACKER_NEWS) -> ProductSignal:
    h = compute_content_hash(
        platform=platform,
        external_id=external_id,
        url=f"https://news.ycombinator.com/item?id={external_id}",
        title=f"Test post {external_id}",
        raw_text=None,
        posted_at=None,
    )
    return ProductSignal(
        platform=platform,
        tier=SourceTier.TIER_5_ALTERNATIVE,
        external_id=external_id,
        url=f"https://news.ycombinator.com/item?id={external_id}",
        title=f"Test post {external_id}",
        raw_text=None,
        modality=ContentModality.TEXT,
        tags=frozenset(),
        intent=IntentType.UNKNOWN,
        provenance=ScrapeProvenance(
            method=ScrapeMethod.OFFICIAL_API,
            scraped_at=_NOW,
            scraper_version="test-0.1.0",
            tos_risk=ToSRisk.GREEN,
        ),
        confidence=ConfidenceMetadata(completeness=0.8, source_confidence=0.9),
        content_hash=h,
    )


class _MockAdapter(SourceAdapter[str]):
    """Minimal test adapter. Yields string IDs, parses them into ProductSignals."""

    def __init__(
        self, items: list[str], *, config: AdapterConfig | None = None, fail_parse: bool = False
    ) -> None:
        cfg = config or AdapterConfig(name="mock")
        super().__init__(cfg)
        self._items = items
        self._fail_parse = fail_parse
        self.setup_called = False
        self.teardown_called = False

    @property
    def name(self) -> str:
        return "mock"

    async def setup(self, ctx: ScrapeContext) -> None:
        self.setup_called = True

    async def teardown(self, ctx: ScrapeContext) -> None:
        self.teardown_called = True

    async def fetch_raw(self, ctx: ScrapeContext, **_: Any) -> AsyncIterator[str]:
        for item in self._items:
            yield item

    def parse(self, raw: str, ctx: ScrapeContext) -> ProductSignal | None:
        if self._fail_parse:
            raise RuntimeError("parse intentionally failed")
        if raw == "skip":
            return None
        return _make_signal(raw)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_adapter_run_yields_signals():
    adapter = _MockAdapter(["1", "2", "3"])
    signals = [s async for s in adapter.run()]
    assert len(signals) == 3
    assert all(isinstance(s, ProductSignal) for s in signals)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_adapter_run_setup_teardown_called():
    adapter = _MockAdapter(["1"])
    _ = [s async for s in adapter.run()]
    assert adapter.setup_called
    assert adapter.teardown_called


@pytest.mark.unit
@pytest.mark.asyncio
async def test_adapter_run_skips_none_from_parse():
    adapter = _MockAdapter(["1", "skip", "2"])
    signals = [s async for s in adapter.run()]
    assert len(signals) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_adapter_run_parse_error_continues():
    adapter = _MockAdapter(["1", "fail-item", "3"], fail_parse=True)
    # All items fail to parse — should yield empty, not crash
    signals = [s async for s in adapter.run()]
    assert signals == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_adapter_run_cancel_event():
    cancel = asyncio.Event()
    adapter = _MockAdapter(["1", "2", "3", "4", "5"], config=AdapterConfig(name="mock"))

    # Cancelling after first signal
    collected = []
    async for signal in adapter.run():
        collected.append(signal)
        if len(collected) == 1:
            cancel.set()
            # Replace the adapter's cancel event
            adapter._cancel_event = cancel
            break

    # Only 1 signal should have been collected
    assert len(collected) >= 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_adapter_run_empty_yields_nothing():
    adapter = _MockAdapter([])
    signals = [s async for s in adapter.run()]
    assert signals == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_adapter_is_cancelled_initially_false():
    adapter = _MockAdapter([])
    assert adapter.is_cancelled is False


@pytest.mark.unit
def test_adapter_cancel():
    adapter = _MockAdapter([])
    adapter.cancel()
    assert adapter.is_cancelled is True


# ---------------------------------------------------------------------------
# core/metrics.py
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_metrics_import_ok():
    from aegis.core.metrics import (
        cache_ops_total,
        ingest_errors_total,
        ingest_latency_seconds,
        ingest_signals_total,
        scrape_proxy_ban_total,
    )

    assert ingest_signals_total is not None
    assert ingest_errors_total is not None
    assert ingest_latency_seconds is not None
    assert scrape_proxy_ban_total is not None
    assert cache_ops_total is not None


@pytest.mark.unit
def test_metrics_reset_registry():
    from aegis.core.metrics import reset_registry

    reset_registry()  # should not raise


@pytest.mark.unit
def test_metrics_ingest_signals_total_inc():
    from aegis.core.metrics import ingest_signals_total

    # Incrementing a metric counter should not raise
    ingest_signals_total.labels(platform="test", tier="T5").inc()


@pytest.mark.unit
def test_metrics_ingest_errors_total_inc():
    from aegis.core.metrics import ingest_errors_total

    ingest_errors_total.labels(platform="test", category="parse").inc()


@pytest.mark.unit
def test_metrics_ingest_latency_observe():
    from aegis.core.metrics import ingest_latency_seconds

    ingest_latency_seconds.labels(platform="test").observe(0.5)
