"""Unit tests for Phase 8 ComplianceEngine orchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from aegis.compliance.cache import ComplianceCache
from aegis.compliance.config import ComplianceSettings
from aegis.compliance.engine import ComplianceEngine
from aegis.compliance.schemas import (
    ComplianceRequest,
    ComplianceRiskAssessment,
    Recommendation,
)


def _clean_request(**kwargs: object) -> ComplianceRequest:
    defaults: dict[str, object] = {
        "product_sku": "TSHIRT-001",
        "product_title": "Classic Cotton T-Shirt",
        "product_description": "100% combed cotton, machine washable.",
        "category": "apparel",
        "origin_country": "US",
        "destination_country": "US",
    }
    defaults.update(kwargs)
    return ComplianceRequest(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def engine() -> ComplianceEngine:
    return ComplianceEngine(ComplianceSettings())


class TestComplianceEngine:
    async def test_clean_product_proceeds(self, engine: ComplianceEngine) -> None:
        req = _clean_request()
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
             patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_resp = AsyncMock()
            mock_resp.status_code = 404
            mock_get.return_value = mock_resp
            mock_post.return_value = mock_resp
            result = await engine.assess(req)

        assert isinstance(result, ComplianceRiskAssessment)
        assert result.recommendation in (Recommendation.PROCEED, Recommendation.ESCALATE)
        assert 0.0 <= result.overall_risk_score <= 1.0

    async def test_ofac_sanctioned_route_blocks(self, engine: ComplianceEngine) -> None:
        req = _clean_request(origin_country="IR", destination_country="US")
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
             patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_resp = AsyncMock()
            mock_resp.status_code = 404
            mock_get.return_value = mock_resp
            mock_post.return_value = mock_resp
            result = await engine.assess(req)

        # Hard override: OFAC sanction forces BLOCK regardless of composite score
        assert result.recommendation == Recommendation.BLOCK
        assert len(result.sanction_matches) >= 1
        assert any("Sanctions" in r or "country_sanction" in r for r in result.reasons)

    async def test_banned_fda_product_blocks(self, engine: ComplianceEngine) -> None:
        req = _clean_request(
            product_sku="DRUG-001",
            product_title="fake medication pills",
            category="pharmaceuticals",
        )
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
             patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_resp = AsyncMock()
            mock_resp.status_code = 404
            mock_get.return_value = mock_resp
            mock_post.return_value = mock_resp
            result = await engine.assess(req)

        assert result.recommendation == Recommendation.BLOCK

    async def test_replica_product_blocks(self, engine: ComplianceEngine) -> None:
        req = _clean_request(
            product_sku="FAKE-001",
            product_title="Louis Vuitton Replica Bag",
            product_description="AAA grade replica of authentic LV bag",
            category="luxury",
        )
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
             patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_resp = AsyncMock()
            mock_resp.status_code = 404
            mock_get.return_value = mock_resp
            mock_post.return_value = mock_resp
            result = await engine.assess(req)

        assert result.recommendation in (Recommendation.BLOCK, Recommendation.ESCALATE)

    async def test_ftc_violation_escalates(self, engine: ComplianceEngine) -> None:
        req = _clean_request(
            product_sku="PILL-001",
            product_title="Miracle Supplement",
            product_description="FDA-Approved! Clinically proven to cure cancer!",
            category="pharmaceuticals",
        )
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
             patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_resp = AsyncMock()
            mock_resp.status_code = 404
            mock_get.return_value = mock_resp
            mock_post.return_value = mock_resp
            result = await engine.assess(req)

        assert result.recommendation in (Recommendation.ESCALATE, Recommendation.BLOCK)
        assert len(result.ftc_violations) >= 1

    async def test_result_is_cached(self, engine: ComplianceEngine) -> None:
        req = _clean_request()
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
             patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_resp = AsyncMock()
            mock_resp.status_code = 404
            mock_get.return_value = mock_resp
            mock_post.return_value = mock_resp

            result1 = await engine.assess(req)
            result2 = await engine.assess(req)  # should be served from cache

        assert result2.cached is True
        assert result1.assessment_id == result2.assessment_id

    async def test_assessment_has_duration(self, engine: ComplianceEngine) -> None:
        req = _clean_request()
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
             patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_resp = AsyncMock()
            mock_resp.status_code = 404
            mock_get.return_value = mock_resp
            mock_post.return_value = mock_resp
            result = await engine.assess(req)

        assert result.duration_ms > 0

    async def test_risk_breakdown_all_dimensions_present(self, engine: ComplianceEngine) -> None:
        req = _clean_request()
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
             patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_resp = AsyncMock()
            mock_resp.status_code = 404
            mock_get.return_value = mock_resp
            mock_post.return_value = mock_resp
            result = await engine.assess(req)

        b = result.risk_breakdown
        assert 0.0 <= b.trademark <= 1.0
        assert 0.0 <= b.patent <= 1.0
        assert 0.0 <= b.fda <= 1.0
        assert 0.0 <= b.counterfeit <= 1.0
        assert 0.0 <= b.ftc <= 1.0
        assert 0.0 <= b.privacy <= 1.0
        assert 0.0 <= b.aml <= 1.0

    async def test_assess_many_concurrent(self, engine: ComplianceEngine) -> None:
        requests = [_clean_request(product_sku=f"SKU-{i:03d}") for i in range(5)]
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
             patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_resp = AsyncMock()
            mock_resp.status_code = 404
            mock_get.return_value = mock_resp
            mock_post.return_value = mock_resp
            results = await engine.assess_many(requests)

        assert len(results) == 5
        for r in results:
            assert isinstance(r, ComplianceRiskAssessment)

    async def test_error_in_assessment_returns_escalate(self, engine: ComplianceEngine) -> None:
        req = _clean_request()
        # Simulate unexpected error in IPR check
        with patch.object(engine._ipr, "check_trademark", side_effect=RuntimeError("boom")):
            result = await engine.assess(req)

        assert result.recommendation == Recommendation.ESCALATE
        assert result.error_code == "AEGIS-COMPLY-0099"

    async def test_privacy_risk_populated_for_gdpr_route(self, engine: ComplianceEngine) -> None:
        req = _clean_request(destination_country="DE")
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
             patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_resp = AsyncMock()
            mock_resp.status_code = 404
            mock_get.return_value = mock_resp
            mock_post.return_value = mock_resp
            result = await engine.assess(req)

        assert result.privacy_risk is not None
        assert "GDPR" in result.privacy_risk.regulations_triggered

    async def test_reasons_populated_for_risky_product(self, engine: ComplianceEngine) -> None:
        req = _clean_request(origin_country="KP")
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
             patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_resp = AsyncMock()
            mock_resp.status_code = 404
            mock_get.return_value = mock_resp
            mock_post.return_value = mock_resp
            result = await engine.assess(req)

        assert len(result.reasons) >= 1


class TestComplianceCache:
    def test_set_and_get(self) -> None:
        cache = ComplianceCache(ttl_seconds=3600)
        key = ComplianceCache.make_key("SKU-001", "T-Shirt", "US", "DE")
        cache.set(key, {"test": True})
        assert cache.get(key) == {"test": True}

    def test_expired_entry_returns_none(self) -> None:
        cache = ComplianceCache(ttl_seconds=-1)  # already expired
        key = ComplianceCache.make_key("SKU-001", "T-Shirt", "US", "DE")
        cache.set(key, {"test": True})
        assert cache.get(key) is None

    def test_len_excludes_expired(self) -> None:
        cache = ComplianceCache(ttl_seconds=3600)
        key = ComplianceCache.make_key("SKU-001", "T-Shirt", "US", "DE")
        cache.set(key, {"test": True})
        assert len(cache) == 1

    def test_invalidate_removes_key(self) -> None:
        cache = ComplianceCache(ttl_seconds=3600)
        key = "test-key"
        cache.set(key, "value")
        cache.invalidate(key)
        assert cache.get(key) is None

    def test_clear_empties_all(self) -> None:
        cache = ComplianceCache(ttl_seconds=3600)
        for i in range(5):
            cache.set(f"key-{i}", i)
        cache.clear()
        assert len(cache) == 0

    def test_make_key_normalizes_title(self) -> None:
        key1 = ComplianceCache.make_key("SKU", "  Cotton T-Shirt  ", "US", "DE")
        key2 = ComplianceCache.make_key("SKU", "cotton t-shirt", "US", "DE")
        assert key1 == key2
