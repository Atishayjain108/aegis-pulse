"""Multi-model council orchestration — debate → critique → synthesize.

The council queries several local models with the *same* prompt, optionally
runs N refinement rounds where each model sees the others' answers and revises
its own, then a synthesizer model fuses everything into a single best answer.

This is the "models orchestrate and analyse each other's responses in a loop
to get the best output" capability. It runs entirely on local Ollama models —
zero paid APIs — and degrades gracefully to a single-model `complete()` when
the gateway has fewer than two healthy models.

Design notes
------------
- **Heuristic-first compatible**: the council only enriches *reasoning text*.
  It never produces verdicts; numeric scoring stays deterministic upstream.
- **Bounded cost**: rounds are clamped [0, 3] and every model call carries the
  gateway's hard timeout. A slow/dead model is skipped, not awaited forever.
- **Deterministic fusion fallback**: if the synthesizer call fails, the longest
  coherent draft is returned so the pipeline always gets an answer.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from aegis.llm.gateway.gateway import LLMGateway

_log = structlog.get_logger("aegis.llm.council")


@dataclass(frozen=True)
class CouncilMemberOutput:
    """One model's contribution to the council."""

    model: str
    content: str
    round: int
    latency_ms: float = 0.0
    failed: bool = False


@dataclass
class CouncilResult:
    """Aggregate council output."""

    final: str
    members: list[CouncilMemberOutput] = field(default_factory=list)
    rounds_run: int = 0
    synth_model: str | None = None

    @property
    def participating_models(self) -> list[str]:
        return sorted({m.model for m in self.members if not m.failed})


class ModelCouncil:
    """Orchestrates a debate loop across multiple local models."""

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        models: list[str],
        rounds: int = 1,
        synth_model: str | None = None,
        provider: str = "ollama",
        temperature: float = 0.4,
    ) -> None:
        # De-duplicate while preserving order.
        seen: set[str] = set()
        self._models = [m for m in models if m and not (m in seen or seen.add(m))]
        self._rounds = max(0, min(rounds, 3))
        self._synth_model = synth_model or (self._models[0] if self._models else None)
        self._gateway = gateway
        self._provider = provider
        self._temperature = temperature

    @property
    def n_models(self) -> int:
        """Number of distinct council members."""
        return len(self._models)

    async def _ask(self, model: str, messages: list[dict[str, str]], rnd: int) -> CouncilMemberOutput:
        try:
            resp = await self._gateway.complete(
                messages,
                provider=self._provider,
                model=model,
                temperature=self._temperature,
                skip_router=True,
            )
            return CouncilMemberOutput(
                model=model, content=resp.content.strip(), round=rnd, latency_ms=resp.latency_ms
            )
        except Exception as exc:
            _log.warning("council.member_failed", model=model, round=rnd, error=str(exc)[:200])
            return CouncilMemberOutput(model=model, content="", round=rnd, failed=True)

    async def deliberate(
        self,
        messages: list[dict[str, str]],
        *,
        system: str | None = None,
    ) -> CouncilResult:
        """Run the full council loop and return the fused answer."""
        base = list(messages)
        if system:
            base = [{"role": "system", "content": system}, *base]

        # Single-model shortcut — no point debating with yourself.
        if len(self._models) < 2:
            model = self._models[0] if self._models else None
            if model is None:
                return CouncilResult(final="", members=[], rounds_run=0)
            out = await self._ask(model, base, 0)
            return CouncilResult(final=out.content, members=[out], rounds_run=0, synth_model=model)

        all_members: list[CouncilMemberOutput] = []

        # ---- Round 0: independent drafts (in parallel) ---------------------
        drafts = await asyncio.gather(*(self._ask(m, base, 0) for m in self._models))
        all_members.extend(drafts)
        current = {d.model: d.content for d in drafts if not d.failed and d.content}

        # ---- Rounds 1..N: each model critiques + revises -------------------
        rounds_run = 0
        for rnd in range(1, self._rounds + 1):
            if len(current) < 2:
                break
            rounds_run = rnd
            peer_block = "\n\n".join(
                f"### Answer from {mdl}:\n{txt}" for mdl, txt in current.items()
            )
            revise_msgs = [
                *base,
                {
                    "role": "user",
                    "content": (
                        "Other expert models produced these answers to the same task:\n\n"
                        f"{peer_block}\n\n"
                        "Critique them, then produce your single best improved answer. "
                        "Keep what is correct, fix errors, add what is missing. "
                        "Output only the improved answer, no meta-commentary."
                    ),
                },
            ]
            revised = await asyncio.gather(
                *(self._ask(m, revise_msgs, rnd) for m in current)
            )
            all_members.extend(revised)
            current = {r.model: r.content for r in revised if not r.failed and r.content} or current

        # ---- Synthesis -----------------------------------------------------
        final = await self._synthesize(base, current)
        return CouncilResult(
            final=final,
            members=all_members,
            rounds_run=rounds_run,
            synth_model=self._synth_model,
        )

    async def _synthesize(self, base: list[dict[str, str]], current: dict[str, str]) -> str:
        if not current:
            return ""
        if len(current) == 1:
            return next(iter(current.values()))

        merged = "\n\n".join(f"### {mdl}:\n{txt}" for mdl, txt in current.items())
        synth_msgs = [
            *base,
            {
                "role": "user",
                "content": (
                    "Below are final answers from multiple expert models for the task above:\n\n"
                    f"{merged}\n\n"
                    "Synthesize them into one authoritative answer that is more accurate "
                    "and complete than any individual answer. Resolve disagreements by "
                    "favoring the most evidence-grounded claim. Output only the final answer."
                ),
            },
        ]
        if self._synth_model is None:
            return max(current.values(), key=len)
        out = await self._ask(self._synth_model, synth_msgs, rnd=self._rounds + 1)
        if out.failed or not out.content:
            # Deterministic fallback: the longest non-empty draft.
            return max(current.values(), key=len)
        return out.content


def council_from_settings(gateway: LLMGateway, cfg: object) -> ModelCouncil:
    """Build a ModelCouncil from an LLMSettings-like object."""
    models = [m.strip() for m in getattr(cfg, "council_models", "").split(",") if m.strip()]
    return ModelCouncil(
        gateway,
        models=models,
        rounds=int(getattr(cfg, "council_rounds", 1)),
        synth_model=getattr(cfg, "council_synth_model", None) or None,
    )


__all__ = ["CouncilMemberOutput", "CouncilResult", "ModelCouncil", "council_from_settings"]
