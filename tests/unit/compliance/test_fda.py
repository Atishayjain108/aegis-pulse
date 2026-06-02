"""Unit tests for Phase 8 FDA checker."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aegis.compliance.config import ComplianceSettings
from aegis.compliance.fda import FDAChecker
from aegis.compliance.schemas import FDAEnforcement


@pytest.fixture
def checker() -> FDAChecker:
    return FDAChecker(ComplianceSettings())


class TestFDAChecker:
    async def test_clean_apparel_low_risk(self, checker: FDAChecker) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 404  # no results
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            enforcements, risk = await checker.check_product("Cotton T-Shirt", "US", "apparel")
        assert risk < 0.2

    async def test_banned_keyword_immediate_block(self, checker: FDAChecker) -> None:
        # No API call needed — banned keyword triggers immediately
        enforcements, risk = await checker.check_product("fake medication pills", "CN")
        assert risk >= 0.90
        # Should not have called FDA API (returns early)

    async def test_counterfeit_drug_blocked(self, checker: FDAChecker) -> None:
        enforcements, risk = await checker.check_product("counterfeit drug tablets", "IN")
        assert risk >= 0.90

    async def test_unauthorized_pharmaceutical_blocked(self, checker: FDAChecker) -> None:
        enforcements, risk = await checker.check_product("unauthorized pharmaceutical compound", "CN")
        assert risk >= 0.90

    async def test_openfda_enforcement_hit_parsed(self, checker: FDAChecker) -> None:
        fda_payload = {
            "results": [
                {
                    "recall_number": "D-001-2024",
                    "reason_for_recall": "Undeclared allergen — peanuts",
                    "product_description": "Protein shake powder vanilla flavour",
                    "classification": "Class I",
                    "status": "Ongoing",
                    "distribution_pattern": "Nationwide",
                }
            ],
            "meta": {"results": {"total": 1, "skip": 0, "limit": 5}},
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fda_payload

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            enforcements, risk = await checker.check_product("protein shake powder", "US", "food")

        assert len(enforcements) >= 1
        assert enforcements[0].recall_number == "D-001-2024"
        assert enforcements[0].classification == "Class I"
        assert risk >= 0.7  # Class I = serious risk

    async def test_class_ii_enforcement_medium_risk(self, checker: FDAChecker) -> None:
        fda_payload = {
            "results": [
                {
                    "recall_number": "D-002-2024",
                    "reason_for_recall": "Minor mislabelling",
                    "product_description": "Vitamin C tablets",
                    "classification": "Class II",
                    "status": "Ongoing",
                }
            ],
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fda_payload

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            enforcements, risk = await checker.check_product("vitamin C tablets", "US", "drug")

        assert 0.3 <= risk <= 0.8

    async def test_class_iii_enforcement_low_risk(self, checker: FDAChecker) -> None:
        fda_payload = {
            "results": [
                {
                    "recall_number": "D-003-2024",
                    "reason_for_recall": "Label print quality issue",
                    "product_description": "Energy bars",
                    "classification": "Class III",
                    "status": "Completed",
                }
            ],
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fda_payload

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            enforcements, risk = await checker.check_product("energy bars", "US", "food")

        assert risk < 0.5  # Class III + Completed = low risk

    async def test_fda_api_404_no_results(self, checker: FDAChecker) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            enforcements, risk = await checker.check_product("plain cotton socks", "US", "apparel")
        assert enforcements == []

    async def test_fda_api_failure_graceful(self, checker: FDAChecker) -> None:
        with patch("httpx.AsyncClient.get", side_effect=Exception("timeout")):
            enforcements, risk = await checker.check_product("vitamin supplement", "US", "drug")
        # Should not raise; returns empty with minimal risk
        assert isinstance(enforcements, list)

    async def test_fda_rate_limit_handled(self, checker: FDAChecker) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            enforcements, risk = await checker.check_product("supplement", "US", "drug")
        assert isinstance(enforcements, list)  # No crash

    async def test_fda_enforcement_model_fields(self, checker: FDAChecker) -> None:
        fda_payload = {
            "results": [
                {
                    "recall_number": "D-999-2024",
                    "reason_for_recall": "Test",
                    "product_description": "Test product",
                    "classification": "Class II",
                    "status": "Ongoing",
                }
            ],
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fda_payload

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            enforcements, _ = await checker.check_product("test product", "US", "drug")

        for e in enforcements:
            assert isinstance(e, FDAEnforcement)
            assert len(e.recall_number) > 0
