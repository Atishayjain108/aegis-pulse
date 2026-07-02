# AEGIS Mentor — Blueprint

**Goal:** Turn AEGIS from a *signal engine* into a *counsel engine* — a system that models the
person asking, then advises, teaches, and guides them from zero to a running business using the
intelligence AEGIS already collects.

**Keystone principle:** The Mentor *understands intent* and *owns the user relationship*. The
existing intelligence (geo arbitrage, deep numbers, compliance, capital, memory) become **tools the
Mentor calls on the user's behalf**, not separate products. Deeper-numbers and Go-To-Market layers
are added later as Mentor capabilities.

New package: `src/aegis/mentor/` (regular subpackage of the `aegis` namespace — same pattern as
`aegis.geo`, `aegis.compliance`, `aegis.memory`). NOT a workspace member, NOT a `__path__` extension.

---

## Who it serves — ANYONE, in any sector

The Mentor is **not** built for a single persona. It serves whoever shows up:

| Example user | Sector/field | What "good counsel" means for them |
|---|---|---|
| 19yo, ₹0, never leaves home | undecided → online business | scaffolded: pick a field, start from zero, learn basics |
| Local kirana / shop owner | retail / FMCG | local demand depth, supplier arbitrage, what to stock |
| Freelance designer | services / creator | productize a skill, pricing, client acquisition |
| Funded D2C founder | e-commerce / D2C | sharp analysis only: margin, competitors, saturation |
| Student exploring a niche | any (research mode) | teach the landscape, no hand-holding decisions |

**The one thing AEGIS must learn from every user is their SECTOR/FIELD and INTENT** — what domain
they're in or chasing, and why. Everything else (capital, skill, channel, experience) tunes *how*
it helps; the sector decides *what domain intelligence* it pulls. A `SectorRouter` maps the field
to the right tools, knowledge, and benchmarks. The system is sector-agnostic by construction — no
hard-coded "this is a dropshipping tool."

## The core ambition: AEGIS as the company's operating system (A→Z)

The goal is **indispensability through capability**. AEGIS should do every function of running a
business so well that a person — solo founder or a whole company — relies on it end to end:

**Market research → market exploration → market knowledge → strategy → operations → supplier
sourcing & verification → customer & lead generation → acquisition → retention → finance/PnL.**

The conversational **Mentor is the brain/interface**; behind it sits a fleet of specialized
**operator agents**, each owning one company function and each backed by AEGIS's real intelligence
(geo arbitrage, compliance, capital, memory, trust). The user talks to one system; AEGIS runs the
whole company underneath.

### What "strongest system in the world" requires (design constraints, not slogans)

Dependence is *earned*, not forced. A person hands their company to AEGIS only when it is:

- **Adaptive to the user & sector** — the one thing it must learn is the person's
  **sector/field + intent**; then it tunes depth to their level (`autonomy_preference`:
  do-it-for-me / co-pilot / brief-me). A novice gets it run *for* them; an operator gets a
  co-pilot. Same engine, different altitude — so it fits everyone, in any field.
- **Trustworthy / grounded** — it can run your company only if you trust it, and trust comes from
  AEGIS showing *why* (which numbers, which sources, what would change the call) and being right
  repeatedly. This is the **trust engine that earns dependence** — reuse the existing
  trust/calibration + reality-verifier discipline.
- **Dynamic & situational** — re-reasons over the *current* state and what changed; when the market
  moves, capital changes, or an op saturates, AEGIS adjusts the whole operating plan and says why.
- **Autonomous where trusted** — beyond advising, AEGIS *acts*: runs the research, ranks suppliers,
  drafts the outreach, watches the market continuously (MarketSentinel), and only surfaces what
  needs a human decision. Capital-moving actions stay gated; everything else it can own.

Every design decision is judged by: *does this make AEGIS the most capable, trusted, end-to-end
operator of a business — for anyone, in any sector?*

## Depth-first knowledge strategy (specialize one sector at a time)

Trying to be deeply expert in *every* field simultaneously produces generic, shallow, fragile
output. So AEGIS has **two tiers of knowledge**:

