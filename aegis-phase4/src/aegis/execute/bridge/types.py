"""Bridge data carrier.

`ComposerInput` is a plain frozen dataclass — not Pydantic — because it is
internal to Phase 4 and constructed millions of times in the hot path.
Pydantic validation costs are unnecessary here; we already validated at
the bridge boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ComposerInput:
    """Normalized input to the policy composer.

    All upstream variability is collapsed into this flat structure.

    Either or both of (phase2_*, phase3_*) groups may be populated. The
    composer handles every combination including 'both missing' (error).
    """

    tenant_id: UUID
    trend_id: str
    decision_window: str = "default"
    correlation_id: str | None = None

    # ----- Phase 2 group (None means absent) -----
    phase2_verdict: str | None = None  # ENTER / HOLD / EXIT / BLOCK / DEGRADED
    phase2_score: float | None = None
    phase2_confidence: float | None = None
    phase2_priority: int | None = None
    phase2_halt_reason: str | None = None
    phase2_blocked_by: tuple[str, ...] = field(default_factory=tuple)
    phase2_title: str | None = None
    phase2_narrative: str = ""  # LLM-augmented text from supervisor
    phase2_explanation: str = ""  # Causal explanation from explainer layer
    phase2_primary_drivers: tuple[str, ...] = field(default_factory=tuple)

    # ----- Phase 3 group (None means absent) -----
    phase3_p_breakout_24h: float | None = None
    phase3_p_decline_6h: float | None = None
    phase3_p_saturation: float | None = None
    phase3_expected_margin_usd: float | None = None
    phase3_loss_probability: float | None = None
    phase3_confidence: float | None = None
    phase3_policy_action: str | None = None  # e.g. "enter" / "hold" / "exit"

    # ----- Optional sizing context -----
    capital_budget_usd: float | None = None


__all__: Final = ["ComposerInput"]
