# OMEGA — Empty-Table Triage (KEEP / WIRE / DELETE)

Generated 2026-06-20 from live DB row counts + source-tree writer grep.
Method: `count(*)` on every public table; `grep "INSERT INTO <t>"` over `src/` and
`aegis-phase4/src/` to detect whether a runtime writer exists at all.

Legend:
- **KEEP** — populated and consumed; part of the working system.
- **DORMANT (wire or retire)** — a writer EXISTS in source but has never run / is
  gated off. Decide: trigger it, or delete the subsystem. Not safe to drop blindly.
- **DEAD (delete or build)** — ZERO writer in source. The schema is decorative.
  Either the feature was never finished or the table is superseded. Safe to drop
  unless you intend to build the missing writer.

---

## KEEP — the working core (populated, fresh, consumed)

| Table | Rows | Note |
|---|---|---|
| signals | 3714↑ | live ingest (fixed today — was inserting 0) |
| authors | 1807 | live ingest |
| signal_outcomes | 543 | **the one real learning loop** (settled hourly) |
| calibration_maps | 1 | fitted from signal_outcomes; now gates ENTER (OMEGA b) |
| opportunities / failures | 543 / 211 | memory; **read only by memory modules, not by the verdict** → advisory |
| alerts / alert_outbox / alert_deliveries | 19 ea | Phase 4 pipeline |
| source_profiles / trust_scores | 6 / 5 | advisory; candidates to wire into verdict next |
| tenants / rl_policy_state | 1 / 1 | config/state |

---

## DEAD — zero writer in source (delete, or build the writer)

| Table | Subsystem | Verdict |
|---|---|---|
| **predictions** | Phase 3 serving | DEAD — inference runs in-memory, never persisted. Either persist predictions (needed for real retraining ground truth) or drop the table + `prediction_audit`. **This is why retraining is starved.** |
| prediction_audit | Phase 3 | DEAD (same root cause) |
| model_manifest | Phase 3 registry | DEAD — no writer |
| backtest_results | Phase 3 backtest | DEAD — no writer |
| geo_opportunities / geo_price_snapshots / geo_fx_snapshots | Phase 7 | DEAD — geo is CLI-only, nothing persists. Delete tables or add a scheduled geo writer. |
| compliance_assessments | Phase 8 | DEAD — assessments computed but not stored (only `compliance_blocks` has a repo). |
| execution_plans | Phase 6 | DEAD — no writer |
| daily_settlements | Phase 6 | DEAD — no live trades to settle |
| velocity_snapshots | Phase 5 | DEAD — no writer; superseded by in-line analytics |
| mentor_sessions / user_profiles* | Phase M1 | DEAD — brand-new, never exercised (*user_profiles has a writer but 0 rows → DORMANT) |

## DORMANT — writer exists, never fired (wire the trigger or retire the phase)

| Table | Subsystem | Why empty |
|---|---|---|
| model_candidates, retrain_audit, drift_snapshots | Phase 9 evolve | `RetrainingPipeline` rejects runs below `min_outcomes_for_retrain`; only ground-truth source (`prediction_outcomes`) has 11 stale rows → never fires. Fix = persist `predictions` (above) **or** point retraining at `signal_outcomes` (543 rows, already settled). |
| lin_ucb_state | RL pricing | policy never invoked in a live execution path |
| execution_records | Phase D exec-intel | advisory mode; no live execution |
| buyer_demand, supplier_reliability | Phase D intel | writers exist; settlement hook never reached (no settled trades) |
| entities, entity_outcomes, knowledge_edges, knowledge_gaps, market_epochs | Phase C knowledge | `job_knowledge_refresh` scheduled but producing nothing — needs runtime trace |
| swarm_results, swarm_agent_health | swarm | swarm persists to **Redis** by design; PG tables redundant → candidate DELETE |
| daily_settlements (dup), sanction_hits, trademark_cache | misc caches | unused caches |

---

## Recommended order of operations

1. **Highest ROI / unblocks retraining:** persist `predictions` from the Phase 3
   serving path → feeds `prediction_outcomes` → unblocks the Phase 9 DORMANT set.
   *Alternative, cheaper:* retarget `RetrainingPipeline` at `signal_outcomes`
   (already 543 settled rows) and delete the Phase 3 prediction tables.
2. **Wire the advisory data already proven good:** `source_profiles`/`trust_scores`
   into the verdict (next step after OMEGA-b calibration gate).
3. **Decide per dead subsystem (geo / compliance-assess / execution / mentor):**
   either schedule a writer or drop the migration tables. Do not leave decorative
   schema that operators mistake for a working engine.
4. **Delete redundant:** `swarm_results`/`swarm_agent_health` PG tables (Redis is
   the source of truth), `velocity_snapshots`, unused caches.

> Brutal summary: ~35 of ~50 tables are empty. ~10 are DEAD (no writer at all);
> the rest are DORMANT behind a single root cause — **predictions are never
> persisted, so the ground-truth → retraining chain can never start.**
