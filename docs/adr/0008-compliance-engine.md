# ADR-0008: Regulatory & Compliance Engine (Phase 8)

## Status
Accepted — 2026-06-02

## Context

Phase 6 (Capital Execution Engine) can autonomously execute cross-market arbitrage
opportunities discovered by Phase 7 (Geospatial Intelligence).  Without a
pre-execution compliance gate, the system could:

- Execute a trade on a counterfeit Louis Vuitton handbag:
  Phase 6 profit = $500 · Legal penalty = $10 000–$200 000 + criminal charges.
- Ship goods **from** an OFAC-sanctioned country (Iran, North Korea):
  Penalty = up to $1 million per violation under 31 CFR 501.
- List a product with false FDA-approval claims:
  FTC/FDA enforcement action, civil penalty up to $50 000/day.
- Sell a data-collecting IoT device into the EU without GDPR compliance:
  GDPR fine up to 4% of global annual turnover.
- Execute against a FATF black-listed trade route:
  AML risk, potential bank account suspension.

The expected value of **one** compliance failure exceeds the expected profit of
**thousands** of legitimate trades.

## Decision

### 1. Pre-execution compliance gate

`gate_execution_plan()` in `src/aegis/execute/compliance_gate.py` is called by
Phase 6's `ExecutionEngine` before any capital is committed.  A BLOCK or ESCALATE
result prevents execution.

### 2. Composite risk matrix (7 dimensions)

| Dimension   | Weight | Primary Source                          |
|-------------|--------|-----------------------------------------|
| Trademark   | 22 %   | Local brand DB + EUIPO TMview API (EU)  |
| FDA         | 20 %   | OpenFDA enforcement API (free, no key)  |
| Counterfeit | 15 %   | Text heuristics + optional CLIP model   |
| Patent      | 13 %   | USPTO PatentsView API (free, no key)    |
| Privacy     | 10 %   | GDPR/DPDP/DSA/GPSR rule engine          |
| FTC         | 10 %   | Rule engine (30+ compiled regex)         |
| AML         | 10 %   | OFAC country list + FATF grey-list      |

Overall risk = Σ weight × dimension_score.

### 3. Decision thresholds

| Score range  | Decision  | Action                              |
|--------------|-----------|-------------------------------------|
| > 0.70       | BLOCK     | Reject execution; log to audit trail |
| 0.50 – 0.70  | ESCALATE  | Hold; require human review           |
| ≤ 0.50       | PROCEED   | Auto-execute (if P0/P1 priority)     |

Hard overrides (always BLOCK regardless of composite score):
- Any OFAC-sanctioned country in the trade route.
- FDA banned keyword match (banned substance / counterfeit drug).
- Explicit "replica" / "knock-off" keyword in product text.

### 4. Real data sources (all free)

- **USPTO PatentsView** — `https://api.patentsview.org/patents/query`
  POST JSON query, no API key required.  45 req/min unauthenticated.

- **EUIPO TMview** — `https://www.tmdn.org/tmview/api/trademark/search`
  GET REST endpoint for EU trademark data.  No key required.

- **FDA OpenFDA** — `https://api.fda.gov/{drug,food,device}/enforcement.json`
  Free JSON API.  1 000 req/day without key; 120 000/day with `AEGIS_COMPLY_FDA_API_KEY`.

- **OFAC Country List** — Static lookup from `constants.py` (refreshed quarterly).
  Trade.gov Consolidated Screening List API for entity-level checks (optional;
  requires free registration key `AEGIS_COMPLY_TRADE_GOV_API_KEY`).

- **FATF Grey/Black List** — Static lookup from `constants.py` (refreshed each FATF
  Plenary meeting, ~quarterly).

- **Local brand trademark DB** — 40+ registered luxury/tech marks in `constants.py`
  with fuzzy matching (difflib SequenceMatcher; no external call, <1 ms).

### 5. Caching strategy

- All API results cached in-process with configurable TTL (default 24 h for IPR/FDA,
  6 h for composite assessments).
- Same `(sku, title_normalized, origin, destination)` key returns cached result
  instantly — avoids repeated API calls for the same SKU across multiple execution
  attempts.

### 6. Counterfeit detection layers

1. Exact luxury brand name substring match in product title (local, <1 ms).
2. Fuzzy brand match (Levenshtein-style via `difflib.SequenceMatcher`, ratio ≥ 0.78).
3. Price anomaly: price < 15% of category median USD from Phase 7 price data.
4. Replica/knock-off keyword regex (`replica`, `aaa grade`, `super copy`, etc.).
5. CLIP image similarity (optional; `AEGIS_COMPLY_CLIP_ENABLED=true`; requires
   `openai-clip` + `torch`; graceful degradation when absent).

### 7. Phase 12 audit integration

Every assessment is asynchronously written to Phase 12's `AuditLogger` (HMAC-signed,
append-only, MinIO WORM archival) when Phase 12 is installed.  This provides a
tamper-evident regulatory paper trail.  Phase 8 degrades gracefully when Phase 12
is absent (`try/except ImportError`).

### 8. Privacy regulations covered

- **GDPR** (EU + EEA + UK + CH) — applies when destination is in GDPR zone.
- **India DPDP 2023** — applies when origin or destination is India.
- **EU DSA** (Digital Services Act) — applies to digital/online products.
- **EU GPSR** (General Product Safety Regulation 2023/988) — physical goods in EU.
- **CCPA** (California) — data-collecting products to US.
- **COPPA** — children's products (title/description keyword detection).

## Trade-offs

**Against this approach:**
- API call latency: ~5–15 s per assessment (mostly network; mitigated by caching).
- False positives: legitimate products with brand names (e.g., "Nike-style colours")
  may be escalated.  Human review resolves escalations.
- Static OFAC/FATF lists require quarterly refreshes (not real-time).

**For this approach:**
- Zero-capital-risk guarantee on executed trades from sanctioned routes.
- 0% FDA/FTC enforcement exposure on auto-executed P0/P1 plans.
- All external APIs are free-tier; no vendor lock-in.
- Graceful degradation on every external dependency (network errors → partial risk
  score, never crash).
- Immutable audit trail satisfies AML regulatory record-keeping requirements.

## Consequences

- `ExecutionEngine` in Phase 6 must call `gate_execution_plan()` before committing
  capital.  Advisory mode (default) still runs the gate; only BLOCK prevents execution.
- `AEGIS_COMPLY_BLOCK_THRESHOLD` and `AEGIS_COMPLY_ESCALATE_THRESHOLD` are tunable
  without code changes.
- Phase 9 (if added) could extend this with real-time USPTO trademark webhook
  notifications and a live OFAC SDN XML feed poll.
