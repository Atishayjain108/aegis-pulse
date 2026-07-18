"""Tests for IntentParser (Phase M1).

Covers the three blueprint personas (19yo from zero, shop owner, funded
founder), the LLM-off heuristic path, and the missing-sector clarifying ask.
"""

from __future__ import annotations

import pytest

from aegis.mentor.config import MentorSettings
from aegis.mentor.intent import IntentParser
from aegis.mentor.schemas import (
    SECTOR_UNDECIDED,
    AutonomyPreference,
    Channel,
    ExperienceLevel,
    Intent,
)


def _parser() -> IntentParser:
    # LLM disabled → pure deterministic heuristic path (zero API keys).
    return IntentParser(settings=MentorSettings(llm_enrichment_enabled=False))


class TestPersonas:
    @pytest.mark.asyncio
    async def test_19yo_zero_capital_never_leaves_home(self) -> None:
        text = (
            "I'm 19, I have no money and I don't know anything. I never leave home "
            "but I want to make some income selling products online."
        )
        result = await _parser().parse(text)
        p = result.profile
        assert p.sector == "d2c_india"
        assert p.intent == Intent.INCOME
        assert p.autonomy_preference == AutonomyPreference.GUIDE_ME
        assert p.experience_level == ExperienceLevel.NONE
        assert p.capital_usd == 0.0
        assert p.channels == Channel.ONLINE_ONLY
        assert "never_leaves_home" in p.constraints
        assert result.needs_clarification is False

    @pytest.mark.asyncio
    async def test_shop_owner_local_retail(self) -> None:
        text = "I run a kirana shop and want to grow it. I already sell groceries locally."
        result = await _parser().parse(text)
        p = result.profile
        assert p.sector == "local_retail"
        assert p.intent == Intent.SCALE
        assert p.channels == Channel.LOCAL
        assert result.needs_clarification is False

    @pytest.mark.asyncio
    async def test_funded_founder_answer_me_altitude(self) -> None:
        text = (
            "I'm an experienced founder of a d2c apparel brand with 5 lakh capital. "
            "Just give me sharp analysis on margin and saturation."
        )
        result = await _parser().parse(text)
        p = result.profile
        assert p.sector == "d2c_india"
        assert p.autonomy_preference == AutonomyPreference.ANSWER_ME
        assert p.experience_level == ExperienceLevel.EXPERIENCED
        # 5 lakh INR ≈ 6024 USD.
        assert 5900 < p.capital_usd < 6100


class TestClarification:
    @pytest.mark.asyncio
    async def test_missing_sector_triggers_clarifying_ask(self) -> None:
        result = await _parser().parse("I want to make some money but I'm not sure how.")
        assert result.profile.sector == SECTOR_UNDECIDED
        assert result.needs_clarification is True
        assert "sector" in result.missing_fields
        assert result.clarifying_question
        assert "?" in result.clarifying_question

    @pytest.mark.asyncio
    async def test_missing_intent_triggers_ask(self) -> None:
        result = await _parser().parse("Something about selling products online on flipkart.")
        # Sector resolved, but no intent stated.
        assert result.profile.sector == "d2c_india"
        assert result.needs_clarification is True
        assert "intent" in result.missing_fields


class TestCapitalExtraction:
    @pytest.mark.asyncio
    async def test_usd_amount(self) -> None:
        result = await _parser().parse("I have $500 to start an online store.")
        assert result.profile.capital_usd == 500.0

    @pytest.mark.asyncio
    async def test_no_capital(self) -> None:
        result = await _parser().parse("I have no capital but want to sell online.")
        assert result.profile.capital_usd == 0.0

    @pytest.mark.asyncio
    async def test_defaults_region_inr(self) -> None:
        result = await _parser().parse("sell products online")
        assert result.profile.region == "IN"
        assert result.profile.currency == "INR"


class TestLLMEnrichment:
    @pytest.mark.asyncio
    async def test_llm_fills_missing_sector(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Heuristics leave sector undecided; LLM resolves it.
        async def fake_complete(agent, messages, **kwargs):
            return '{"sector_keywords": "online store dropshipping", "intent": "income"}'

        monkeypatch.setattr(
            "aegis.llm.bridge.agents_bridge.complete_for_agent",
            fake_complete,
        )
        parser = IntentParser(settings=MentorSettings(llm_enrichment_enabled=True))
        result = await parser.parse("I want to earn but not sure in what.")
        assert result.profile.sector == "d2c_india"
        assert result.extraction_method == "llm"

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_heuristic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def boom(agent, messages, **kwargs):
            raise RuntimeError("gateway down")

        monkeypatch.setattr(
            "aegis.llm.bridge.agents_bridge.complete_for_agent",
            boom,
        )
        parser = IntentParser(settings=MentorSettings(llm_enrichment_enabled=True))
        result = await parser.parse("sell products online to make income")
        # Heuristic still produced a grounded profile.
        assert result.profile.sector == "d2c_india"
        assert result.profile.intent == Intent.INCOME
