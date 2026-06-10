"""Tests for comply/trademark/cache.py, clients.py, and rules/loader.py error paths.

All tests are sync/pure-Python — no httpx, no network calls.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from aegis.comply.errors import TrademarkCircuitOpenError
from aegis.comply.schemas import Jurisdiction, TrademarkMatch
from aegis.comply.trademark.cache import TrademarkCache
from aegis.comply.trademark.clients import (
    EUIPOClient,
    TrademarkClient,
    USPTOClient,
    WIPOClient,
    _Circuit,
)

# ---------------------------------------------------------------------------
# TrademarkCache
# ---------------------------------------------------------------------------


def _match(**kw) -> TrademarkMatch:
    base = {"mark": "NIKE", "owner": "Nike Inc.", "jurisdiction": Jurisdiction.US, "similarity": 0.95}
    base.update(kw)
    return TrademarkMatch(**base)


class TestTrademarkCache:
    def test_miss_returns_none(self):
        cache = TrademarkCache()
        assert cache.get("unknown-term") is None

    def test_put_and_get_returns_matches(self):
        cache = TrademarkCache()
        matches = [_match()]
        cache.put("nike", matches)
        result = cache.get("nike")
        assert result is not None
        assert len(result) == 1
        assert result[0].mark == "NIKE"

    def test_lookup_is_case_insensitive(self):
        cache = TrademarkCache()
        cache.put("ADIDAS", [_match(mark="ADIDAS")])
        assert cache.get("adidas") is not None
        assert cache.get("Adidas") is not None

    def test_expired_entry_returns_none(self):
        cache = TrademarkCache(ttl_s=60)
        cache.put("brand", [_match()], now=time.time() - 61)
        assert cache.get("brand") is None

    def test_not_expired_entry_still_returns(self):
        cache = TrademarkCache(ttl_s=3600)
        cache.put("brand", [_match()], now=time.time() - 10)
        assert cache.get("brand") is not None

    def test_empty_matches_list(self):
        cache = TrademarkCache()
        cache.put("nomarks", [])
        result = cache.get("nomarks")
        assert result == []

    def test_close_does_not_raise(self):
        cache = TrademarkCache()
        cache.close()

    def test_persistent_path(self, tmp_path: Path):
        db_path = tmp_path / "tm_cache.sqlite3"
        cache = TrademarkCache(path=db_path)
        cache.put("term", [_match()])
        cache.close()
        cache2 = TrademarkCache(path=db_path)
        assert cache2.get("term") is not None
        cache2.close()


# ---------------------------------------------------------------------------
# _Circuit
# ---------------------------------------------------------------------------


class TestCircuit:
    def test_initially_closed(self):
        c = _Circuit()
        assert not c.is_open

    def test_now_returns_float(self):
        c = _Circuit()
        assert isinstance(c._now(), float)

    def test_below_threshold_stays_closed(self):
        c = _Circuit(threshold=3)
        c.record_failure()
        c.record_failure()
        assert not c.is_open

    def test_at_threshold_opens(self):
        c = _Circuit(threshold=3, recovery_s=9999.0)
        c.record_failure()
        c.record_failure()
        c.record_failure()
        assert c.is_open

    def test_recovery_after_timeout_resets(self):
        c = _Circuit(threshold=1, recovery_s=0.0)
        c.record_failure()
        assert not c.is_open  # recovery_s=0 means already past window

    def test_record_success_resets_failures(self):
        c = _Circuit(threshold=3, recovery_s=9999.0)
        c.record_failure()
        c.record_failure()
        c.record_success()
        assert c.failures == 0
        assert not c.is_open

    def test_opened_at_set_on_threshold(self):
        before = time.monotonic()
        c = _Circuit(threshold=1)
        c.record_failure()
        assert c.opened_at >= before


# ---------------------------------------------------------------------------
# TrademarkClient and subclasses
# ---------------------------------------------------------------------------


class TestTrademarkClientInit:
    def test_default_init(self):
        client = TrademarkClient()
        assert client._circuit is not None
        assert client._timeout > 0

    def test_custom_timeout(self):
        client = TrademarkClient(timeout_s=42.0)
        assert client._timeout == 42.0

    def test_uspto_client_source(self):
        client = USPTOClient()
        assert client.source == "uspto"
        assert client.jurisdiction == Jurisdiction.US

    def test_euipo_client_source(self):
        client = EUIPOClient()
        assert client.source == "euipo"
        assert client.jurisdiction == Jurisdiction.EU

    def test_wipo_client_source(self):
        client = WIPOClient()
        assert client.source == "wipo"
        assert client.jurisdiction == Jurisdiction.GLOBAL

    def test_query_raises_when_circuit_open(self):
        client = TrademarkClient(timeout_s=1.0)
        # Trip the circuit breaker
        for _ in range(client._circuit.threshold):
            client._circuit.record_failure()
        assert client._circuit.is_open
        with pytest.raises(TrademarkCircuitOpenError):
            asyncio.run(client.query("nike"))


# ---------------------------------------------------------------------------
# rules/loader.py error paths
# ---------------------------------------------------------------------------


from aegis.comply.errors import RuleLoadError  # noqa: E402
from aegis.comply.rules.loader import (  # noqa: E402
    _parse_rule,
    _parse_ruleset,
    load_registry,
)


class TestLoaderErrorPaths:
    def test_parse_rule_missing_required_field(self):
        with pytest.raises(RuleLoadError, match="invalid rule"):
            _parse_rule(
                {"name": "no-id", "severity": "block", "category": "advertising"},
                default_jur=Jurisdiction.US,
                source="test",
            )

    def test_parse_rule_bad_severity_value(self):
        with pytest.raises(RuleLoadError, match="invalid rule"):
            _parse_rule(
                {"id": "T-001", "name": "x", "severity": "NOTAVALUE", "category": "advertising", "match": {}},
                default_jur=Jurisdiction.US,
                source="test",
            )

    def test_parse_ruleset_non_dict_raises(self):
        with pytest.raises(RuleLoadError, match="mapping"):
            _parse_ruleset(["not", "a", "dict"], source="test.yaml")  # type: ignore[arg-type]

    def test_parse_ruleset_bad_jurisdiction_raises(self):
        with pytest.raises(RuleLoadError, match="bad jurisdiction"):
            _parse_ruleset(
                {"jurisdiction": "INVALID_LAND", "rules": []},
                source="test.yaml",
            )

    def test_parse_ruleset_rules_not_list_raises(self):
        with pytest.raises(RuleLoadError, match="list"):
            _parse_ruleset(
                {"rules": "not-a-list", "jurisdiction": "US"},
                source="test.yaml",
            )

    def test_load_registry_missing_directory_returns_builtin(self, tmp_path: Path):
        missing = tmp_path / "no_such_dir"
        registry = load_registry(missing)
        # Should fall back to builtin_registry — has at least one rule
        assert len(registry.all_rules) > 0
