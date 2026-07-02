# PROJECT OMEGA — PHASE C BLUEPRINT
## AEGIS Prime Knowledge Expansion Protocol

**Status:** AWAITING ROADMAP APPROVAL — no implementation has begun.
**Author:** AEGIS engineering pass, 2026-06-14
**Predecessors (assumed complete, not to be repeated):** Phase A (Reality Loop / `signal_outcomes`), Phase B (Trust Reconstruction / calibration + trust scores), Phase B.5 / Confidence Audit (calibrated `1 - p_decline`).

---

## 0. THESIS (Knowledge First — Rule 1 / Rule 12)

AEGIS today is **stateless across time**. Every scrape → score → predict cycle starts cold:
- A trend it nailed last month teaches it nothing this month.
- A source that lied 10 times is trusted the same as one that never has — *except* the narrow `trust_scores` table from Phase B.
- A failed prediction is settled (`signal_outcomes.resolution_status='incorrect'`) and then **forgotten** — the *reason* it failed is never captured.
- An entity ("Nike", "Flipkart", HS code 610910, region IN) appears in compliance/geo checks but has no memory of how it behaved last time.

Phase C's single job: **make the system accumulate verified knowledge so decision quality compounds.** Every component below must answer Rule 1's four questions (new knowledge / how stored / how verified / how it improves future decisions). Components that cannot are explicitly **rejected** in §11.

We do **not** add new prediction models, new domains of scraping, or new agents. We add **memory + verification on top of the loops that already settle to ground truth.**

---

## 1. CURRENT STATE ASSESSMENT (Rule 11.1)

Verified by direct inspection of the repo (2026-06-14):

| Capability | Exists? | Where | Reusable for Phase C |
|---|---|---|---|
| Falsifiable outcome ground truth | ✅ | `signal_outcomes` (mig 0015), `SignalOutcomeSettler` | **Primary fact source** for Opportunity/Failure memory |
| Capital trade outcomes | ✅ | `prediction_outcomes`, `evolve/schemas.TradeOutcome` | Secondary outcome source (mostly empty in advisory mode) |
| Calibration + trust scoring | ✅ | `src/aegis/trust/` (calibration, calibrator, scores, store) | **Primary engine** for Source/Trust memory; entity_kind already model/source/agent/global |
| Trust persistence | ✅ | `trust_scores`, `calibration_snapshots` (0016), `calibration_maps` (0017), `TrustStore` | Extend, don't replace |
| Per-claim emission | ✅ | `trust/claim_emitter.py` (`ClaimEmitter`) | Hook point to write Opportunity records |
| Research engine | ✅ | `intelligence/research_engine.py` | Reused by Reality Verification Layer |
| Entity-shaped reference data | ⚠️ partial | `compliance/` (brands, regulations), `geo/` (regions, HS codes, tariffs) | Source of entity *attributes*; has **no outcome memory** |
| Drift / retrain | ✅ | `evolve/` (drift, retrain, outcomes) | Consumer of new memory features |
| Pattern detection | ✅ | `scrape/patterns.py`, `scrape/pattern_engine.py` | Source of recurring-pattern candidates |
| **Opportunity memory** | ❌ | — | **BUILD** |
| **Entity memory** | ❌ | — | **BUILD** |
| **Source memory (beyond trust score)** | ⚠️ | only `trust_scores` row per source | **EXTEND** |
| **Failure memory** | ❌ | — | **BUILD** |
| **Market memory** | ⚠️ | data lake Gold has daily rollups | **EXTEND via memory layer** |
| **Knowledge graph** | ❌ | — | **BUILD (lightweight, SQL-backed)** |
| **Reality Verification Layer** | ❌ | scattered: trust + research + compliance | **COMPOSE existing into one gate** |

**Grep confirmation:** no module named `*memory*`, `knowledge_graph`, `opportunity_memory`, etc. exists in `src/` or `db/`. This is greenfield on top of a mature outcome+trust substrate.

---

## 2. GAP ANALYSIS (Rule 11.2)

The eight primary goals map to gaps as follows:

