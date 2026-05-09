"""Unit tests for the proxy pool and related utilities."""

from __future__ import annotations

import time
from uuid import UUID, uuid4

import pytest

from aegis.scrape.proxies import (
    DEFAULT_DIRECT_SPEC,
    EmptyPoolError,
    ProxyKind,
    ProxyOutcome,
    ProxyPool,
    ProxySpec,
    ProxyState,
    _sanitise_proxy_url,
    make_proxy_spec,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec(
    url: str = "http://proxy.example.com:3128",
    kind: ProxyKind = ProxyKind.DATACENTER,
    country_code: str | None = "US",
) -> ProxySpec:
    return make_proxy_spec(url, kind=kind, country_code=country_code)


# ---------------------------------------------------------------------------
# _sanitise_proxy_url
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sanitise_removes_credentials():
    result = _sanitise_proxy_url("http://user:password@proxy.example.com:3128")
    assert "password" not in result
    assert "***" in result
    assert "proxy.example.com" in result


@pytest.mark.unit
def test_sanitise_no_credentials_unchanged():
    url = "http://proxy.example.com:3128"
    result = _sanitise_proxy_url(url)
    assert result == url


@pytest.mark.unit
def test_sanitise_socks5():
    result = _sanitise_proxy_url("socks5://u:p@10.0.0.1:1080")
    assert "p@" not in result
    assert "10.0.0.1" in result


# ---------------------------------------------------------------------------
# make_proxy_spec
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_make_proxy_spec_creates_uuid():
    s1 = make_proxy_spec("http://a:8080", kind=ProxyKind.RESIDENTIAL)
    s2 = make_proxy_spec("http://a:8080", kind=ProxyKind.RESIDENTIAL)
    assert s1.proxy_id != s2.proxy_id  # fresh UUID each call


@pytest.mark.unit
def test_make_proxy_spec_fields():
    s = make_proxy_spec(
        "socks5://192.168.1.1:9050",
        kind=ProxyKind.TOR,
        country_code="DE",
        sticky=True,
        notes="tor exit",
    )
    assert s.kind == ProxyKind.TOR
    assert s.country_code == "DE"
    assert s.sticky is True
    assert "tor exit" in s.notes


# ---------------------------------------------------------------------------
# ProxySpec.host
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_proxy_spec_host():
    s = _spec(url="http://proxy.example.com:3128")
    assert s.host == "proxy.example.com"


@pytest.mark.unit
def test_proxy_spec_host_no_port():
    s = _spec(url="http://myproxy.com")
    assert s.host == "myproxy.com"


# ---------------------------------------------------------------------------
# ProxyState
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_proxy_state_untrusted_initially():
    state = ProxyState(proxy_id=uuid4())
    assert not state.trusted
    assert state.composite_score() == 0.5


@pytest.mark.unit
def test_proxy_state_composite_score_after_trust():
    from aegis.constants import PROXY_HEALTH_MIN_SAMPLES

    state = ProxyState(proxy_id=uuid4())
    state.samples = PROXY_HEALTH_MIN_SAMPLES
    state.ema_success = 1.0
    state.ema_latency_ms = 100.0
    score = state.composite_score()
    assert 0.9 < score <= 1.0


@pytest.mark.unit
def test_proxy_state_is_not_banned_by_default():
    state = ProxyState(proxy_id=uuid4())
    assert not state.is_banned_for("reddit.com", now_monotonic=time.monotonic())


@pytest.mark.unit
def test_proxy_state_ban_active():
    state = ProxyState(proxy_id=uuid4())
    state.banned_until["reddit.com"] = time.monotonic() + 3600
    assert state.is_banned_for("reddit.com", now_monotonic=time.monotonic())


@pytest.mark.unit
def test_proxy_state_ban_expired():
    state = ProxyState(proxy_id=uuid4())
    state.banned_until["reddit.com"] = time.monotonic() - 1.0  # already past
    assert not state.is_banned_for("reddit.com", now_monotonic=time.monotonic())
    assert "reddit.com" not in state.banned_until  # cleared


# ---------------------------------------------------------------------------
# DEFAULT_DIRECT_SPEC
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_default_direct_spec():
    assert DEFAULT_DIRECT_SPEC.kind == ProxyKind.DIRECT
    assert DEFAULT_DIRECT_SPEC.proxy_id == UUID("00000000-0000-0000-0000-00000000DEAD")


# ---------------------------------------------------------------------------
# ProxyPool
# ---------------------------------------------------------------------------


@pytest.fixture
async def pool() -> ProxyPool:
    return ProxyPool(rng_seed=42)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_proxy_pool_empty_initially():
    p = ProxyPool()
    assert p.size == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_proxy_pool_add():
    p = ProxyPool()
    spec = _spec()
    await p.add(spec)
    assert p.size == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_proxy_pool_add_duplicate_no_op():
    p = ProxyPool()
    spec = _spec()
    await p.add(spec)
    await p.add(spec)  # same proxy_id
    assert p.size == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_proxy_pool_add_many():
    p = ProxyPool()
    specs = [_spec() for _ in range(5)]
    n = await p.add_many(specs)
    assert n == 5
    assert p.size == 5


@pytest.mark.unit
@pytest.mark.asyncio
async def test_proxy_pool_remove():
    p = ProxyPool()
    spec = _spec()
    await p.add(spec)
    removed = await p.remove(spec.proxy_id)
    assert removed is True
    assert p.size == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_proxy_pool_remove_nonexistent():
    p = ProxyPool()
    removed = await p.remove(uuid4())
    assert removed is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_proxy_pool_acquire_basic():
    p = ProxyPool(rng_seed=0)
    spec = _spec()
    await p.add(spec)
    result = await p.acquire(target_host="example.com")
    assert result.proxy_id == spec.proxy_id


@pytest.mark.unit
@pytest.mark.asyncio
async def test_proxy_pool_acquire_empty_raises():
    p = ProxyPool()
    with pytest.raises(EmptyPoolError):
        await p.acquire(target_host="example.com")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_proxy_pool_acquire_country_filter():
    p = ProxyPool(rng_seed=0)
    us_spec = _spec(url="http://us.proxy:8080", country_code="US")
    de_spec = _spec(url="http://de.proxy:8080", country_code="DE")
    await p.add(us_spec)
    await p.add(de_spec)

    result = await p.acquire(target_host="example.com", country_codes=frozenset({"US"}))
    assert result.country_code == "US"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_proxy_pool_acquire_kind_filter():
    p = ProxyPool(rng_seed=0)
    dc_spec = make_proxy_spec("http://dc:8080", kind=ProxyKind.DATACENTER)
    res_spec = make_proxy_spec("http://res:8080", kind=ProxyKind.RESIDENTIAL)
    await p.add(dc_spec)
    await p.add(res_spec)

    result = await p.acquire(
        target_host="example.com", kind_in=frozenset({ProxyKind.RESIDENTIAL})
    )
    assert result.kind == ProxyKind.RESIDENTIAL


@pytest.mark.unit
@pytest.mark.asyncio
async def test_proxy_pool_sticky_session():
    p = ProxyPool(rng_seed=0)
    for i in range(3):
        await p.add(_spec(url=f"http://proxy{i}:8080"))

    # First acquire sets the sticky binding
    s1 = await p.acquire(target_host="reddit.com", session_key="sess-abc")
    # Second should return the same proxy
    s2 = await p.acquire(target_host="reddit.com", session_key="sess-abc")
    assert s1.proxy_id == s2.proxy_id


@pytest.mark.unit
@pytest.mark.asyncio
async def test_proxy_pool_report_success():
    p = ProxyPool(rng_seed=0)
    spec = _spec()
    await p.add(spec)
    # Should not raise
    await p.report(
        proxy_id=spec.proxy_id,
        target_host="example.com",
        outcome=ProxyOutcome.SUCCESS,
        latency_ms=120.5,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_proxy_pool_report_banned():
    p = ProxyPool(rng_seed=0)
    spec = _spec()
    await p.add(spec)
    await p.report(
        proxy_id=spec.proxy_id,
        target_host="reddit.com",
        outcome=ProxyOutcome.BANNED,
        latency_ms=None,
    )
    # Proxy should now be banned for reddit.com
    state = p._states[spec.proxy_id]
    assert "reddit.com" in state.banned_until


@pytest.mark.unit
@pytest.mark.asyncio
async def test_proxy_pool_acquire_skips_banned():
    p = ProxyPool(rng_seed=0)
    spec1 = _spec(url="http://p1:8080", kind=ProxyKind.DATACENTER)
    spec2 = _spec(url="http://p2:8080", kind=ProxyKind.DATACENTER)
    await p.add(spec1)
    await p.add(spec2)

    # Ban spec1 for reddit.com
    await p.report(
        proxy_id=spec1.proxy_id,
        target_host="reddit.com",
        outcome=ProxyOutcome.BANNED,
    )

    # Acquire should pick spec2 (only non-banned one)
    result = await p.acquire(target_host="reddit.com")
    assert result.proxy_id == spec2.proxy_id


# ---------------------------------------------------------------------------
# ProxyKind and ProxyOutcome enums
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_proxy_kind_enum():
    assert ProxyKind.TOR == "tor"
    assert ProxyKind.RESIDENTIAL == "residential"
    assert ProxyKind.DIRECT == "direct"


@pytest.mark.unit
def test_proxy_outcome_enum():
    assert ProxyOutcome.SUCCESS == "success"
    assert ProxyOutcome.BANNED == "banned"
    assert ProxyOutcome.SOFT_FAIL == "soft_fail"
