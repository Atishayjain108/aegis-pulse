"""Priority classifier (deterministic).

Given a composer input, assigns a P0..P3 priority. The rules:

  P0  ENTER + p_breakout_24h ≥ 0.80 + combined_confidence ≥ 0.70
  P0  EXIT  + p_decline_6h   ≥ 0.80 + combined_confidence ≥ 0.70
  P1  ENTER + p_breakout_24h ≥ 0.60 + combined_confidence ≥ 0.55
  P1  EXIT  + p_decline_6h   ≥ 0.60 + combined_confidence ≥ 0.55
  P2  every other ENTER / EXIT / HOLD
  P3  BLOCK / DEGRADED (informational)

Phase 4 may *escalate* a Phase 2 priority but never *demotes* one — the
upstream may have its own reason for prioritizing.
"""

from __future__ import annotations

from aegis.execute.bridge.types import ComposerInput
from aegis.execute.constants import (
    P0_BREAKOUT_PROB_FLOOR,
    P0_CONFIDENCE_FLOOR,
    P1_BREAKOUT_PROB_FLOOR,
    P1_CONFIDENCE_FLOOR,
    PRIORITY_P0_CRITICAL,
    PRIORITY_P1_HIGH,
    PRIORITY_P2_NORMAL,
    PRIORITY_P3_INFO,
    VERDICT_BLOCK,
    VERDICT_DEGRADED,
    VERDICT_ENTER,
    VERDICT_EXIT,
)


def classify_priority(
    *,
    verdict: str,
    composer_input: ComposerInput,
    combined_confidence: float,
    upstream_priority: int | None,
) -> int:
    """Return P0..P3 priority.

    `upstream_priority` is Phase 2's `final_priority` if available.
    """
    if verdict in (VERDICT_BLOCK, VERDICT_DEGRADED):
        return PRIORITY_P3_INFO

    p_breakout = composer_input.phase3_p_breakout_24h or 0.0
    p_decline = composer_input.phase3_p_decline_6h or 0.0

    if verdict == VERDICT_ENTER:
        if (
            p_breakout >= P0_BREAKOUT_PROB_FLOOR
            and combined_confidence >= P0_CONFIDENCE_FLOOR
        ):
            computed = PRIORITY_P0_CRITICAL
        elif (
            p_breakout >= P1_BREAKOUT_PROB_FLOOR
            and combined_confidence >= P1_CONFIDENCE_FLOOR
        ):
            computed = PRIORITY_P1_HIGH
        else:
            computed = PRIORITY_P2_NORMAL
    elif verdict == VERDICT_EXIT:
        if (
            p_decline >= P0_BREAKOUT_PROB_FLOOR
            and combined_confidence >= P0_CONFIDENCE_FLOOR
        ):
            computed = PRIORITY_P0_CRITICAL
        elif (
            p_decline >= P1_BREAKOUT_PROB_FLOOR
            and combined_confidence >= P1_CONFIDENCE_FLOOR
        ):
            computed = PRIORITY_P1_HIGH
        else:
            computed = PRIORITY_P2_NORMAL
    else:
        # HOLD or anything unrecognised → P2
        computed = PRIORITY_P2_NORMAL

    # Never demote upstream's priority — pick the more urgent (smaller int).
    if upstream_priority is not None:
        return min(computed, int(upstream_priority))
    return computed


__all__ = ["classify_priority"]