1. **Opportunity Memory** — gap: outcomes are settled then dropped; no durable record linking signal→evidence→prediction→outcome→reason. *Fact source already exists (`signal_outcomes`); only persistence + enrichment missing.*
2. **Entity Memory** — gap: entities are recomputed every call; zero longitudinal tracking. *Hard gap.*
3. **Source Memory** — gap: `trust_scores` gives a single scalar per source but no freshness, manipulation-risk, or per-category accuracy. *Partial.*
4. **Failure Memory** — gap: total. `resolution_status='incorrect'` is the only failure trace; root cause never captured. *Hard gap.*
5. **Market Memory** — gap: Gold layer has aggregates but no "compare now vs history" interface and no seasonality model. *Partial.*
6. **Knowledge Graph** — gap: relationships implied by foreign keys but never materialized or queryable as a graph. *Hard gap.*
7. **Reality Verification Layer** — gap: trust + research + compliance exist but are not composed into a single pre-recommendation gate producing Reality/Trust/Evidence/Unknowns scores. *Composition gap, not a build-from-scratch gap.*
8. **Global Opportunity Framework** — gap: opportunity types are implicit (geo arb, product, B2B); no unified schema. *Schema gap.*

---

## 3. REUSABLE COMPONENTS (Rule 11.3)

**Hard rule: reuse before build.**

- **`trust/` package** → the entire calibration + scoring engine is reused verbatim for Source Memory and Trust Memory. `entity_kind` already supports `source`/`model`/`agent`; we add `product`/`brand`/`region` etc. as new `entity_kind` values — *no engine change*.
- **`signal_outcomes` + `SignalOutcomeSettler`** → the settler already computes `observed_direction` vs `claimed_direction`. The Opportunity Memory writer subscribes to settlement events; the Failure Memory writer classifies the `incorrect` rows.
- **`ClaimEmitter`** → already the single chokepoint where a prediction commits to a falsifiable claim. We add one fire-and-forget call to write the Opportunity record there. No new prediction path.
- **`TrustStore` + asyncpg pool pattern + RLS** → all new tables follow the exact `app.current_tenant` RLS + `gen_random_uuid` + hypertable conventions already established in migrations 0001–0017.
- **`intelligence/research_engine.py`** → the Reality Verification Layer calls it for contradiction/evidence checks rather than reimplementing.
- **Data lake Gold aggregates** → Market Memory reads from Gold rather than recomputing.
- **`compliance/` + `geo/` reference data** → seed entity attributes (brand list, region configs, HS schedule).

---

## 4. MISSING COMPONENTS — what we actually build (Rule 11.4)

A new top-level package: **`src/aegis/memory/`** (regular subpackage of `aegis`, NOT a workspace member — same convention as `evolve/`, `geo/`, `compliance/`).

```
src/aegis/memory/
  __init__.py            # exports the public façade + version
  schemas.py             # frozen Pydantic v2 models (Opportunity, Entity, SourceProfile, Failure, MarketEpoch, GraphEdge)
  opportunity.py         # OpportunityMemory: record / query / patterns
  entity.py              # EntityMemory: upsert / track / relate
  source.py              # SourceMemory: extends trust with freshness + manipulation + per-category accuracy
  failure.py             # FailureMemory: classify settled-incorrect outcomes into reusable knowledge
  market.py              # MarketMemory: epoch snapshots + now-vs-history comparison
  graph.py               # KnowledgeGraph: SQL-backed edge store + relationship discovery queries
  verify.py              # RealityVerifier: composes trust+research+contradiction → Reality/Trust/Evidence/Unknowns scores
  store.py               # MemoryStore: asyncpg persistence for all of the above (mirrors TrustStore)
  taxonomy.py            # unified OpportunityType / EntityKind enums (Rule 9 global framework)
  report.py              # weekly self-audit report generator (Rule 10)
  cli.py                 # `aegis memory` command group
  api.py                 # FastAPI router /memory/* for dashboard
```

Plus an **opportunity ingestion hook** in `ClaimEmitter` and a **settlement hook** in `SignalOutcomeSettler`, and a **scheduler job** for weekly self-audit + entity/source trust refresh.

---

## 5. DATABASE / SCHEMA CHANGES (Deliverable 3)

One migration: **`0018_knowledge_memory.sql`**. All tables: RLS on `app.current_tenant`, `gen_random_uuid()` PKs, JSONB for open-ended fields, follow existing conventions.

### 5.1 `opportunities` (the durable opportunity ledger — Rule 2)
```
opportunity_id UUID, tenant_id UUID,
opportunity_type TEXT  -- taxonomy.OpportunityType (product/service/software/ai/manufacturing/logistics/info_arb/geo_arb/regulatory_arb)
category TEXT, region TEXT,
trend_key TEXT,                       -- links to signal_outcomes.trend_key
source_signal_ids JSONB,              -- evidence handles
evidence JSONB,                       -- snapshot of features/signals at decision time
trust_score REAL, reality_score REAL, evidence_score REAL, unknowns_score REAL,
prediction_direction TEXT, prediction_score REAL, prediction_confidence REAL,
outcome TEXT CHECK (outcome IN ('pending','realized','failed','expired')),
failure_id UUID NULL,                 -- FK→failures when outcome='failed'
realization_path JSONB,               -- how it actually played out
decay_horizon_hours INT,
created_at, settled_at, settlement_timestamp (hypertable key)
```
Hypertable on `settlement_timestamp`. Indexes on `(opportunity_type, outcome)`, `(trend_key)`, `(region)`.