- **Broad baseline (everything)** — a wide, shallow map of every sector so AEGIS is never blind and
  can always route, frame, and give a competent first answer in any field. Backed by
  `aegis.memory` + LLM general knowledge + the `SectorRouter`.
- **Deep specialization (one sector at a time)** — for the *active* sector, AEGIS has a rich
  `SectorPack`: real benchmarks, margin structures, supplier landscape, regulations, customer
  behavior, channel playbooks, failure patterns, and ground-reality knowledge. This is where AEGIS
  is genuinely *expert*, not generic.

A `SectorPack` is a pluggable bundle (data + tuned operator behavior + knowledge). New sectors are
added one at a time, each mastered before the next — sequential mastery, not parallel sprawl. This
also protects stability: a half-built sector can't degrade a finished one.

**First deep sector (v1): D2C / e-commerce in India** — it's where AEGIS already has the most real
machinery (swarm marketplace adapters, geo arbitrage, Phase 6 fulfillment, supplier clients), so we
reach genuine depth fastest. Region default: **India / INR**.

## The conviction engine (persuade with practicality + evidence)

AEGIS must be able to **convince even a skeptical or negative person** — not by hype, but by making
the practical, evidence-backed case so clearly that doubt has nowhere to stand:

- **Evidence-first persuasion** — every claim carries the number, the source, and the live proof
  (real margins, real demand slope, real supplier quotes). Reuse grounded-or-silent.
- **Objection modeling** — AEGIS anticipates the skeptic's objections ("too saturated", "no
  capital", "won't sell") and answers each with concrete evidence + a practical path, rather than
  ignoring them.
- **Practical, do-able next step** — conviction lands when the person sees a *small, concrete,
  affordable* first action that obviously works, not a grand abstract plan.
- **Honest, calibrated confidence** — a skeptic trusts a system that admits the risks and shows it
  has priced them in. Overclaiming destroys persuasion; calibrated honesty builds it.

This lives as a cross-cutting `Persuader` used by the MentorAgent and `CustomerOp` (for customer
acquisition / lead conversion), and is itself bound by the grounded-or-silent rule.

---

## Architecture — how the Mentor sits on top of what exists

```
            ┌─────────────────────────── MENTOR ───────────────────────────┐
 user text  │  IntentParser → UserProfile     (who is this, what SECTOR,    │
 ──────────▶│       sector/field, intent,      what can they do, what       │
            │        capital, skills, risk,    stops them, how much          │
            │        channel, autonomy_pref)   autonomy they want)          │
            │            │                                                  │
            │            ▼                                                  │
            │  SectorRouter → domain tools/knowledge/benchmarks for field   │
            │            │                                                  │
            │            ▼                                                  │
            │  OpportunityMatcher  ──── calls ───▶ aegis.geo (arbitrage)    │
            │   (filter+rank ops vs profile:        aegis.memory (opp recall│
            │    capital fit, channel fit,          + failure memory)       │
            │    skill fit, saturation)             aegis.compliance/capital│
            │            │                          (advisory gates)        │
            │            ▼                                                  │
            │  MentorAgent = ORCHESTRATOR (LLM, grounded)                   │
            │   routes the user's need to the operator fleet, then          │
            │   synthesizes grounded counsel + actions:                     │
            │                                                               │
            │   ┌── OPERATOR FLEET (A→Z company functions) ──────────────┐  │
            │   │ ResearchOp     market research / sizing / trends        │  │
            │   │ ExploreOp      discover new markets & niches (Sentinel) │  │
            │   │ KnowledgeOp    domain knowledge + ground reality        │  │
            │   │ StrategyOp     business model, positioning, plan        │  │
            │   │ SupplierOp     find + verify reliable suppliers         │  │
            │   │ CustomerOp     lead gen + acquisition + retention       │  │
            │   │ OpsOp          pricing, fulfillment, settlement (Ph6)   │  │
            │   │ FinanceOp      PnL, capital, unit economics             │  │
            │   └─────────────────────────────────────────────────────────┘  │
            │      each backed by real intelligence: geo, compliance,       │
            │      capital, memory, trust, llm, scrape/swarm/sentinel       │
            └───────────────────────────────────────────────────────────────┘
                     │                         │
                 aegis mentor CLI        /api/mentor/* + dashboard "Mentor" page
```

