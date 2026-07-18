"""Tests for the M4 CustomerOp (lead gen / acquisition / retention — grounded)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from aegis.mentor.operators import CustomerOp, OperatorContext, OperatorResult
from aegis.mentor.packs.d2c_india import D2CIndiaPack
from aegis.mentor.schemas import Channel, Intent, UserProfile


def _profile(**kw: Any) -> UserProfile:
    base = {"sector": "d2c_india", "sector_raw": "wireless earbuds", "intent": Intent.INCOME}
    base.update(kw)
    return UserProfile(**base)


def _ctx(**kw: Any) -> OperatorContext:
    base: dict[str, Any] = {"sector_pack": D2CIndiaPack(), "depth": "deep"}
    base.update(kw)
    return OperatorContext(**base)


# --- Fakes ----------------------------------------------------------------


class _FakeBuyerIntel:
    def __init__(self, intensity: float | None) -> None:
        self._intensity = intensity

    async def demand_proxy(self, region: str, category: str) -> Any:
        return SimpleNamespace(
            region=region.upper(),
            category=category.lower(),
            demand_intensity=self._intensity,
            buyer_trust=None,  # UNVERIFIED — no real orders
            model_dump=lambda: {
                "region": region.upper(),
                "category": category.lower(),
                "demand_intensity": self._intensity,
                "buyer_trust": None,
            },
        )


async def _signal_source(profile: UserProfile, request: str, ctx: OperatorContext) -> list[dict]:
    return [
        {"platform": "reddit", "title": "Looking for good wireless earbuds under 2k",
         "url": "http://r/1", "raw_text": "earbuds recommendation"},
        {"platform": "youtube", "title": "Best earbuds 2024 review",
         "url": "http://y/2", "raw_text": "earbuds review"},
    ]


# --- Tests ----------------------------------------------------------------


class TestCustomerOp:
    @pytest.mark.asyncio
    async def test_persona_channels_leads_drafts(self) -> None:
        op = CustomerOp(
            buyer_intel=_FakeBuyerIntel(0.62),
            signal_source=_signal_source,
        )
        res = await op.run(_profile(), "find me leads and an audience", _ctx())

        assert isinstance(res, OperatorResult)
        assert res.operator == "customer"
        # Persona is grounded in the sector pack (marketplaces, CAC).
        assert "Target persona" in res.data["persona"]
        # ₹0-budget acquisition channels present.
        assert res.data["acquisition_channels"]
        assert any("Free channel:" in a for a in res.actions)
        # Outreach drafts produced.
        assert res.data["outreach_drafts"]
        # Leads surfaced.
        assert res.data["lead_count"] == 2

    @pytest.mark.asyncio
    async def test_leads_are_labeled_proxies_not_confirmed_buyers(self) -> None:
        op = CustomerOp(buyer_intel=_FakeBuyerIntel(0.5), signal_source=_signal_source)
        res = await op.run(_profile(), "leads", _ctx())

        assert res.data["all_leads_are_proxies"] is True
        assert all(lead["verified_buyer"] is False for lead in res.data["leads"])
        assert all(lead["proxy"] is True for lead in res.data["leads"])
        # Findings explicitly mark them as proxies, never confirmed buyers.
        lead_findings = [f for f in res.findings if "proxy lead" in f]
        assert lead_findings
        assert all("NOT a confirmed buyer" in f for f in lead_findings)

    @pytest.mark.asyncio
    async def test_demand_is_labeled_proxy_unverified(self) -> None:
        op = CustomerOp(buyer_intel=_FakeBuyerIntel(0.62), signal_source=_signal_source)
        res = await op.run(_profile(), "audience demand", _ctx())
        demand_findings = [f for f in res.findings if "demand PROXY" in f]
        assert demand_findings
        assert "not verified purchase intent" in demand_findings[0]
        assert "execution_intel:buyer_demand_proxy" in res.sources

    @pytest.mark.asyncio
    async def test_persuasion_case_answers_objections_with_real_numbers(self) -> None:
        op = CustomerOp(buyer_intel=_FakeBuyerIntel(0.62), signal_source=_signal_source)
        res = await op.run(_profile(), "convince me there are customers", _ctx())
        # The conviction engine ran on the grounded numbers.
        assert res.data["persuasion_refused"] is False
        assert res.data["persuasion"]
        # Grounded evidence exposed for the agent-level Persuader to fold in.
        kinds = {e["kind"] for e in res.data["persuasion_evidence"]}
        assert "demand" in kinds  # demand proxy
        assert "audience" in kinds  # audience signals
        assert "margin" in kinds  # sector benchmark

    @pytest.mark.asyncio
    async def test_llm_off_path_still_full(self) -> None:
        # use_llm False → still a complete grounded result (heuristic-first).
        op = CustomerOp(buyer_intel=_FakeBuyerIntel(0.4), signal_source=_signal_source)
        res = await op.run(_profile(), "leads", _ctx(use_llm=False))
        assert res.has_signal
        assert res.data["persona"]
        assert res.confidence > 0.0

    @pytest.mark.asyncio
    async def test_graceful_when_no_pool_no_sources(self) -> None:
        # No signal source, no pool, no buyer intel, no sector pack → still
        # grounded-or-silent: persona/channels from defaults, but no fabricated leads.
        op = CustomerOp(buyer_intel=None, signal_source=None)
        res = await op.run(_profile(), "leads", OperatorContext())
        assert res.data["lead_count"] == 0
        assert res.data["demand_proxy"] is None
        # No grounded numeric evidence → Persuader refuses (silent).
        assert res.data["persuasion_refused"] is True
        assert res.data["persuasion"] == []

    @pytest.mark.asyncio
    async def test_local_channel_profile_gets_local_channels(self) -> None:
        op = CustomerOp(buyer_intel=None, signal_source=_signal_source)
        res = await op.run(
            _profile(channels=Channel.LOCAL), "acquire customers", _ctx()
        )
        channels = " ".join(res.data["acquisition_channels"]).lower()
        assert "word-of-mouth" in channels or "local" in channels

    @pytest.mark.asyncio
    async def test_signal_source_failure_degrades_to_no_leads(self) -> None:
        async def _boom(profile, request, ctx):
            raise RuntimeError("scrape down")

        op = CustomerOp(buyer_intel=_FakeBuyerIntel(0.3), signal_source=_boom)
        res = await op.run(_profile(), "leads", _ctx())
        assert res.data["lead_count"] == 0
        # Still grounded by the demand proxy + sector benchmark.
        assert res.has_signal
