"""Tests for the LLM augmentor: escalate-only, confidence-only-down, never-raise."""

from __future__ import annotations

import pytest

from aegis.comply.engine import ComplianceEngine
from aegis.comply.llm.augmentor import ComplianceAugmentor
from aegis.comply.schemas import ComplianceRequest, ComplianceVerdict


def _clear_result():
    eng = ComplianceEngine()
    return eng.evaluate(ComplianceRequest(trend_id="aug", title="plain mug", price=10.0))


def _flag_result():
    eng = ComplianceEngine()
    return eng.evaluate(
        ComplianceRequest(trend_id="augf", title="Gucci bag", brand_mentions=("Gucci",), price=300.0)
    )


class _Client:
    def __init__(self, response):
        self._response = response
        self.prompts = []

    async def complete(self, prompt, *, system=""):
        self.prompts.append((prompt, system))
        return self._response


@pytest.mark.asyncio
async def test_no_client_is_passthrough():
    aug = ComplianceAugmentor(client=None)
    r = _clear_result()
    out = await aug.augment(r, ComplianceRequest(trend_id="aug", title="plain mug"))
    assert out is r
    assert aug.available is False


@pytest.mark.asyncio
async def test_augmentor_can_escalate_clear_to_flag():
    client = _Client('{"escalate_to":"flag","confidence_penalty":0.1,"reason":"risky imagery"}')
    aug = ComplianceAugmentor(client=client)
    r = _clear_result()
    out = await aug.augment(r, ComplianceRequest(trend_id="aug", title="plain mug"))
    assert out.verdict is ComplianceVerdict.FLAG
    assert out.augmented is True
    assert out.confidence <= r.confidence


@pytest.mark.asyncio
async def test_augmentor_cannot_downgrade_verdict():
    # Model tries to clear a flagged item -> must be ignored.
    client = _Client('{"escalate_to":"clear","confidence_penalty":0.0,"reason":"looks fine"}')
    aug = ComplianceAugmentor(client=client)
    r = _flag_result()
    out = await aug.augment(r, ComplianceRequest(trend_id="augf", title="Gucci bag"))
    assert out.verdict is ComplianceVerdict.FLAG  # unchanged, never downgraded


@pytest.mark.asyncio
async def test_augmentor_never_raises_on_client_error():
    class _Bad:
        async def complete(self, prompt, *, system=""):
            raise TimeoutError("llm timed out")

    aug = ComplianceAugmentor(client=_Bad())
    r = _flag_result()
    out = await aug.augment(r, ComplianceRequest(trend_id="augf", title="Gucci bag"))
    assert out is r  # unchanged on failure


@pytest.mark.asyncio
async def test_augmentor_handles_garbage_response():
    aug = ComplianceAugmentor(client=_Client("not json at all"))
    r = _flag_result()
    out = await aug.augment(r, ComplianceRequest(trend_id="augf", title="Gucci bag"))
    assert out is r


@pytest.mark.asyncio
async def test_already_blocked_is_left_untouched():
    eng = ComplianceEngine()
    blocked = eng.evaluate(
        ComplianceRequest(trend_id="b", title="gummies", claims=("cures diabetes",))
    )
    assert blocked.verdict is ComplianceVerdict.BLOCK
    client = _Client('{"escalate_to":"clear","confidence_penalty":0,"reason":"x"}')
    aug = ComplianceAugmentor(client=client)
    out = await aug.augment(blocked, ComplianceRequest(trend_id="b", title="gummies"))
    assert out is blocked
    assert client.prompts == []  # short-circuited; no LLM call


@pytest.mark.asyncio
async def test_confidence_penalty_is_clamped():
    client = _Client('{"escalate_to":"block","confidence_penalty":5.0,"reason":"huge"}')
    aug = ComplianceAugmentor(client=client, max_confidence_penalty=0.3)
    r = _flag_result()
    out = await aug.augment(r, ComplianceRequest(trend_id="augf", title="Gucci bag"))
    assert out.verdict is ComplianceVerdict.BLOCK
    assert out.confidence >= 0.30  # floor respected


# ---- pure-helper + discovery coverage --------------------------------------
@pytest.mark.asyncio
async def test_adapter_wraps_acomplete_shape():
    from aegis.comply.llm.augmentor import _adapt

    class _Raw:
        async def acomplete(self, prompt, *, system=""):
            return '{"escalate_to":"flag","confidence_penalty":0.05,"reason":"ok"}'

    adapted = _adapt(_Raw())
    assert adapted is not None
    out = await adapted.complete("p", system="s")
    assert "flag" in out


def test_adapt_returns_none_for_incompatible():
    from aegis.comply.llm.augmentor import _adapt

    assert _adapt(object()) is None
    assert _adapt(None) is None


def test_build_default_augmentor_is_noop_without_phase11():
    # In this standalone test env neither aegis.llm nor aegis.agents exists,
    # so discovery must gracefully yield a pass-through augmentor.
    from aegis.comply.llm.augmentor import build_default_augmentor

    aug = build_default_augmentor()
    assert aug.available is False


def test_parse_strips_markdown_fence():
    aug = ComplianceAugmentor(client=_Client("x"))
    parsed = aug._parse('```json\n{"escalate_to":"block","confidence_penalty":0.2,"reason":"r"}\n```')
    assert parsed is not None
    verdict, penalty, reason = parsed
    assert verdict is ComplianceVerdict.BLOCK
    assert penalty == 0.2


def test_parse_returns_none_for_bad_verdict():
    aug = ComplianceAugmentor(client=_Client("x"))
    assert aug._parse('{"escalate_to":"banana"}') is None
