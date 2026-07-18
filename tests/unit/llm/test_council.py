"""Unit tests for the multi-model council orchestrator."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from aegis.llm.council import ModelCouncil, council_from_settings


@dataclass
class _FakeResp:
    content: str
    latency_ms: float = 1.0


class _FakeGateway:
    """Records calls and returns a per-model canned answer."""

    def __init__(self, answers: dict[str, str], fail: set[str] | None = None) -> None:
        self.answers = answers
        self.fail = fail or set()
        self.calls: list[tuple[str, int]] = []

    async def complete(self, messages, *, provider=None, model=None, **kw):
        self.calls.append((model, len(messages)))
        if model in self.fail:
            raise RuntimeError(f"{model} down")
        return _FakeResp(content=self.answers.get(model, f"answer-from-{model}"))


@pytest.mark.asyncio
async def test_single_model_shortcut_no_debate() -> None:
    gw = _FakeGateway({"m1": "solo answer"})
    council = ModelCouncil(gw, models=["m1"], rounds=2)
    result = await council.deliberate([{"role": "user", "content": "hi"}])
    assert result.final == "solo answer"
    assert result.rounds_run == 0
    assert result.participating_models == ["m1"]


@pytest.mark.asyncio
async def test_multi_model_synthesizes() -> None:
    gw = _FakeGateway(
        {"m1": "draft one", "m2": "draft two", "synth": "FUSED BEST"}
    )
    council = ModelCouncil(gw, models=["m1", "m2"], rounds=0, synth_model="synth")
    result = await council.deliberate([{"role": "user", "content": "q"}])
    assert result.final == "FUSED BEST"
    assert set(result.participating_models) == {"m1", "m2"}
    # synth model was invoked
    assert any(c[0] == "synth" for c in gw.calls)


@pytest.mark.asyncio
async def test_debate_rounds_run() -> None:
    gw = _FakeGateway({"m1": "a", "m2": "b", "synth": "final"})
    council = ModelCouncil(gw, models=["m1", "m2"], rounds=2, synth_model="synth")
    result = await council.deliberate([{"role": "user", "content": "q"}])
    assert result.rounds_run == 2
    # round 0 (2) + 2 rounds (2 each) + 1 synth = 7 calls
    assert len(gw.calls) == 7


@pytest.mark.asyncio
async def test_failed_member_skipped_and_fallback() -> None:
    gw = _FakeGateway({"m1": "only good", "m2": ""}, fail={"m2", "synth"})
    council = ModelCouncil(gw, models=["m1", "m2"], rounds=0, synth_model="synth")
    result = await council.deliberate([{"role": "user", "content": "q"}])
    # m2 failed, synth failed → deterministic fallback to longest draft (m1)
    assert result.final == "only good"
    assert result.participating_models == ["m1"]


@pytest.mark.asyncio
async def test_no_models_returns_empty() -> None:
    gw = _FakeGateway({})
    council = ModelCouncil(gw, models=[], rounds=1)
    result = await council.deliberate([{"role": "user", "content": "q"}])
    assert result.final == ""


def test_council_from_settings_parses_csv() -> None:
    class _Cfg:
        council_models = "a, b ,c"
        council_rounds = 2
        council_synth_model = "a"

    council = council_from_settings(_FakeGateway({}), _Cfg())
    assert council.n_models == 3
