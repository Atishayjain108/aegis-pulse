"""
Unit tests for the Stage-2 data-machine watchdog.

Mocked asyncpg pool only — no live DB. The fake conn answers the two count
queries by SQL shape (same style as the settlement-loop test fakes), and the
ntfy sender is monkeypatched at the seam ``watchdog._send_page`` so no test
ever performs network I/O.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from aegis.scheduler import watchdog as wd

# ---------------------------------------------------------------------------
# Fake pool
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self, signals_n: int, settled_n: int, *, explode: bool = False):
        self._signals_n = signals_n
        self._settled_n = settled_n
        self._explode = explode
        self.tenant_sets = 0

    async def execute(self, sql: str, *args):
        if "set_config" in sql:
            self.tenant_sets += 1

    async def fetchrow(self, sql: str, *args):
        if self._explode:
            raise ConnectionError("db down")
        if "FROM signals" in sql:
            return {"n": self._signals_n}
        if "FROM signal_outcomes" in sql:
            # The clean-settlement count MUST filter on the quarantine flag —
            # assert the query shape here so a future edit can't silently
            # widen it back to poisoned rows.
            assert "window_scraper_alive = TRUE" in sql
            assert "resolution_status IN ('correct', 'incorrect')" in sql
            return {"n": self._settled_n}
        return None


def _make_pool(signals_n: int, settled_n: int, *, explode: bool = False):
    conn = _FakeConn(signals_n, settled_n, explode=explode)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


@pytest.fixture()
def sent_pages(monkeypatch: pytest.MonkeyPatch) -> list[wd.WatchdogReport]:
    """Capture pages at the module seam; no network, no ntfy env needed."""
    captured: list[wd.WatchdogReport] = []

    async def _fake_send(report: wd.WatchdogReport) -> None:
        captured.append(report)

    monkeypatch.setattr(wd, "_send_page", _fake_send)
    return captured


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthy_machine_no_page(sent_pages) -> None:
    pool, conn = _make_pool(signals_n=87, settled_n=12)
    report = await wd.run_watchdog(pool)
    assert report.healthy
    assert report.alarms == ()
    assert report.paged is False
    assert sent_pages == []
    assert report.signals_24h == 87
    assert report.clean_settled_24h == 12
    # RLS was set before each of the two queries.
    assert conn.tenant_sets == 2
    assert report.checked_at.tzinfo is UTC or report.checked_at.utcoffset().total_seconds() == 0


@pytest.mark.asyncio
async def test_zero_ingest_pages(sent_pages) -> None:
    pool, _ = _make_pool(signals_n=0, settled_n=5)
    report = await wd.run_watchdog(pool)
    assert report.alarms == (wd.ALARM_NO_INGEST,)
    assert report.paged is True
    assert len(sent_pages) == 1


@pytest.mark.asyncio
async def test_zero_clean_settlements_pages(sent_pages) -> None:
    pool, _ = _make_pool(signals_n=40, settled_n=0)
    report = await wd.run_watchdog(pool)
    assert report.alarms == (wd.ALARM_NO_CLEAN_SETTLE,)
    assert report.paged is True
    assert len(sent_pages) == 1


@pytest.mark.asyncio
async def test_both_dead_single_page_names_both(sent_pages) -> None:
    pool, _ = _make_pool(signals_n=0, settled_n=0)
    report = await wd.run_watchdog(pool)
    assert set(report.alarms) == {wd.ALARM_NO_INGEST, wd.ALARM_NO_CLEAN_SETTLE}
    # ONE page, not two.
    assert len(sent_pages) == 1
    title, body = wd._page_body(report)
    assert "ZERO new signals" in body
    assert "ZERO clean settled claims" in body
    # ntfy Title header must survive latin-1 encoding (GATE 1.7 lesson).
    title.encode("latin-1")


@pytest.mark.asyncio
async def test_dry_run_never_sends(sent_pages) -> None:
    pool, _ = _make_pool(signals_n=0, settled_n=0)
    report = await wd.run_watchdog(pool, dry_run=True)
    assert report.alarms  # alarms detected...
    assert report.paged is False  # ...but nothing sent
    assert sent_pages == []


@pytest.mark.asyncio
async def test_db_failure_never_raises_and_pages_unverifiable(sent_pages) -> None:
    """Unverifiable == unhealthy: a dead DB must page, not silently pass."""
    pool, _ = _make_pool(signals_n=0, settled_n=0, explode=True)
    report = await wd.run_watchdog(pool)  # must not raise
    assert report.alarms == (wd.ALARM_UNVERIFIABLE,)
    assert report.signals_24h == -1
    assert report.clean_settled_24h == -1
    assert report.paged is True
    assert len(sent_pages) == 1


@pytest.mark.asyncio
async def test_page_delivery_failure_never_raises(monkeypatch) -> None:
    """The sender itself blowing up must not propagate into the scheduler."""

    async def _boom(report):
        raise RuntimeError("ntfy unreachable")

    monkeypatch.setattr(wd, "_send_page", _boom)
    pool, _ = _make_pool(signals_n=0, settled_n=0)
    report = await wd.run_watchdog(pool)  # must not raise
    assert report.alarms
    assert report.paged is False  # delivery failed → truthfully not paged


@pytest.mark.asyncio
async def test_no_secrets_in_page_body(monkeypatch, sent_pages) -> None:
    """The page content never embeds the ntfy topic or base URL."""
    monkeypatch.setenv("AEGIS_NTFY_TOPIC", "super-secret-topic-xyz")
    monkeypatch.setenv("AEGIS_NTFY_BASE_URL", "https://secret.example")
    pool, _ = _make_pool(signals_n=0, settled_n=3)
    report = await wd.run_watchdog(pool)
    title, body = wd._page_body(report)
    assert "super-secret-topic-xyz" not in title + body
    assert "secret.example" not in title + body


@pytest.mark.asyncio
async def test_scheduler_job_wrapper_never_raises(monkeypatch) -> None:
    """job_watchdog is the scheduler entrypoint — belt over the belt."""
    from aegis.scheduler import autonomous

    async def _boom():
        raise RuntimeError("everything is on fire")

    monkeypatch.setattr(wd, "run_watchdog", _boom)
    await autonomous.job_watchdog()  # must not raise


def test_report_is_frozen() -> None:
    r = wd.WatchdogReport(checked_at=datetime.now(UTC))
    with pytest.raises(Exception):
        r.paged = True  # type: ignore[misc]