### 5.2 `entities` + `entity_outcomes` (Rule 3)
```
entities: entity_id UUID, tenant_id, entity_kind TEXT (product/brand/company/supplier/marketplace/region/country/regulation/technology/software),
  canonical_name TEXT, attributes JSONB, trust REAL DEFAULT 0.5,
  first_seen, last_seen, n_observations INT, metadata JSONB
  UNIQUE(tenant_id, entity_kind, canonical_name)
entity_outcomes: entity_id, opportunity_id, outcome TEXT, contribution REAL, observed_at
  -- longitudinal behavior per entity
```

### 5.3 `source_profiles` (extends trust — Rule 4)
```
source_id TEXT (=platform/adapter name), tenant_id,
trust REAL,                  -- mirrors trust_scores, refreshed from it
freshness_score REAL,        -- recency of useful signals
reliability_score REAL,      -- settled-correct rate of opportunities sourced here
manipulation_risk REAL,      -- coordination/burst anomaly rate
per_category_accuracy JSONB, -- {category: accuracy}
n_signals INT, n_outcomes INT, updated_at
PRIMARY KEY (tenant_id, source_id)
```

### 5.4 `failures` (Rule 5)
```
failure_id UUID, tenant_id, opportunity_id, trend_key,
failure_category TEXT,       -- e.g. false_breakout / decayed_too_fast / source_unreliable / evidence_thin / overconfident
root_cause TEXT,
evidence_quality REAL, missing_information JSONB,
confidence_error REAL,       -- predicted_conf - observed_correctness
detected_at, settlement_timestamp (hypertable key)
```

### 5.5 `market_epochs` (Rule 6)
```
epoch_id UUID, tenant_id, period_start, period_end,
category TEXT, region TEXT,
trend_summary JSONB,         -- top trends, volumes
demand_index REAL, seasonality_tag TEXT,
recurring_pattern_ids JSONB, created_at
```
(Reads from Gold; this is a compressed historical index for fast now-vs-history compare.)

### 5.6 `knowledge_edges` (Rule 7 — SQL-backed graph, not a graph DB)
```
edge_id UUID, tenant_id,
src_kind TEXT, src_id TEXT,
dst_kind TEXT, dst_id TEXT,
relation TEXT,               -- signal_of / opportunity_of / sourced_by / outcome_of / trusted_via / caused_by
weight REAL, evidence_count INT,
first_seen, last_seen, metadata JSONB
UNIQUE(tenant_id, src_kind, src_id, dst_kind, dst_id, relation)
```
Relationship discovery = SQL aggregation over edges (no Neo4j; NetworkX already in deps from Pass 9 for in-memory analysis if needed).

**No existing table is altered.** Migration is purely additive → trivially reversible (drop new tables).

---

## 6. FILES AFFECTED (Deliverable 4)

**New:** the 12 files under `src/aegis/memory/`, `db/migrations/0018_knowledge_memory.sql`, `tests/unit/memory/` (one test file per module), `docs/adr/0018-knowledge-memory.md`, `docs/PHASE_C_MEMORY.md`.

**Edited (minimal, additive hooks only):**
- `src/aegis/trust/claim_emitter.py` — one `await opportunity_memory.record(...)` fire-and-forget after a claim is emitted.
- `src/aegis/evolve/outcomes.py` (or the `SignalOutcomeSettler`) — on settlement, call `opportunity_memory.settle(...)` + `failure_memory.classify(...)`.
- `src/aegis/cli/main.py` — register `aegis memory` group.
- `src/aegis/api/main.py` (or unified :8400 api) — mount `/memory/*` router.
- `src/aegis/scheduler/autonomous.py` — add weekly `job_self_audit` + daily `job_refresh_source_trust`.
- `src/aegis/dashboard/app.py` + `static/index.html` — one new "Knowledge" panel (best/worst opportunities, source leaderboard).
- `pyproject.toml` — add `tests/unit/memory/` to testpaths; `memory/cli.py`+`api.py` to coverage omit (existing convention).

---

## 7. TESTS REQUIRED (Deliverable 5)

All mock the asyncpg pool (reuse Phase 13 `fake_pg_pool`) — no live DB needed in the main suite.