**Heuristic-first doctrine (same as the rest of AEGIS):** every Mentor output must be produced
deterministically from the profile + opportunity numbers even with zero LLM keys. The LLM only
enriches the *prose*; it can never invent an opportunity or flip a recommendation. This keeps the
zero-API-key test path green and keeps counsel grounded in real numbers.

---

## Data model

New migration `00XX_mentor.sql` (RLS, `app.current_tenant`):

- **`user_profiles`** — `profile_id`, `tenant_id`, `raw_description`, **`sector`** (free-text +
  normalized tag, e.g. "d2c_apparel", "saas", "local_retail", "creator_services", "undecided"),
  **`intent`** (income/learning/scale/research/validate), **`autonomy_preference`**
  (guide_me/coach_me/answer_me), `capital_usd`, `risk_tolerance` (low/med/high), `channels`
  (online_only/local/both), `skills jsonb`, `constraints jsonb` (e.g. "never_leaves_home",
  "no_team"), `time_per_week_hrs`, `experience_level` (none/some/experienced), `region`,
  `created_at`, `updated_at`. One person → one evolving profile.
- **`mentor_sessions`** — `session_id`, `profile_id`, `query`, `matched_opportunity_ids jsonb`,
  `counsel jsonb` (the structured advice), `created_at`. Append-only history of advice given.
- **`knowledge_gaps`** — `gap_id`, `profile_id`, `topic`, `detected_from` (session/query),
  `lesson_delivered bool`, `created_at`. Drives the teaching loop.

Pydantic v2 frozen schemas in `aegis/mentor/schemas.py`: `UserProfile`, `Opportunity` (thin
adapter over geo/memory results), `MatchResult`, `Counsel`, `Lesson`, `KnowledgeGap`.

---

## Phased plan (each phase ships green tests + 0 ruff before the next)

### Phase M1 — Intent, Sector & Profile (the keystone of the keystone)
- `schemas.py` — `UserProfile` (incl. `sector`, `intent`, `autonomy_preference`) + `Counsel` etc.
- `config.py` — `MentorSettings` (env prefix `AEGIS_MENTOR_`).
- `intent.py` — `IntentParser.parse(text) -> UserProfile`. LLM-backed extraction via
  `aegis.llm` gateway with **deterministic regex/keyword fallback** (capital amounts, "never
  leave home" → online_only + constraint, "don't know anything" → experience=none +
  autonomy=guide_me, sector keywords → sector tag). **Asks a clarifying question when sector or
  intent is missing** rather than guessing — sector is the one field it must not fabricate.
- `sector.py` — `SectorRouter` + `SectorPack` protocol: normalize free-text field → sector tag →
  the pluggable pack (benchmarks, knowledge, tuned operator behavior). Ships the broad baseline +
  the first deep pack `packs/d2c_india.py` (real margins, marketplaces, supplier landscape, GST/
  regs). Extensible registry; new packs added one at a time.
- `profiles.py` — `ProfileStore` (asyncpg, upsert/fetch, RLS). Profiles persist + evolve.
  Region defaults to India / INR.
- Migration `00XX_mentor.sql`.
- Tests: parse several different personas (the 19yo, a shop owner, a funded founder) → assert
  distinct sectors, autonomy levels, and that a missing-sector input triggers a clarifying ask.
  Mock LLM + fallback path.

Each operator agent shares one contract: `Operator.run(profile, request, context) -> OperatorResult`
(grounded findings + actions + reasoning trace). The MentorAgent orchestrates them. Build the brain
first, then add operators one function at a time — each immediately usable through the Mentor.

### Phase M2 — Orchestrator + Research/Explore/Knowledge ops
- `operators/base.py` — `Operator` protocol + `OperatorResult` schema (findings, actions, sources,
  reasoning, confidence).
- `mentor_agent.py` — `MentorAgent` orchestrator: route request → operators → synthesize grounded
  counsel. Altitude adapts to `autonomy_preference`. Deterministic skeleton; LLM enriches prose.
- `operators/research.py` — `ResearchOp`: market sizing, trends, demand depth via existing
  scrape/swarm + `aegis.geo` + signals DB.
