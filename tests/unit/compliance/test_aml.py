"""Unit tests for Phase 8 AML / sanctions checker."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aegis.compliance.aml import AMLChecker
from aegis.compliance.config import ComplianceSettings
from aegis.compliance.schemas import SanctionMatch


@pytest.fixture
def checker() -> AMLChecker:
    return AMLChecker(ComplianceSettings())


class TestAMLChecker:
    async def test_clean_us_to_us_low_risk(self, checker: AMLChecker) -> None:
        matches, risk = await checker.check_transaction("US", "US")
        assert matches == []
        assert risk == pytest.approx(0.02)

    async def test_ofac_sanctioned_origin_iran(self, checker: AMLChecker) -> None:
        matches, risk = await checker.check_transaction("IR", "US")
        assert len(matches) >= 1
        sanction_matches = [m for m in matches if m.match_type == "country_sanction"]
        assert len(sanction_matches) >= 1
        assert sanction_matches[0].matched_value == "IR"
        assert risk >= 0.99

    async def test_ofac_sanctioned_destination_north_korea(self, checker: AMLChecker) -> None:
        matches, risk = await checker.check_transaction("US", "KP")
        sanction_matches = [m for m in matches if m.match_type == "country_sanction"]
        assert len(sanction_matches) >= 1
        assert risk >= 0.99

    async def test_ofac_sanctioned_russia(self, checker: AMLChecker) -> None:
        matches, risk = await checker.check_transaction("RU", "US")
        assert any(m.matched_value == "RU" for m in matches)
        assert risk >= 0.99

    async def test_ofac_sanctioned_cuba(self, checker: AMLChecker) -> None:
        matches, risk = await checker.check_transaction("US", "CU")
        assert any(m.matched_value == "CU" for m in matches)

    async def test_fatf_high_risk_not_ofac(self, checker: AMLChecker) -> None:
        # Nigeria is FATF grey-listed but not OFAC-sanctioned
        matches, risk = await checker.check_transaction("US", "NG")
        fatf_matches = [m for m in matches if m.match_type == "fatf_high_risk"]
        assert len(fatf_matches) >= 1
        # Risk should be medium (0.50), not as high as OFAC
        assert 0.40 <= risk <= 0.65

    async def test_fatf_black_listed_country(self, checker: AMLChecker) -> None:
        # Myanmar is both OFAC-sanctioned AND FATF black-listed
        matches, risk = await checker.check_transaction("MM", "US")
        assert risk >= 0.95

    async def test_no_entity_screening_without_api_key(self, checker: AMLChecker) -> None:
        # Default checker has no trade_gov_api_key → entity screening is skipped
        matches, _ = await checker.check_transaction("US", "US", entity_names=["ACME Corp"])
        entity_matches = [m for m in matches if m.match_type == "entity_match"]
        assert entity_matches == []

    async def test_entity_screening_with_api_key(self) -> None:
        cfg = ComplianceSettings(trade_gov_api_key="test-key")
        checker = AMLChecker(cfg)

        mock_response = {
            "results": [
                {
                    "name": "EVIL ENTITY",
                    "programs": ["IRAN"],
                    "source": "SDN",
                }
            ],
            "meta": {"total": 1, "count": 1},
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_response

        # Patch the AsyncClient context manager so .get() returns our mock
        with patch("aegis.compliance.aml.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            matches, risk = await checker.check_transaction(
                "US", "US", entity_names=["EVIL ENTITY"]
            )

        entity_matches = [m for m in matches if m.match_type == "entity_match"]
        assert len(entity_matches) >= 1
        assert entity_matches[0].matched_value == "EVIL ENTITY"

    async def test_sanction_match_model_fields(self, checker: AMLChecker) -> None:
        matches, risk = await checker.check_transaction("IR", "US")
        m = matches[0]
        assert isinstance(m, SanctionMatch)
        assert m.match_type in ("country_sanction", "fatf_high_risk", "entity_match")
        assert 0.0 <= m.risk_score <= 1.0
        assert len(m.source) > 0

    async def test_both_countries_clean(self, checker: AMLChecker) -> None:
        matches, risk = await checker.check_transaction("DE", "JP")
        assert matches == []
        assert risk == pytest.approx(0.02)

    async def test_ofac_program_recorded(self, checker: AMLChecker) -> None:
        matches, _ = await checker.check_transaction("IR", "US")
        iran_match = next(m for m in matches if m.matched_value == "IR")
        assert "IRAN" in iran_match.program.upper()