- `test_opportunity_memory.py` — record→settle→query; pattern aggregation; outcome transitions.
- `test_entity_memory.py` — upsert idempotency; longitudinal outcome tracking; trust update.
- `test_source_memory.py` — freshness/manipulation computation; per-category accuracy; sync from `trust_scores`.
- `test_failure_memory.py` — classification of an incorrect outcome into a `failures` row; confidence_error math.
- `test_market_memory.py` — epoch snapshot; now-vs-history comparison returns a delta.
- `test_knowledge_graph.py` — edge upsert dedup; relationship discovery query; weight accrual.
- `test_reality_verify.py` — composition produces 4 scores in [0,1]; thin-evidence → low evidence_score; contradiction → low reality_score.
- `test_taxonomy.py` — every OpportunityType maps to a scorer.
- `test_report.py` — weekly report includes best/worst predictions+sources+entities+failures (Rule 10).
- **Reality-gate falsifiability tests:** assert that every memory write is downstream of a *settled* outcome (no memory invented from unsettled predictions) — enforces Rule 12.

Target: ≥ 40 new tests; project coverage floor stays ≥ 78%.

---

## 8. MIGRATION PLAN (Deliverable 6)

1. Land `0018_knowledge_memory.sql` (additive, no locks on existing tables).
2. **Backfill** from existing settled `signal_outcomes` → populate `opportunities` + `failures` retroactively (we already have 247 settled outcomes from Phase A). One-shot CLI: `aegis memory backfill`.
3. Backfill `entities` from `compliance` brand DB + `geo` region configs (attributes only, trust=0.5 prior).
4. Sync `source_profiles` from existing `trust_scores`.
5. Build initial `knowledge_edges` from the backfilled opportunities (signal→opportunity→source→outcome).

Backfill is **idempotent** (ON CONFLICT DO NOTHING on natural keys) and re-runnable.

---

## 9. ROLLOUT STRATEGY (Deliverable 7)

Feature-flagged: `AEGIS_MEMORY_ENABLED` (default **true** but every hook is wrapped `try/except → log` so memory failure never breaks the prediction path — same graceful-degradation doctrine as harden/observability).

- **Stage 1 (read-only build):** ship package + migration + backfill. No write hooks active yet. Verify backfill correctness.
- **Stage 2 (capture):** enable `ClaimEmitter` + settler hooks. Memory accumulates live. No decisions changed yet — *pure observation*.
- **Stage 3 (consume):** wire `RealityVerifier` scores + source/entity trust into the verdict path as **advisory enrichment** (attached to output, does not yet flip verdicts).
- **Stage 4 (compound):** allow Reality/Trust scores to adjust confidence (bounded multiplier, like the neural-augmentation doctrine — can only *reduce* confidence, never fabricate). Gated on measured calibration improvement.

Each stage is independently shippable and reversible.

---

## 10. RISK ANALYSIS & MITIGATION (Rule 11.5 + Deliverable 8)

| Risk | Severity | Mitigation |
|---|---|---|
| Memory write failures break prediction path | High | All hooks fire-and-forget + `try/except`; flag kill-switch |
| Backfill contaminates with look-ahead | High | Reuse Phase A no-look-ahead invariant; only settled rows; baseline frozen at claim_ts |
| "Knowledge graph" balloons into a graph-DB project | Med | Hard constraint: SQL table + NetworkX in-memory only; no new infra service |
| Trust/reality scores become unfalsifiable theater | High (Rule 12) | Every score must be tested against a settled outcome; §7 falsifiability tests block merge |
| Coverage floor regression | Med | cli/api omitted per convention; ≥40 unit tests |
| Entity name collisions / dedup | Med | UNIQUE(kind, canonical_name) + normalization in `taxonomy.py` |
| Scope creep into new domains/models | High | Rule 1/9: reuse existing scoring; **no new predictors** — explicitly rejected in §11 |

---

## 11. ROI ANALYSIS + REJECTED WORK (Rule 11.6 + Rule 12 gate)

**Ranked by (impact × future-value) ÷ (effort × risk):**

