"""Persuader — the conviction engine (evidence-first, never hype).

AEGIS must be able to convince even a skeptical person, but **only** by making
the practical, evidence-backed case so clearly that doubt has nowhere to stand.
This module is the cross-cutting tool the :class:`MentorAgent` and
:class:`~aegis.mentor.operators.customer.CustomerOp` use for that.

Four disciplines (from the blueprint's conviction engine), all bound by
grounded-or-silent:

* **Evidence-first** — every persuasive point carries a real number + its source.
  :class:`Evidence` requires a numeric ``value``; the Persuader never invents one.
* **Objection modeling** — it anticipates the three classic objections
  ("too saturated", "no capital", "won't sell") and answers EACH with a concrete
  real number drawn from the supplied evidence plus a practical, affordable step.
* **Calibrated honesty** — it does not overclaim. When NO evidence is supplied it
  **refuses to persuade** (``refused=True``) and stays silent rather than
  fabricating a case. An objection with no matching numeric evidence is dropped,
  not answered with a guess.
* **Practical next step** — each rebuttal lands a small, do-able action (a free
  channel, a test order) — never a grand abstract plan.

Heuristic-first: zero LLM, zero network. Pure deterministic synthesis over the
numbers it is handed.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Grounded value objects
# ---------------------------------------------------------------------------


class Evidence(BaseModel, frozen=True):
    """One real number, what it means, and where it came from.

    ``kind`` lets the objection matcher pick the right number for the right
    doubt. The Persuader treats ``value`` as ground truth supplied by a grounded
    caller — it never manufactures a number of its own.
    """

    label: str
    value: float
    unit: str = ""           # "%", "signals", "INR", ""
    source: str = ""
    # margin | growth | demand | audience | capital | cost | general
    kind: str = "general"

    def render(self) -> str:
        """Human-readable ``label: value unit (source)`` with no embellishment."""
        num = f"{self.value:g}{self.unit}"
        src = f" [{self.source}]" if self.source else ""
        return f"{self.label}: {num}{src}"


class Objection(BaseModel, frozen=True):
    """A modeled doubt answered with a real number + a do-able next step."""

    concern: str
    rebuttal: str
    next_step: str
    evidence_label: str
    source: str = ""


class PersuasionCase(BaseModel, frozen=True):
    """The grounded case: evidence points + answered objections.

    ``refused`` is True (with ``reason``) when there is no evidence to stand on —
    the Persuader stays silent rather than hype an empty hand.
    """

    points: list[str] = Field(default_factory=list)
    objections: list[Objection] = Field(default_factory=list)
    refused: bool = False
    reason: str = ""

    @property
    def has_case(self) -> bool:
        return not self.refused and bool(self.points)

    def render_lines(self) -> list[str]:
        """Flat list a Counsel can carry — points first, then objection answers."""
        lines = list(self.points)
        for o in self.objections:
            lines.append(f"Objection — {o.concern}: {o.rebuttal} Next: {o.next_step}")
        return lines


# ---------------------------------------------------------------------------
# Objection model
# ---------------------------------------------------------------------------

# Each modeled objection is answered ONLY by evidence whose ``kind`` is in
# ``kinds`` (priority order). No matching evidence → the objection is dropped
# (silent), never answered with a fabricated number.
_OBJECTION_MODEL: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("too saturated", ("margin", "growth", "demand")),
    ("no capital", ("capital", "cost")),
    ("won't sell", ("demand", "audience")),
)


class Persuader:
    """Evidence-first case builder + objection model. Grounded or silent."""

    def __init__(self, *, default_step: str | None = None) -> None:
        # A practical fallback action when an objection has no channel-specific one.
        self._default_step = (
            default_step
            or "Run one small, cheap test (e.g. a single listing or a free post) and "
            "measure the real response before committing money."
        )

    # ------------------------------------------------------------------

    def build_case(
        self,
        evidence: list[Evidence],
        *,
        free_channels: list[str] | None = None,
    ) -> PersuasionCase:
        """Turn grounded evidence into a calibrated, objection-aware case.

        ``free_channels`` (grounded, e.g. from a SectorPack) are used as the
        concrete next step for the "no capital" objection.
        """
        real = list(evidence)  # Evidence.value is a validated float — always grounded
        if not real:
            # Calibrated honesty: nothing to stand on → refuse, stay silent.
            return PersuasionCase(
                refused=True,
                reason=(
                    "No grounded evidence to persuade with. Withholding a case rather "
                    "than overclaiming (grounded-or-silent)."
                ),
            )

        points = [f"Evidence: {e.render()}" for e in real]
        objections = self._model_objections(real, free_channels or [])
        return PersuasionCase(points=points, objections=objections)

    # ------------------------------------------------------------------

    def _model_objections(
        self, evidence: list[Evidence], free_channels: list[str]
    ) -> list[Objection]:
        objections: list[Objection] = []
        for concern, kinds in _OBJECTION_MODEL:
            ev = self._best_evidence(evidence, kinds)
            if ev is None:
                continue  # no real number for this doubt → drop it, never guess
            objections.append(
                Objection(
                    concern=concern,
                    rebuttal=self._rebuttal(concern, ev),
                    next_step=self._step(concern, free_channels),
                    evidence_label=ev.label,
                    source=ev.source,
                )
            )
        return objections

    @staticmethod
    def _best_evidence(
        evidence: list[Evidence], kinds: tuple[str, ...]
    ) -> Evidence | None:
        # Pick the first evidence matching the highest-priority kind available.
        for kind in kinds:
            for e in evidence:
                if e.kind == kind:
                    return e
        return None

    @staticmethod
    def _rebuttal(concern: str, ev: Evidence) -> str:
        """Neutral, number-led answer. No superlatives, no hype."""
        num = f"{ev.value:g}{ev.unit}"
        src = f" (source: {ev.source})" if ev.source else ""
        if concern == "too saturated":
            return (
                f"Saturation is a claim, not a measurement — the measured {ev.label} "
                f"is {num}{src}, which is the number to weigh, not the noise."
            )
        if concern == "no capital":
            return (
                f"The measured {ev.label} here is {num}{src}, low enough that a first "
                "step needs little to no capital."
            )
        # won't sell
        return (
            f"Demand here is an observed proxy, not a promise — but the measured "
            f"{ev.label} is {num}{src}, which is real signal worth testing."
        )

    def _step(self, concern: str, free_channels: list[str]) -> str:
        if concern == "no capital" and free_channels:
            return f"Start on a free channel first: {free_channels[0]}."
        return self._default_step
