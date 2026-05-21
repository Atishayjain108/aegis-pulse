"""Phase 2 → Phase 4 bridge.

Accepts a Phase 2 `GraphResult` (or any duck-typed equivalent) and produces
a `ComposerInput`. Never imports `aegis.agents` at the top of any function
that runs in tests — type hints are forward strings.

A `GraphResult` is expected to expose (per CLAUDE.md):
    .trend_id          : str
    .final_verdict     : str        — "ENTER" / "HOLD" / "EXIT" / "BLOCK"
    .final_score       : float ∈ [0, 1]
    .final_confidence  : float ∈ [0, 1]
    .final_priority    : int ∈ {0, 1, 2, 3}
    .halt_reason       : str | None
    .blocked_by        : list[str] | tuple[str, ...]
    .decisions         : Iterable[AgentDecision]  (we don't traverse here)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from uuid import UUID

if TYPE_CHECKING:
    from aegis.execute.bridge.types import ComposerInput


@runtime_checkable
class _GraphResultLike(Protocol):
    """Structural type for Phase 2 GraphResult."""

    trend_id: str
    final_verdict: str
    final_score: float
    final_confidence: float
    final_priority: int
    halt_reason: str | None
    blocked_by: Any
    decisions: Any


def _as_tuple_of_str(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    try:
        return tuple(str(v) for v in value)
    except TypeError:
        return ()


def _safe_str(value: Any, fallback: str = "") -> str:
    return str(value) if value is not None else fallback


def from_graph_result(
    result: _GraphResultLike,
    *,
    tenant_id: UUID,
    decision_window: str = "default",
    correlation_id: str | None = None,
    capital_budget_usd: float | None = None,
    narrative_text: str = "",
    title_override: str | None = None,
) -> ComposerInput:
    """Bridge a Phase 2 GraphResult → ComposerInput.

    Raises:
        TypeError: if `result` does not satisfy the structural protocol.
    """
    # Defensive: import the dataclass here to keep module import cheap.
    from aegis.execute.bridge.types import ComposerInput

    if not isinstance(result, _GraphResultLike):  # type: ignore[arg-type]
        raise TypeError(
            "from_graph_result: argument does not look like a GraphResult "
            "(missing one of: trend_id, final_verdict, final_score, "
            "final_confidence, final_priority, halt_reason, blocked_by, decisions)"
        )

    trend_id = str(result.trend_id)
    # Map Phase 2 AgentVerdict → Phase 4 vocabulary (ENTER/HOLD/EXIT/BLOCK).
    # AgentVerdict values are lowercase ("proceed", "hold", "block", "escalate").
    _MAP = {"PROCEED": "ENTER", "HOLD": "HOLD", "BLOCK": "BLOCK", "ESCALATE": "HOLD"}
    raw = str(result.final_verdict).upper()
    verdict = _MAP.get(raw, raw)  # pass through unknown values (e.g. already-mapped "ENTER")
    title = title_override or f"Trend {trend_id} — {verdict}"

    return ComposerInput(
        tenant_id=tenant_id,
        trend_id=trend_id,
        decision_window=decision_window,
        correlation_id=correlation_id,
        phase2_verdict=verdict,
        phase2_score=float(result.final_score),
        phase2_confidence=float(result.final_confidence),
        phase2_priority=int(result.final_priority),
        phase2_halt_reason=_safe_str(result.halt_reason) or None,
        phase2_blocked_by=_as_tuple_of_str(result.blocked_by),
        phase2_title=title,
        phase2_narrative=narrative_text,
        capital_budget_usd=capital_budget_usd,
    )


__all__ = ["from_graph_result"]