| Rank | Component | Impact | Effort | Risk | Why |
|---|---|---|---|---|---|
| 1 | Opportunity Memory + backfill | ★★★★★ | M | L | Turns 247 dead outcomes into queryable knowledge immediately; foundation for all else |
| 2 | Failure Memory | ★★★★★ | S | L | Highest learning value per Rule 5; small build on top of #1 |
| 3 | Source Memory (extend trust) | ★★★★ | S | L | Reuses trust engine; directly improves source weighting |
| 4 | Reality Verification Layer | ★★★★ | M | M | Composes existing trust+research; gates recommendations |
| 5 | Entity Memory | ★★★ | M | M | Compounds slowly; valuable long-term |
| 6 | Knowledge Graph | ★★★ | M | M | Enables relationship discovery; SQL-backed keeps risk low |
| 7 | Market Memory | ★★ | M | L | Nice now-vs-history; depends on Gold maturity |
| 8 | Self-Audit Reports | ★★★ | S | L | Rule 10; cheap, high transparency value |

**REJECTED (fail Rule 1 or Rule 12 — cannot validate):**
- ❌ A separate vector/graph database service — no measurable decision improvement over SQL+NetworkX; pure infra cost.
- ❌ New neural "knowledge model" — violates "no new models"; unfalsifiable.
- ❌ Auto-generated entity relationships from LLM free-association — cannot be verified against outcomes.
- ❌ Real-time "intelligence dashboard" beyond one panel — theater; doesn't change a decision.

---

## 12. DEPENDENCY GRAPH (Rule 11.7)

```
0018 migration
   └─> MemoryStore (store.py)
         ├─> OpportunityMemory ──(backfill)──> signal_outcomes [existing]
         │       └─> FailureMemory
         │       └─> KnowledgeGraph (edges)
         ├─> SourceMemory ──reads──> trust_scores [existing]
         ├─> EntityMemory ──seeds──> compliance/geo [existing]
         └─> MarketMemory ──reads──> data lake Gold [existing]
RealityVerifier ──composes──> TrustStore + research_engine + KnowledgeGraph
ClaimEmitter[existing] ──hook──> OpportunityMemory.record
Settler[existing] ──hook──> OpportunityMemory.settle + FailureMemory.classify
Scheduler[existing] ──job──> report.weekly + SourceMemory.refresh
```
No cycles. Every arrow into `[existing]` is read-or-append-only.

---

## 13. SUCCESS METRICS (Deliverable 9 — Rule 12 validation)

Each is measurable against settled outcomes:
1. **Opportunity recall:** `aegis memory patterns` returns ≥ N recurring opportunity patterns with realized/failed counts. (Validates Rule 2.)
2. **Failure reuse:** ≥ 80% of settled-incorrect outcomes get a non-null `failure_category`. (Rule 5.)
3. **Source discrimination:** source trust scores have variance > 0 and rank-correlate with their settled-correct rate (Spearman > 0). (Rule 4.)
4. **Reality gate calibration:** opportunities passing the Reality gate (high reality_score) have a **higher realized rate** than those that don't — measured out-of-sample. (Rule 8 + Rule 12.) This is the headline metric: *the gate must actually predict success.*
5. **Compounding:** week-over-week, the Reality gate's separation (Δ realized-rate between pass/fail) is non-decreasing as more outcomes accrue.
6. **No regression:** prediction-path latency unchanged (hooks are async fire-and-forget); coverage ≥ 78%; 0 ruff.

If metric #4 cannot be shown out-of-sample, the consume stages (Stage 3–4) are **not shipped** — capture stays as observation only. (Rule 12.)

---

## 14. POST-IMPLEMENTATION AUDIT PLAN (Deliverable 10)

- **Falsifiability audit:** confirm every memory row traces to a settled outcome; no invented knowledge. Script: `aegis memory audit`.
- **Reality-gate backtest:** k-fold OOS on accumulated opportunities — does high reality_score predict realization? (mirrors the Phase B/C calibration audit methodology, reuse `trust.calibration`).
- **Knowledge growth curve:** plot opportunities/entities/edges over time — must be monotincreasing and verified.
- **Self-audit dogfood:** the weekly report (Rule 10) is itself the audit artifact; review 4 weeks of reports for drift.
- **CONFIDENCE_AUDIT-style writeup:** `KNOWLEDGE_AUDIT.md` tracing each of the 8 goals to a measured result.

---

## DECISION REQUESTED

This blueprint reuses the Phase A/B/C substrate (`signal_outcomes`, `trust/`, `ClaimEmitter`, settler, research engine), adds **one additive migration** and **one new package**, and is staged so capture (observation) ships before any decision is altered. No new models, domains, or infra services.

**Proposed first implementation slice (if approved): Stage 1 + ROI ranks #1–#2** — Opportunity Memory + Failure Memory + backfill of the 247 existing settled outcomes, behind `AEGIS_MEMORY_ENABLED`, with falsifiability tests. This delivers immediate queryable knowledge with the lowest risk.

Awaiting approval before writing any implementation code.