- `operators/explore.py` — `ExploreOp`: discover new markets/niches (wraps MarketSentinel).
- `operators/knowledge.py` — `KnowledgeOp` + `KnowledgeRetriever`: domain + ground-reality facts
  from `aegis.memory`; grounded, not generic LLM filler.
- Tests: research/explore/knowledge each return grounded results with sources; orchestrator
  synthesizes; same request → different altitude for do-it-for-me vs brief-me; LLM-off path works.

### Phase M3 — SupplierOp (find + verify reliable suppliers)
- `operators/supplier.py` — `SupplierOp`: source candidate suppliers (Printful/CJ/IndiaMart +
  scrape), **verify reliability** (reuse Phase D supplier intel + execution-intel verification),
  rank by trust/price/lead-time. Surfaces *verified* suppliers, flags unverified.
- Tests: returns ranked verified suppliers; never marks an unverified supplier as reliable.

### Phase M4 — CustomerOp + Persuader (lead gen + acquisition + conviction)
- `operators/customer.py` — `CustomerOp`: competitive analysis, target-persona, lead generation
  (grounded, real sources), ₹0-budget acquisition channels, outreach drafts, retention angles.
- `persuasion.py` — `Persuader`: evidence-first case builder + objection modeling (anticipate
  "too saturated / no capital / won't sell" → answer each with real numbers + a do-able step).
  Cross-cutting; used by MentorAgent and CustomerOp. Bound by grounded-or-silent.
- Honest labeling: demand/lead signals marked as proxies vs verified (reuse trust discipline).
- Tests: produces persona + channels + sample leads + drafts; proxy signals labeled, not faked;
  Persuader answers each modeled objection with a real number, never hype; refuses to persuade
  when evidence is absent (silent rather than fabricated).

### Phase M5 — Ops + Finance ops
- `operators/ops.py` — `OpsOp`: pricing, fulfillment, settlement via Phase 6 capital engine
  (advisory mode — never moves capital without the gate).
- `operators/finance.py` — `FinanceOp`: unit economics, PnL, capital fit, runway.
- Tests: end-to-end advisory plan; capital-moving actions stay gated.

### Phase M6 — Surfaces + autonomy + (optional) teaching
- `cli.py` — `aegis mentor` (`profile`, `ask`, `run`, `plan`, `learn`).
- `api.py` — `/mentor/*` FastAPI router (profile, ask, operators, plan).
- Dashboard "Mentor / Command" page: free-text → profile → live operator results → grounded plan.
- `curriculum.py` (optional teaching mode for `do-it-for-me`+novice): `GapDetector` +
  `LessonBuilder` micro-lessons; `knowledge_gaps` persistence.
- Continuous-autonomy hook: MentorAgent can run operators on a schedule (Sentinel-style) and push
  what needs a human decision.

---

## Non-negotiables (so this stays AEGIS, not a chatbot)
1. **Grounded or silent** — every claim traces to a real number or a stored fact; no invented data.
   Reuse the trust/calibration + reality-verifier discipline already in the codebase.
2. **Heuristic-first** — full counsel with zero API keys; LLM only enriches prose.
3. **Depth-first, broad baseline** — competent in any field (broad baseline), genuinely expert in
   the active sector (deep `SectorPack`). Sectors added one at a time; a half-built sector must not
   degrade a finished one. The `SectorRouter` is extensible and never hard-codes a single domain.
4. **Persuasion is grounded** — the conviction engine convinces with evidence + practicality +
   objection-handling, never hype; bound by grounded-or-silent.
5. **Indispensable through trust** — output altitude follows `autonomy_preference` (do-it-for-me /
   co-pilot / brief-me); every recommendation shows its reasoning + sources, because people only
   hand a system their whole company when it visibly earns the trust.
6. **Dynamic & honest** — re-reasons on current state; states confidence; says what it doesn't know
   and when to verify — false confidence is what breaks dependence.
7. **Acts, but capital stays gated** — operators run research/sourcing/outreach autonomously;
   capital-moving actions go through the existing capital/compliance gates.
8. **RLS everywhere** — profiles are personal data; `app.current_tenant` on every query.
9. **Tests + 0 ruff per phase**, coverage floor held.
