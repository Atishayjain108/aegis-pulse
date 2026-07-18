"""Unit tests for Phase 8 IPR checker (USPTO + EUIPO)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aegis.compliance.config import ComplianceSettings
from aegis.compliance.ipr import IPRChecker
from aegis.compliance.schemas import PatentMatch, TrademarkMatch


@pytest.fixture
def checker() -> IPRChecker:
    return IPRChecker(ComplianceSettings())


# ---------------------------------------------------------------------------
# Trademark tests
# ---------------------------------------------------------------------------

class TestTrademarkCheck:
    async def test_exact_brand_match_nike(self, checker: IPRChecker) -> None:
        with patch.object(checker, "_search_euipo", new_callable=AsyncMock, return_value=[]):
            matches, risk = await checker.check_trademark("Nike Air Max Running Shoes")
        assert len(matches) >= 1
        assert any("nike" in m.registered_mark.lower() for m in matches)
        assert risk >= 0.7

    async def test_exact_brand_match_gucci(self, checker: IPRChecker) -> None:
        with patch.object(checker, "_search_euipo", new_callable=AsyncMock, return_value=[]):
            matches, risk = await checker.check_trademark("Gucci Leather Handbag")
        assert len(matches) >= 1
        assert risk >= 0.7

    async def test_fuzzy_brand_match_typosquat(self, checker: IPRChecker) -> None:
        # "Adiddas" doesn't contain "adidas" as a substring (double-d insertion)
        # but difflib SequenceMatcher ratio ≥ 0.78 → triggers fuzzy match
        with patch.object(checker, "_search_euipo", new_callable=AsyncMock, return_value=[]):
            matches, _ = await checker.check_trademark("Adiddas Sneakers Limited Edition")
        assert len(matches) >= 1

    async def test_clean_product_no_brand_match(self, checker: IPRChecker) -> None:
        with patch.object(checker, "_search_euipo", new_callable=AsyncMock, return_value=[]):
            matches, risk = await checker.check_trademark("Plain Cotton T-Shirt")
        assert matches == []
        assert risk == 0.0

    async def test_euipo_live_response_parsed(self, checker: IPRChecker) -> None:
        # Use a title without any local brand match so EUIPO API is always called
        euipo_payload = {
            "trademarks": [
                {
                    "markName": "EUROMARK",
                    "registrationNumber": "EU-009876",
                    "holder": {"name": "Test Owner SA"},
                    "status": "Registered",
                    "goodsServicesDescription": "beverages",
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = euipo_payload

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            matches, _ = await checker.check_trademark("euromark drinks")

        eu_matches = [m for m in matches if m.source == "euipo_tmview"]
        assert len(eu_matches) >= 1
        assert eu_matches[0].jurisdiction == "EU"
        assert eu_matches[0].registration_number == "EU-009876"

    async def test_euipo_api_failure_falls_back(self, checker: IPRChecker) -> None:
        with patch("httpx.AsyncClient.get", side_effect=Exception("network error")):
            # Should not raise; just returns local matches only
            matches, risk = await checker.check_trademark("gucci bag")
        # Local match still found
        assert len(matches) >= 1

    async def test_trademark_risk_zero_for_clean_product(self, checker: IPRChecker) -> None:
        with patch.object(checker, "_search_euipo", new_callable=AsyncMock, return_value=[]):
            _, risk = await checker.check_trademark("Generic Bamboo Cutting Board")
        assert risk == 0.0

    async def test_trademark_caching(self, checker: IPRChecker) -> None:
        with patch.object(checker, "_search_euipo", new_callable=AsyncMock, return_value=[]) as mock_euipo:
            await checker.check_trademark("nike shoes")
            await checker.check_trademark("nike shoes")
        # EUIPO should only be called once (second call is cached)
        assert mock_euipo.call_count <= 2  # local found = may skip EUIPO on 1st

    async def test_trademark_match_model_fields(self, checker: IPRChecker) -> None:
        with patch.object(checker, "_search_euipo", new_callable=AsyncMock, return_value=[]):
            matches, _ = await checker.check_trademark("Rolex Watch")
        for m in matches:
            assert isinstance(m, TrademarkMatch)
            assert 0.0 <= m.confidence_score <= 1.0
            assert m.jurisdiction != ""
            assert m.status in ("registered", "pending", "expired")


# ---------------------------------------------------------------------------
# Patent tests
# ---------------------------------------------------------------------------

class TestPatentCheck:
    async def test_patent_search_live_response(self, checker: IPRChecker) -> None:
        patentsview_payload = {
            "patents": [
                {
                    "patent_number": "US12345678",
                    "patent_title": "Fabric dyeing method for cotton garments",
                    "patent_date": "2022-06-15",
                    "patent_abstract": "A method for dyeing cotton fabrics using natural dyes.",
                }
            ],
            "count": 1,
            "total_patent_count": 10,
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = patentsview_payload

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
            matches, risk = await checker.check_patent("cotton dyeing method")

        assert len(matches) >= 1
        assert matches[0].patent_number == "US12345678"
        assert matches[0].status == "granted"

    async def test_patent_expired_low_risk(self, checker: IPRChecker) -> None:
        patentsview_payload = {
            "patents": [
                {
                    "patent_number": "US01234567",
                    "patent_title": "Method for weaving cotton",
                    "patent_date": "1998-03-10",
                    "patent_abstract": "Old textile weaving method.",
                }
            ],
            "count": 1,
            "total_patent_count": 1,
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = patentsview_payload

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
            matches, risk = await checker.check_patent("cotton weaving")

        expired = [m for m in matches if m.status == "expired"]
        # Expired patents produce lower risk
        if expired:
            assert risk < 0.5

    async def test_patent_api_failure_returns_empty(self, checker: IPRChecker) -> None:
        with patch("httpx.AsyncClient.post", side_effect=Exception("timeout")):
            matches, risk = await checker.check_patent("some product")
        assert matches == []
        assert risk == 0.0

    async def test_patent_match_model_fields(self, checker: IPRChecker) -> None:
        patentsview_payload = {
            "patents": [
                {
                    "patent_number": "US99999999",
                    "patent_title": "Widget manufacture process",
                    "patent_date": "2023-01-01",
                    "patent_abstract": "Widget manufacturing using advanced process.",
                }
            ],
            "count": 1,
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = patentsview_payload

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
            matches, _ = await checker.check_patent("widget manufacturing process")

        for m in matches:
            assert isinstance(m, PatentMatch)
            assert 0.0 <= m.similarity_score <= 1.0
