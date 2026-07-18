# Integrating Phase 3 with Phase 2 (LangGraph)

The Phase 2 multi-agent pipeline (SCOUT / SOURCER / AUDITOR /
SENTINEL / COMPLIANCE / GEO_ARBITRAGE / NARRATIVE / HISTORIAN /
HEDGE / RED_TEAM) treats Phase 3 as an **augmentation layer**, not a
replacement. SCOUT and SENTINEL get the most value:

* **SCOUT** — uses Phase 3's `p_breakout` at horizon=24h to refine
  its trend-discovery verdict.
* **SENTINEL** — uses Phase 3's `p_decline` at horizon=6h to refine
  its saturation-detection verdict.

The other agents can call Phase 3 for context but don't need to gate
on it.

## Wire-up: SCOUT

```python
# In aegis/agents/nodes/scout.py
from aegis.agents.schemas import AgentDecision, GraphState
from aegis.agents_phase3_glue.bridge import enrich_scout_decision


async def scout_node(state: GraphState) -> GraphState:
    # 1. Run SCOUT's own logic first — heuristic stage classifier,
    #    velocity gate, etc. Produces an initial AgentDecision.
    initial = run_scout_heuristics(state)

    # 2. Enrich with Phase 3.
    enriched_state = await enrich_scout_decision(
        {
            "trend_id": state["trend_id"],
            "tenant_id": state["tenant_id"],
            "signals": state["signals"],
        },
        primary_horizon=24,
    )
    phase3_decision_dict = enriched_state.get("phase3_decision")
    if phase3_decision_dict is None:
        # Phase 3 unavailable — return the SCOUT-only decision.
        return {**state, "scout_decision": initial}

    phase3_agent_decision = AgentDecision.model_validate(phase3_decision_dict)

    # 3. Combine the two decisions. Phase 2's "weighted vote by
    #    confidence" rule applies — the agent with the higher
    #    confidence wins, with RED_TEAM holding a unilateral block.
    combined = combine_decisions(initial, phase3_agent_decision)
    return {**state, "scout_decision": combined}
```

## Wire-up: SENTINEL

```python
from aegis.agents_phase3_glue.bridge import enrich_sentinel_decision


async def sentinel_node(state: GraphState) -> GraphState:
    initial = run_sentinel_heuristics(state)
    enriched = await enrich_sentinel_decision(
        {
            "trend_id": state["trend_id"],
            "tenant_id": state["tenant_id"],
            "signals": state["signals"],
        },
        primary_horizon=6,    # SENTINEL cares about short-horizon decay
    )
    phase3_dict = enriched.get("phase3_decision")
    if phase3_dict is None:
        return {**state, "sentinel_decision": initial}
    return {
        **state,
        "sentinel_decision": combine_decisions(
            initial, AgentDecision.model_validate(phase3_dict)
        ),
    }
```

## Verdict mapping (canonical)

The bridge maps Phase 3's `PredictionAction` to Phase 2's verdicts:

| `PredictionAction` | Phase 2 verdict | halt |
|---|---|---|
| `ENTER`     | `advance` | False |
| `HOLD`      | `hold`    | False |
| `OBSERVE`   | `hold`    | False |
| `EXIT`      | `exit`    | True  |
| `AVOID`     | `block`   | True  |

This mapping lives in `agents_phase3_glue/bridge.py` constants
`_ACTION_TO_VERDICT` and `_ACTION_TO_HALT`. To extend, add a row to
both dicts.

## Score axis per agent

* **SCOUT** uses `p_breakout` as `score`.
* **SENTINEL** uses `p_decline` as `score`.
* All other agents wrapping Phase 3 default to `p_breakout`.

The bridge auto-detects via the `agent_name` argument — pass `"SCOUT"`
or `"SENTINEL"` for the right axis.

## Halt-reason propagation

Phase 3's halt_reasons (e.g. `latency_budget_exceeded`,
`predictor_timeout`, `low_confidence_below_action_floor`) are appended
verbatim to the agent's `halt_reasons` list. Phase 2's supervisor
surfaces them in the audit log so operators can see exactly why a
decision was blocked.

## Audit trail

Every Phase 3 enrichment carries:

* `phase3_bundle` — the full `PredictionBundle.model_dump(mode="json")`
* `phase3_audit` — the `AuditRecord.model_dump(mode="json")` if any

These are persisted by Phase 2's supervisor `finalize` node alongside
the agent's own decision, so a single `correlation_id` reconstructs
the entire decision chain.

## Performance

The bridge keeps the `InferenceRunner` cached on `state["_phase3_runner"]`
between agent invocations within a single graph tick. First-tick
overhead: ~100ms (predictor load). Subsequent enrichments: ~12ms p99.

For the typical SCOUT → SOURCER → AUDITOR pipeline that visits ~5
trends per tick, the runner is loaded once and reused.

## Disabling Phase 3 in Phase 2

Set `AEGIS_PHASE3_ENABLED=0` in the env. The bridge functions become
no-ops returning the input state unchanged. Phase 2 falls back to its
own heuristics with no behaviour change other than the absence of
`phase3_decision` in the final state.

This is useful when:

* Debugging Phase 2 in isolation.
* Phase 3 is undergoing a bad-deploy rollback and you want to disable
  it instantly without touching code.
* A specific tenant has opted out of ML-augmented trend detection.

Implementation:

```python
# At top of bridge.py
import os
_ENABLED = os.environ.get("AEGIS_PHASE3_ENABLED", "1") != "0"

async def enrich_scout_decision(state, *, primary_horizon=24):
    if not _ENABLED:
        return state
    return await _enrich(state, agent_name="SCOUT", primary_horizon=primary_horizon)
```

(This guard is documented but not yet wired in — add it to the bridge
when Phase 2 ships.)
