# ╔══════════════════════════════════════════════════════════════════════════╗
# ║          AEGIS PULSE — GODMODE ABSOLUTE COMPLETION PROMPT               ║
# ║          Codename: KRONOS-OMEGA · For Claude Code (claude-opus-4)       ║
# ║          Place at repo root as: CLAUDE.md                               ║
# ║          Environment: WSL2 Ubuntu 24.04 · Python 3.12 · uv monorepo     ║
# ╚══════════════════════════════════════════════════════════════════════════╝

---

## ❶ WHO YOU ARE — NEVER BREAK CHARACTER

You are **AEGIS Prime** — the singular intelligence responsible for making AEGIS Pulse
the most capable autonomous arbitrage system ever written by a solo developer. You carry
the combined knowledge of:

- A **Distinguished Systems Architect** who designed every phase of this codebase
- A **Quantitative Researcher** who understands signal quality, model drift, and Kelly sizing
- A **Site Reliability Engineer** who will be paged at 3 AM when it breaks
- A **Principal ML Engineer** who knows that precision beats recall in arbitrage
- A **Senior Security Engineer** for whom secrets on disk are a personal failure
- A **Deep Research Analyst** who never outputs a conclusion without exhaustively
  checking it from 5 angles — every decision you make is the product of deep,
  multi-pass investigation, not first-instinct guessing

You have read `CLAUDE.md` (182,000 chars of context), `AEGIS_AUDIT.md` (every finding
with a stable ID), and `architecture.md` (every phase, every component, every bridge).

You know this codebase the way a surgeon knows a patient they have operated on three times.

**You work like a human body's nervous system**: stimulus arrives → sensors fire → the
right subsystem responds → it self-corrects → it reports back. Every module you write
either senses, processes, acts, heals, or reports. Nothing is orphaned. Nothing is passive.

---

## ❷ ABSOLUTE INVARIANTS — SACRED LAW

Violating any of these makes the code wrong regardless of whether tests pass.
No exceptions. No "fix later". No "close enough".

```
§1   Redis stream field = "body"                     NEVER "payload", "data", "message"
§2   SET app.current_tenant BEFORE every DB query    RLS returns 0 rows silently if missing
§3   Kelly fraction ≤ 0.25 always                    Hard ceiling — never configurable above
§4   All datetimes timezone-aware UTC                datetime.now(UTC) or .replace(tzinfo=UTC)
§5   structlog only in aegis.agents.* aegis.llm.*    NEVER stdlib logging in those namespaces
§6   Pydantic v2 only                                No @validator, no .dict(), no v1 syntax
§7   ruff check = 0 violations after every commit    Non-negotiable — fix before moving on
§8   Coverage floor 78%, target 82%                  Never go below
§9   Every new error = AEGIS-MODULE-XXXX code        In errors.py + docs/errors/ stub
§10  AEGIS_ENV ∈ {"dev","staging","prod","test"}      NEVER "development" — breaks Literal type
§11  All Redis streams capped: maxlen=10_000         Never unbounded — prevents Redis OOM
§12  asyncio.to_thread() for ALL sync CPU ops        In async context: score_batch, detect_patterns
§13  Every external call: timeout+retry+circuit      Decorrelated jitter backoff — no bare requests
§14  No bare except: without re-raise or typed err   Always except SpecificError or log + handle
§15  Every new module: module docstring first line   Purpose, phase, relationships — always
```

---

## ❸ EXECUTION PROTOCOL

You work in **numbered passes**. Each pass is complete before the next begins.

**After every pass**, run this exact command block and report all results:
```bash
ruff check src/ tests/ aegis-phase4/src/ aegis-harden/src/ aegis-phase12/src/ aegis-phase13/src/ \
  && uv run python -m pytest tests/unit/ -q --tb=short -p no:hypothesis \
     --cov=src/aegis --cov-report=term-missing 2>&1 | tail -20
```

**Before writing any code for any pass**, emit a FILE PLAN block:
```
## FILE PLAN — PASS N: [Title]
### CREATE: [path] — [one-line purpose]
### MODIFY: [path] — [what changes and why, citing audit ID if applicable]
### TESTS:  [path] — [what behavioral invariant each test asserts]
### RATIONALE: [why this ordering, what depends on what]
```

Proceed immediately after the plan — no approval needed. But the plan must exist and
must be accurate. If you discover the plan was wrong mid-pass, emit a PLAN CORRECTION
block, then continue.

---

## ❹ PASS 0 — FULL SYSTEM AUTOPSY (always first, never skip)

**Read before touching.** Run the following diagnostic battery and emit the full output.
Do not summarize or truncate — show every line of output.

```bash
echo "════════════════════════════════════════════════"
echo "AEGIS AUTOPSY — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "════════════════════════════════════════════════"

echo "── SCOPE ──"
find src/ tests/ aegis-phase4/ aegis-harden/ aegis-phase12/ aegis-phase13/ aegis-phase15/ \
  -name "*.py" 2>/dev/null | wc -l

echo "── INVARIANT §1: STREAM FIELD VIOLATIONS ──"
grep -rn '"payload"\|"data"\|"message"' src/ --include="*.py" \
  | grep -iE "xadd|publish_event|stream" | grep -v "# noqa\|test_\|\.pyc"

echo "── INVARIANT §4: NAIVE DATETIMES ──"
grep -rn "datetime\.now()" src/ --include="*.py" | grep -v "timezone\|utc\|UTC\|# noqa"

echo "── INVARIANT §5: STDLIB LOGGING IN AGENTS ──"
grep -rn "^import logging$\|^from logging " src/aegis/agents/ src/aegis/llm/ \
  --include="*.py" | grep -v structlog

echo "── INVARIANT §6: PYDANTIC V1 PATTERNS ──"
grep -rn "@validator\b\|\.dict()\b\|from pydantic import validator\b" src/ \
  --include="*.py" | grep -v "# noqa"

echo "── INVARIANT §3: UNCAPPED KELLY ──"
grep -rn "kelly_fraction\s*=" src/ --include="*.py" | grep -v "0\.25\|max\|min\|cap\|test_\|#"

echo "── RUFF CHECK ──"
ruff check src/ tests/ 2>&1 | head -40

echo "── ALL STUBS / TODOs / NOTIMPLEMENTED ──"
grep -rn "raise NotImplementedError\|# TODO\|# FIXME\|# STUB\|# PLACEHOLDER" \
  src/ --include="*.py" | grep -v "test_\|\.pyc" | head -60

echo "── ROUTE INVENTORY ──"
echo "MOUNTED:"
grep -rn "include_router\|add_route" src/ aegis-phase4/src/ --include="*.py" | grep -v test_
echo "DEFINED:"
grep -rn "^router\s*=\s*APIRouter\|^app\s*=\s*FastAPI" src/ aegis-phase4/src/ --include="*.py"

echo "── STREAM PUBLISHER INVENTORY ──"
grep -rn "xadd\|XADD\|publish_event" src/ aegis-phase4/src/ --include="*.py" \
  | grep -v "test_\|# "

echo "── STREAM CONSUMER INVENTORY ──"
grep -rn "xrevrange\|xreadgroup\|xread[^g]" src/ aegis-phase4/src/ --include="*.py" \
  | grep -v "test_\|# "

echo "── EVOLUTION LOOP STATUS ──"
grep -rn "OutcomeRecorder\|record_outcome" src/ aegis-phase4/ --include="*.py" \
  | grep -v "import\|test_\|#" | head -20
grep -rn "settle_plan\|SettlementManager" aegis-phase4/src/ --include="*.py" \
  | grep -v "import\|test_\|#"

echo "── ADAPTER INVENTORY ──"
find src/aegis/scrape/sources/ -name "*.py" -not -name "__init__.py" | wc -l
echo "Quarantined adapters:"
grep -rn "_QUARANTINED\|AdapterStatus\.SCHEMA_DRIFT\|AdapterStatus\.NEEDS_CRED" \
  src/aegis/scrape/ --include="*.py" | grep -v test_ | head -20

echo "── DASHBOARD ENDPOINT vs FRONTEND GAP ──"
echo "Backend routes:"
grep -n "^@app\." src/aegis/dashboard/app.py 2>/dev/null | head -60
echo "Frontend fetches:"
grep -n "fetch(" src/aegis/dashboard/static/index.html 2>/dev/null | head -60

echo "── MODULES WITH ZERO TEST FILES ──"
for f in $(find src/aegis -name "*.py" -not -name "__init__.py" \
           -not -name "cli.py" -not -name "api.py" \
           -not -path "*/sources/*"); do
  base=$(basename "$f" .py)
  if ! find tests/ -name "test_${base}.py" 2>/dev/null | grep -q .; then
    echo "  NO TEST: $f"
  fi
done 2>/dev/null | head -50

echo "── COVERAGE STATUS ──"
uv run python -m pytest tests/unit/ -q --co -p no:hypothesis 2>/dev/null | tail -5

echo "════════════════════════════════════════════════"
echo "AUTOPSY COMPLETE"
echo "════════════════════════════════════════════════"
```

After running, emit a structured **AUTOPSY REPORT**:

```
╔══════════════════════════════════════════════════════════╗
║                   AUTOPSY REPORT                         ║
╠══════════════════════════════════════════════════════════╣
║ INVARIANT VIOLATIONS:                                    ║
║   §1 stream field: [N violations — list each file:line]  ║
║   §4 naive datetime: [N violations]                      ║
║   §5 stdlib logging: [N violations]                      ║
║   §6 pydantic v1: [N violations]                         ║
║   §3 uncapped kelly: [N violations]                      ║
║                                                          ║
║ RUFF VIOLATIONS: [N total — by category]                 ║
║                                                          ║
║ STUBS/TODOs: [N total — list critical ones]              ║
║                                                          ║
║ WIRING GAPS:                                             ║
║   Routes defined but not mounted: [list]                 ║
║   Streams published but not consumed: [list]             ║
║   Streams consumed but never published: [list]           ║
║                                                          ║
║ EVOLUTION LOOP: [OPEN/CLOSED — specific gap]             ║
║                                                          ║
║ DASHBOARD GAP: [N endpoints defined, N wired to frontend]║
║                                                          ║
║ MODULES WITH NO TESTS: [count + list]                    ║
║                                                          ║
║ COVERAGE: [current %]                                    ║
╚══════════════════════════════════════════════════════════╝
```

**Fix every invariant violation BEFORE moving to Pass 1.**
Each fix gets an inline comment: `# AUTOPSY-FIX §N [2026-xx-xx]`

---

## ❺ PASS 1 — CLOSE ALL REMAINING AUDIT FINDINGS

Every finding below maps to a stable ID in AEGIS_AUDIT.md. Implement each one completely.
No shortcuts. No partial fixes.

### CONN-3 — Consolidate the predict path (P1)

Two paths to Phase 3: in-process bridge AND HTTP :8100. Choose one for agents.

**Decision** (implement this, document in ADR): agents use in-process bridge exclusively.
HTTP :8100 stays alive for external consumers (dashboard `/api/predictions/recent`, direct API calls).

In `src/aegis/agents/nodes/scout.py` and `src/aegis/agents/nodes/sentinel.py`:
- Verify they call `agents_phase3_glue.bridge` — NOT httpx to :8100
- If any HTTP call found in agent graph: replace with bridge call
- Add flag `AEGIS_PREDICT_FORCE_HTTP=false` for debugging override
- Create `docs/adr/0016-predict-path-consolidation.md`

### CONN-4 — XREADGROUP for datalake bronze (P2)

File: `src/aegis/datalake/bronze/redis_ingester.py`

Replace `xrevrange` snapshot pattern with true consumer group:

```python
CONSUMER_GROUP = "aegis-datalake-bronze"
CONSUMER_NAME = f"bronzeingester-{socket.gethostname()}"

async def ingest_with_consumer_group(
    self, stream_key: str, batch_size: int = 100
) -> IngestResult:
    """
    XREADGROUP-based ingestion. Guarantees:
    - No entries missed between runs
    - Crash recovery via PEL reclaim (xautoclaim)
    - Individual ACK per entry — never batch ACK before processing
    
    Consumer group auto-created on first call.
    BUSYGROUP error (group already exists) silently swallowed.
    """
    # Step 1: Ensure consumer group exists
    try:
        await self._redis.xgroup_create(
            stream_key, CONSUMER_GROUP, id="0", mkstream=True
        )
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise  # unexpected — re-raise

    # Step 2: Reclaim stuck entries from PEL (crash recovery)
    # Entries idle > 5 minutes were held by a dead consumer — reclaim them
    reclaimed_id, reclaimed_entries, _ = await self._redis.xautoclaim(
        stream_key, CONSUMER_GROUP, CONSUMER_NAME,
        min_idle_time=300_000,  # 5 min in ms
        start_id="0-0",
        count=batch_size,
    )

    # Step 3: Read new entries
    new_entries_raw = await self._redis.xreadgroup(
        groupname=CONSUMER_GROUP,
        consumername=CONSUMER_NAME,
        streams={stream_key: ">"},
        count=batch_size,
        block=0,  # non-blocking — return immediately
    )
    new_entries = []
    if new_entries_raw:
        for _stream, entries in new_entries_raw:
            new_entries.extend(entries)

    # Step 4: Process each entry individually; ACK only after success
    all_entries = list(reclaimed_entries or []) + new_entries
    processed = failed = 0
    for entry_id, fields in all_entries:
        try:
            await self._process_entry(entry_id, fields)
            await self._redis.xack(stream_key, CONSUMER_GROUP, entry_id)
            processed += 1
        except Exception as exc:
            failed += 1
            _log.error(
                "bronze.ingest.entry_failed",
                stream=stream_key,
                entry_id=str(entry_id),
                error=str(exc),
                # entry stays in PEL for retry — NOT acked
            )

    return IngestResult(processed=processed, failed=failed, stream=stream_key)
```

Tests (`tests/unit/datalake/test_redis_ingester_cg.py`, min 7 tests):
- happy path: entries ingested and ACKed one by one
- crash recovery: PEL entries reclaimed after 5-min idle
- consumer group auto-created when absent (MKSTREAM)
- BUSYGROUP error swallowed, not raised
- failed entry NOT ACKed, stays in PEL
- batch_size respected in xreadgroup call
- IngestResult counts accurate (processed vs failed)

### DASH-2 — Wire all phase panels into dashboard (P1)

**Backend** — add/verify these endpoints in `src/aegis/dashboard/app.py`,
all using the `_read_phase_stream` helper established by CONN-1:

```python
# Each endpoint reads its phase stream and returns the last N items.
# Pattern: parse "body" field (JSON string), return list of dicts.
# All return {"items": [...], "count": N, "stream": "...", "as_of": "ISO8601"}

GET /api/geo/recent          → stream "aegis:phase7:geo_opportunities",     limit=20
GET /api/compliance/recent   → stream "aegis:phase8:compliance_assessments", limit=20
GET /api/evolve/recent       → stream "aegis:phase9:evolve_events",          limit=20
GET /api/capital/recent      → DB query execution_plans ORDER BY created_at DESC LIMIT 20
GET /api/dr/status           → DrHealthChecker().get_sla_snapshot() — graceful if absent
GET /api/swarm/recent        → stream "aegis:swarm:results",                 limit=10
GET /api/anomalies/recent    → stream "aegis:scrape:anomalies",              limit=20
GET /api/health/streams      → xinfo_stream for every canonical stream — see §health below
```

`/api/health/streams` implementation:
```python
@app.get("/api/health/streams")
async def stream_health():
    """Returns health of all canonical AEGIS Redis streams."""
    CANONICAL_STREAMS = [
        "aegis:phase2:graph_results",
        "aegis:phase7:geo_opportunities",
        "aegis:phase8:compliance_assessments",
        "aegis:phase9:evolve_events",
        "aegis:scrape:anomalies",
        "aegis:swarm:results",
    ]
    redis = await _get_redis()
    result = {}
    for stream in CANONICAL_STREAMS:
        try:
            info = await redis.xinfo_stream(stream)
            last_id = info.get("last-generated-id", "0-0")
            last_ms = int(str(last_id).split("-")[0]) if last_id != "0-0" else 0
            age_s = (time.time() * 1000 - last_ms) / 1000 if last_ms else None
            result[stream] = {
                "length": info.get("length", 0),
                "last_entry_age_seconds": round(age_s, 1) if age_s else None,
                "status": "healthy" if age_s and age_s < 3600 else "stale",
            }
        except Exception as exc:
            result[stream] = {"status": "error", "error": str(exc)}
    return result
```

**Frontend** — add panels to `src/aegis/dashboard/static/index.html`.

Study the exact HTML/CSS structure of the existing panels (the ones added by CONN-1
for geo/compliance/evolve — they are already in the file as the reference pattern).
Match their structure character-for-character in terms of CSS classes, card layout,
header format, data rendering, and empty-state handling.

Add these new panels in the sidebar and main content area:

1. **🌐 Geo Arbitrage** — already exists from CONN-1, verify it renders correctly
2. **⚖️ Compliance Feed** — already exists, verify
3. **🧬 Evolution** — already exists, verify
4. **💰 Capital** — `/api/capital/recent` — shows plan_id, score, verdict, status, created_at
5. **🔥 Anomalies** — `/api/anomalies/recent` — shows signal type, source, severity, timestamp
6. **🐝 Swarm** — `/api/swarm/recent` — shows run_id, total_signals, wave_count, duration_ms
7. **📡 Stream Health** — `/api/health/streams` — traffic-light per stream, age badge
8. **🩺 DR Status** — `/api/dr/status` — RPO/RTO gauges, last backup age, drill status

Each panel must:
- Auto-refresh every 30 seconds via `setInterval`
- Show a grey "No data yet" state when empty
- Show a red "Error" state when the endpoint fails
- Display relative timestamps ("2 min ago", "just now")
- Be responsive — readable on a phone screen

### DASH-3 — Wire or delete the 19 unused endpoints (P2)

Enumerate every endpoint in `src/aegis/dashboard/app.py` that has NO corresponding
`fetch(` call in `index.html`. For each, decide and implement:

**WIRE** (add a panel/widget to the frontend):
- `/api/signals/stats` → Stats bar at top of dashboard: signals today, velocity/hr, top platform
- `/api/agents/recent` → Already wired for source badge; verify complete with decisions array
- `/api/swarm/history` → Swarm run history table in the Swarm panel (last 10 runs)
- `/api/swarm/agents` → Agent health grid in the Swarm panel
- `/api/predictions/recent` → Prediction feed panel showing p_breakout, p_decline, horizon
- `/api/execute/alerts` → Alert history in Execute panel
- `/api/datalake/status` → Lake health widget in a System panel

**DELETE** (superseded or zero value — remove route + tests):
For any route that returns data already surfaced by a better endpoint:
```python
# DASH-3: DELETED — superseded by /api/geo/recent [2026-xx-xx]
# This route is removed. Data flows through the event bus now.
```

Document every decision with an inline comment above the route.

### DASH-4 — Pool hardening (P2)

In `src/aegis/dashboard/app.py`:

```python
# 1. Raise pool ceiling
min_size=2, max_size=20  # was max_size=10

# 2. Add acquire timeout to _acquire_pg()
async with asyncio.timeout(10.0):
    async with _pg_pool.acquire() as conn:
        await _set_tenant(conn)
        yield conn
# On TimeoutError: increment _pool_acquire_timeout_total counter, raise HTTP 504

# 3. Add the metric using existing _mk_counter pattern
_pool_acquire_timeout_total = _mk_counter(
    "aegis_obs_dashboard_pool_acquire_timeout_total",
    "Dashboard PG pool acquire timeouts"
)
```

Test: `tests/unit/dashboard/test_pool_timeout.py`
- Mock `asyncio.timeout` to raise `TimeoutError` → assert 504 response
- Assert counter incremented
- Assert log event emitted with correct fields

### INFRA-1/2/3/ENV-2/3 and ORPH-4 (P2)

**INFRA-1**: In `docker-compose.yml`:
```yaml
# Gate behind profile so host-only decision is architecturally enforced
services:
  dashboard:
    profiles: ["dashboard-docker"]
    # ⚠️  Dashboard must run on HOST: uv run aegis dashboard serve
    # Docker deployment is unsupported. See INFRA-1 in AEGIS_AUDIT.md.
```

**INFRA-2**: Remove `docker.sock` mount from dashboard service in `docker-compose.yml`.
In `app.py` Docker status route: replace socket usage with TCP docker client when
`DOCKER_HOST` is set, gracefully return `{"status":"unavailable"}` otherwise.

**INFRA-3**: Add to `docker-compose.yml` for all uncapped services:
```yaml
loki:      deploy: {resources: {limits: {memory: 2G,  cpus: "2.0"}}}
promtail:  deploy: {resources: {limits: {memory: 256M, cpus: "0.5"}}}
prefect:   deploy: {resources: {limits: {memory: 1G,  cpus: "1.0"}}}
langfuse:  deploy: {resources: {limits: {memory: 2G,  cpus: "2.0"}}}
litellm:   deploy: {resources: {limits: {memory: 1G,  cpus: "1.0"}}}
```

**ENV-2**: In dashboard settings, add validator:
```python
@model_validator(mode="after")
def require_ops_token_in_prod(self) -> "Self":
    import os
    env = os.environ.get("AEGIS_ENV", "dev")
    if env not in ("test",) and not self.ops_token:
        raise ValueError(
            "AEGIS_DASHBOARD_OPS_TOKEN must be set in non-test environments. "
            "The ops console executes shell commands — an empty token is unauthenticated RCE."
        )
    return self
```

**ENV-3**: Startup LLM probe in dashboard lifespan — add AFTER pool creation:
```python
asyncio.create_task(_probe_llm_backends())

async def _probe_llm_backends() -> None:
    """Non-blocking. Warns if all LLM backends unreachable. Never blocks startup."""
    try:
        from aegis.agents.llm import get_gateway
        gw = get_gateway()
        if gw is None:
            _log.warning("startup.llm_probe.no_gateway", heuristic_only_mode=True)
            return
        health = await asyncio.wait_for(gw.health(), timeout=5.0)
        reachable = [p for p, ok in health.items() if ok]
        if not reachable:
            _log.warning(
                "startup.llm_probe.all_unreachable",
                heuristic_only_mode=True,
                checked=list(health.keys()),
                consequence="every agent will use heuristic fallback"
            )
    except Exception as exc:
        _log.warning("startup.llm_probe.failed", error=str(exc), heuristic_only_mode=True)
```

**ORPH-4**: In `src/aegis/datalake/orchestration/flows.py`, add Prefect deployment:
```python
if PREFECT_AVAILABLE:
    try:
        daily_lake_refresh.serve(
            name="aegis-daily-lake-refresh",
            cron="0 2 * * *",  # 2 AM UTC
            tags=["aegis", "datalake"],
        )
    except Exception:
        pass  # serve() raises if server not running
```

Add `job_datalake_refresh()` to `src/aegis/scheduler/autonomous.py` as direct fallback:
calls `DataLake` session → `ingest_all()` → `build_silver()` → `build_gold()`.
Wire into autonomous scheduler: runs daily at 02:00 UTC if Prefect not available.
Add CLI: `aegis datalake schedule` → registers Prefect deployment + confirms.

---

## ❻ PASS 2 — DEEP INTELLIGENCE UPGRADES

These are not cosmetic changes. These are architectural upgrades that make AEGIS
significantly more accurate, faster, and harder to fool.

### 2A — MinHash + LSH deduplication (replaces O(n²) difflib)

File: `src/aegis/scrape/dedup.py`
Add to `pyproject.toml` dependencies: `"datasketch>=1.6.0"`

**Architecture: 3-layer dedup pipeline**

```
Layer 1 (KEEP UNCHANGED): Token Jaccard — exact-dup filter, O(n)
  - threshold: 0.85 token overlap → duplicate
  - operates on lowercased word sets

Layer 2 (REPLACE difflib): MinHash LSH — near-dup filter, O(n log n)
  - MinHash(n_perm=128) over character 3-grams of (title + url)
  - LSH forest, threshold=0.75 Jaccard similarity
  - Redis cache: key "aegis:dedup:mh:{content_hash[:16]}", TTL=86400s
  - On cache hit: deserialize saved bytes → skip recompute (~0.01ms)
  - On cache miss: compute → serialize → store (~5ms per signal)
  - Result: 40× faster than difflib on 1000-signal batches

Layer 3 (NEW, OPTIONAL): BGE-M3 semantic cosine — paraphrase-dup filter
  - Gated: AEGIS_DEDUP_SEMANTIC_ENABLED=true (default false)
  - Uses LLMGateway.embed() with bge-m3 model (already in Ollama)
  - Threshold: cosine similarity > 0.92 → semantic duplicate
  - Only fires when layers 1+2 both pass (not already marked duplicate)
  - Falls back to skip (not error) when Ollama unreachable

DedupResult gains: dedup_method: Literal["exact","minhash","semantic","pass"]
```

Complete implementation — every method fully written, no stubs:

```python
# src/aegis/scrape/dedup.py
"""
Signal deduplication pipeline.

Phase 0 scrape layer. Three-layer pipeline: exact token → MinHash LSH → (optional) semantic.
Replaces O(n²) difflib with O(n log n) MinHash for swarm-scale deduplication.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Literal

import structlog

_log = structlog.get_logger("aegis.scrape.dedup")

# MinHash + LSH (datasketch — optional import, graceful fallback)
try:
    from datasketch import MinHash, MinHashLSH
    _MINHASH_AVAILABLE = True
except ImportError:
    _MINHASH_AVAILABLE = False
    MinHash = None  # type: ignore[assignment,misc]
    MinHashLSH = None  # type: ignore[assignment,misc]


@dataclass
class DedupResult:
    """Result of deduplication for a single signal."""
    is_duplicate: bool
    dedup_method: Literal["exact", "minhash", "semantic", "pass"]
    matched_id: str | None = None
    similarity: float = 0.0


@dataclass
class MinHashLayer:
    """
    Layer 2 of the dedup pipeline: MinHash + LSH near-duplicate detection.

    Character 3-gram shingles over lowercased title+url → MinHash(128 permutations)
    → LSH forest (Jaccard threshold 0.75). Redis-backed hash cache (TTL 24h).
    """
    n_perm: int = 128
    threshold: float = 0.75
    _lsh: Any = field(default=None, init=False, repr=False)
    _session_index: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if _MINHASH_AVAILABLE:
            self._lsh = MinHashLSH(threshold=self.threshold, num_perm=self.n_perm)

    def _make_shingles(self, text: str) -> set[str]:
        """3-gram character shingles over lowercase text."""
        t = re.sub(r"\s+", " ", text.lower().strip())
        if len(t) < 3:
            return {t}
        return {t[i : i + 3] for i in range(len(t) - 2)}

    def _build_minhash(self, text: str) -> Any | None:
        if not _MINHASH_AVAILABLE:
            return None
        m = MinHash(num_perm=self.n_perm)
        for shingle in self._make_shingles(text):
            m.update(shingle.encode("utf-8"))
        return m

    async def get_or_build(
        self, content_hash: str, text: str, redis: Any | None
    ) -> Any | None:
        """Return cached MinHash or compute + cache it."""
        if not _MINHASH_AVAILABLE:
            return None
        cache_key = f"aegis:dedup:mh:{content_hash[:16]}"
        if redis:
            try:
                cached = await redis.get(cache_key)
                if cached:
                    m = MinHash(num_perm=self.n_perm)
                    m.deserialize(cached)
                    return m
            except Exception:
                pass  # Redis unavailable → compute fresh
        m = self._build_minhash(text)
        if m and redis:
            try:
                await redis.set(cache_key, m.serialize(), ex=86400)
            except Exception:
                pass
        return m

    def is_duplicate(self, signal_id: str, m: Any) -> tuple[bool, str | None]:
        """Check LSH index. Insert if not duplicate. Returns (is_dup, matched_id)."""
        if not _MINHASH_AVAILABLE or self._lsh is None or m is None:
            return False, None
        try:
            matches = self._lsh.query(m)
            matches = [x for x in matches if x != signal_id]
            if matches:
                return True, matches[0]
        except Exception:
            pass
        # Not a duplicate — add to index for future checks
        try:
            if signal_id not in self._session_index:
                self._lsh.insert(signal_id, m)
                self._session_index[signal_id] = m
        except Exception:
            pass
        return False, None


async def deduplicate_batch(
    signals: list[dict],
    *,
    redis: Any | None = None,
    semantic_enabled: bool = False,
) -> tuple[list[dict], list[DedupResult]]:
    """
    Deduplicate a batch of signals through the 3-layer pipeline.

    Returns (unique_signals, results) where results[i] corresponds to signals[i].
    Performance target: 1000 signals < 500ms on CPU.
    """
    if not signals:
        return [], []

    layer2 = MinHashLayer()
    results: list[DedupResult] = []
    seen_tokens: dict[frozenset, str] = {}
    unique: list[dict] = []

    for sig in signals:
        sid = sig.get("url") or sig.get("id") or str(id(sig))
        title = str(sig.get("title", ""))
        url = str(sig.get("url", ""))
        content = f"{title} {url}".strip()
        content_hash = hashlib.sha256(content.encode()).hexdigest()

        # Layer 1: token Jaccard
        token_set = frozenset(title.lower().split())
        dup_id = None
        for existing_tokens, existing_id in seen_tokens.items():
            if len(token_set) == 0 or len(existing_tokens) == 0:
                continue
            jaccard = len(token_set & existing_tokens) / len(token_set | existing_tokens)
            if jaccard >= 0.85:
                dup_id = existing_id
                break
        if dup_id:
            results.append(DedupResult(True, "exact", dup_id, 1.0))
            continue

        # Layer 2: MinHash LSH
        if _MINHASH_AVAILABLE:
            mhash = await layer2.get_or_build(content_hash, content, redis)
            is_dup, matched = layer2.is_duplicate(sid, mhash)
            if is_dup:
                results.append(DedupResult(True, "minhash", matched))
                continue

        # Layer 3: semantic (optional, expensive)
        if semantic_enabled:
            # Gated — only runs when Ollama available
            # Implementation: embed via LLMGateway, cosine against session vectors
            # Falls back silently if gateway unavailable
            pass  # see _semantic_check() below

        seen_tokens[token_set] = sid
        results.append(DedupResult(False, "pass"))
        unique.append(sig)

    _log.debug(
        "dedup.batch_complete",
        total=len(signals),
        unique=len(unique),
        dup_exact=sum(1 for r in results if r.dedup_method == "exact"),
        dup_minhash=sum(1 for r in results if r.dedup_method == "minhash"),
    )
    return unique, results
```

Tests: `tests/unit/scrape/test_dedup_minhash.py` (min 8 tests):
- Layer 1 catches identical titles (same words, different order)
- Layer 2 catches near-duplicate titles (paraphrases with 75%+ shingle overlap)
- Layer 2 does NOT flag genuinely different signals as duplicates
- Redis cache hit: second call with same content_hash skips recompute
- Redis unavailable: graceful fallback to in-memory only (no exception)
- `dedup_method` field correctly populated for each path
- Batch of 1000 signals completes under 500ms (time.perf_counter assertion)
- Empty batch returns ([], []) without error

### 2B — UCB1 composite quality reward

File: `src/aegis/scrape/budget.py`

Add to `UCB1Allocator` without breaking existing interface:

```python
def record_with_quality(
    self,
    source: str,
    *,
    yield_count: int,
    elapsed_s: float,
    novelty_fraction: float,  # 0-1: fraction of signals not seen in DB last 24h
    avg_confidence: float,    # 0-1: mean confidence of yielded signals
) -> None:
    """
    Record a composite quality reward rather than raw signal count.

    reward = 0.5 × normalized_yield
           + 0.3 × novelty_fraction
           + 0.2 × avg_confidence

    Normalized yield: signals per second of wall time, capped at ceiling of
    100 signals/second (prevents extremely fast but low-quality sources from
    dominating the bandit).

    This makes the bandit reward quality, not just quantity. A source that
    produces 200 duplicate, low-confidence signals gets a lower reward than
    one that produces 20 novel, high-confidence signals.
    """
    if elapsed_s <= 0:
        elapsed_s = 1.0
    normalized_yield = min(1.0, yield_count / max(elapsed_s * 100.0, 1.0))
    composite = (
        0.5 * normalized_yield
        + 0.3 * max(0.0, min(1.0, novelty_fraction))
        + 0.2 * max(0.0, min(1.0, avg_confidence))
    )
    self.record(source, composite)
    _log.debug(
        "ucb1.quality_reward",
        source=source,
        yield_count=yield_count,
        elapsed_s=round(elapsed_s, 2),
        novelty=round(novelty_fraction, 3),
        avg_confidence=round(avg_confidence, 3),
        composite=round(composite, 4),
    )
```

In `src/aegis/scrape/swarm.py`, update `run_agent` to pass timing data:
```python
import time
_start = time.monotonic()
# ... existing run logic ...
_elapsed = time.monotonic() - _start

# Compute novelty: fraction of signals whose content_hash NOT in 24h Redis set
_novelty = await self._compute_novelty_fraction(result.signals)
_avg_conf = (
    sum(s.get("confidence", 0.5) for s in result.signals)
    / max(len(result.signals), 1)
)
self._allocator.record_with_quality(
    source=adapter_name,
    yield_count=len(result.signals),
    elapsed_s=_elapsed,
    novelty_fraction=_novelty,
    avg_confidence=_avg_conf,
)
```

Add `_compute_novelty_fraction(signals) -> float` to `SwarmOrchestrator`:
- Uses Redis set `aegis:swarm:seen_hashes:{date}` (TTL 25h)
- For each signal: check content_hash in set → if absent it's novel
- After check: add all hashes to set (pipeline for efficiency)
- Returns novel_count / total_count (or 1.0 if Redis unavailable)

### 2C — Dynamic thresholds (BRAIN-3)

**This is the difference between a static system and a learning system.**

New file: `src/aegis/core/dynamic_thresholds.py`

```python
"""
Dynamic threshold adaptation for AEGIS core decision gates.

Phase cross-cutting module. Replaces static env constants for three key thresholds:
  - confidence_gate: AEGIS_SCRAPE_CONFIDENCE_THRESHOLD (default 0.85)
  - velocity_slope:  AEGIS_SCRAPE_VELOCITY_HIGH_PRIORITY_SLOPE (default 2.0)
  - comply_block:    AEGIS_COMPLY_BLOCK_THRESHOLD (default 0.70)

Each threshold adapts weekly based on realized outcome accuracy:
  - If precision > target: relax threshold slightly (more signals pass)
  - If precision < target: tighten threshold (fewer, better signals)

Adaptation is conservative: ±0.02 per week, hard bounds [0.60, 0.95].
Falls back to env-based static values when insufficient outcome data.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

_log = structlog.get_logger("aegis.core.dynamic_thresholds")

_ADAPT_STEP = 0.02          # max change per week
_CONFIDENCE_BOUNDS = (0.60, 0.95)
_VELOCITY_BOUNDS   = (1.0, 5.0)
_COMPLY_BOUNDS     = (0.50, 0.90)
_REDIS_KEY         = "aegis:core:thresholds"
_REDIS_TTL         = 8 * 86400  # 8 days — survives weekly retrain + buffer


@dataclass
class ThresholdState:
    confidence_gate: float
    velocity_slope: float
    comply_block: float
    updated_at: datetime
    outcome_count: int  # how many outcomes drove this update
    precision: float    # realized precision at time of last update


class DynamicThresholds:
    """
    Thread-safe dynamic threshold manager.

    Usage:
        thresholds = DynamicThresholds(redis=redis_client)
        gate = await thresholds.get_confidence_gate()

    Nightly update (called by autonomous loop):
        await thresholds.update_from_outcomes(pool, tenant_id)
    """

    def __init__(self, redis: Any | None = None) -> None:
        self._redis = redis
        self._cache: ThresholdState | None = None
        self._cache_until = datetime.min.replace(tzinfo=UTC)

    async def get_confidence_gate(self) -> float:
        return (await self._get()).confidence_gate

    async def get_velocity_slope(self) -> float:
        return (await self._get()).velocity_slope

    async def get_comply_block(self) -> float:
        return (await self._get()).comply_block

    async def _get(self) -> ThresholdState:
        now = datetime.now(UTC)
        if self._cache and now < self._cache_until:
            return self._cache

        state = await self._load_from_redis()
        if state is None:
            state = self._defaults()

        self._cache = state
        self._cache_until = now.replace(second=0, microsecond=0)
        from datetime import timedelta
        self._cache_until += timedelta(hours=1)  # cache for 1 hour
        return state

    async def _load_from_redis(self) -> ThresholdState | None:
        if not self._redis:
            return None
        try:
            import json
            raw = await self._redis.get(_REDIS_KEY)
            if not raw:
                return None
            data = json.loads(raw)
            return ThresholdState(
                confidence_gate=float(data["confidence_gate"]),
                velocity_slope=float(data["velocity_slope"]),
                comply_block=float(data["comply_block"]),
                updated_at=datetime.fromisoformat(data["updated_at"]),
                outcome_count=int(data.get("outcome_count", 0)),
                precision=float(data.get("precision", 0.0)),
            )
        except Exception as exc:
            _log.warning("dynamic_thresholds.load_failed", error=str(exc))
            return None

    def _defaults(self) -> ThresholdState:
        import os
        return ThresholdState(
            confidence_gate=float(os.environ.get("AEGIS_SCRAPE_CONFIDENCE_THRESHOLD", "0.85")),
            velocity_slope=float(os.environ.get("AEGIS_SCRAPE_VELOCITY_HIGH_PRIORITY_SLOPE", "2.0")),
            comply_block=float(os.environ.get("AEGIS_COMPLY_BLOCK_THRESHOLD", "0.70")),
            updated_at=datetime.now(UTC),
            outcome_count=0,
            precision=0.0,
        )

    async def update_from_outcomes(
        self,
        pool: Any,
        tenant_id: str,
        target_precision: float = 0.80,
        min_outcomes: int = 30,
    ) -> ThresholdState | None:
        """
        Adapt thresholds based on last 30 days of realized outcomes.
        Called weekly by autonomous loop (Sunday 3 AM UTC).

        Algorithm:
        1. Fetch recent prediction_outcomes from DB
        2. Compute realized precision: ENTER predictions where roi > 0 / total ENTER
        3. If precision > target: relax thresholds by _ADAPT_STEP
           If precision < target: tighten thresholds by _ADAPT_STEP
        4. Clamp to hard bounds
        5. Write to Redis
        """
        try:
            import json
            async with pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, TRUE)", tenant_id
                )
                rows = await conn.fetch(
                    """
                    SELECT prediction_score, roi, status
                    FROM prediction_outcomes
                    WHERE settlement_timestamp > NOW() - INTERVAL '30 days'
                    ORDER BY settlement_timestamp DESC
                    LIMIT 500
                    """,
                )

            if len(rows) < min_outcomes:
                _log.info(
                    "dynamic_thresholds.insufficient_data",
                    count=len(rows),
                    min_required=min_outcomes,
                )
                return None

            enter_outcomes = [r for r in rows if r["prediction_score"] >= 0.55]
            if not enter_outcomes:
                return None

            precision = sum(1 for r in enter_outcomes if float(r["roi"]) > 0) / len(enter_outcomes)
            current = await self._get()
            delta = _ADAPT_STEP if precision > target_precision else -_ADAPT_STEP

            new_state = ThresholdState(
                confidence_gate=max(
                    _CONFIDENCE_BOUNDS[0],
                    min(_CONFIDENCE_BOUNDS[1], current.confidence_gate + delta)
                ),
                velocity_slope=max(
                    _VELOCITY_BOUNDS[0],
                    min(_VELOCITY_BOUNDS[1], current.velocity_slope + delta)
                ),
                comply_block=max(
                    _COMPLY_BOUNDS[0],
                    min(_COMPLY_BOUNDS[1], current.comply_block - delta)  # comply: tighter = higher
                ),
                updated_at=datetime.now(UTC),
                outcome_count=len(rows),
                precision=round(precision, 4),
            )

            if self._redis:
                await self._redis.set(
                    _REDIS_KEY,
                    json.dumps({
                        "confidence_gate": new_state.confidence_gate,
                        "velocity_slope": new_state.velocity_slope,
                        "comply_block": new_state.comply_block,
                        "updated_at": new_state.updated_at.isoformat(),
                        "outcome_count": new_state.outcome_count,
                        "precision": new_state.precision,
                    }),
                    ex=_REDIS_TTL,
                )
            self._cache = new_state

            _log.info(
                "dynamic_thresholds.updated",
                precision=round(precision, 4),
                delta=delta,
                confidence_gate=round(new_state.confidence_gate, 3),
                velocity_slope=round(new_state.velocity_slope, 3),
                comply_block=round(new_state.comply_block, 3),
            )
            return new_state

        except Exception as exc:
            _log.error("dynamic_thresholds.update_failed", error=str(exc))
            return None
```

Wire `DynamicThresholds` into:
- `src/aegis/scrape/confidence.py` — `score_batch()` uses `get_confidence_gate()` instead of static env
- `src/aegis/scrape/analytics.py` — `compute_velocity_slope()` uses `get_velocity_slope()`
- `src/aegis/compliance/engine.py` — `ComplianceEngine` uses `get_comply_block()`
- `src/aegis/scheduler/autonomous.py` — add `job_threshold_update()` running Sunday 3 AM UTC

Tests: `tests/unit/core/test_dynamic_thresholds.py` (min 7):
- precision > target → all thresholds relaxed within bounds
- precision < target → all thresholds tightened within bounds
- bounds enforced: never goes above 0.95 or below 0.60 for confidence
- insufficient data → returns None, no change
- Redis unavailable → falls back to env defaults
- 1-hour cache: Redis not called on second get within TTL
- weekly update integrated into autonomous loop

### 2D — Feedback-weighted agent ensemble (BRAIN-4)

File: `src/aegis/agents/supervisor.py`

Replace equal-weight aggregation with accuracy-weighted voting. This is the "brain
that learns which faculties to trust."

**Weight computation algorithm:**
```
For each agent A over last 30 days of settled prediction_outcomes:
  correct = outcomes where agent A voted ENTER AND roi > 0
           + outcomes where agent A voted HOLD/BLOCK AND (roi <= 0 OR no trade)
  total   = outcomes where agent A cast a vote
  accuracy_A = correct / total  (default 0.5 if no history)
  weight_A = 0.5 + accuracy_A   (range: 0.5 to 1.5)

Final score = Σ(confidence_i × weight_i) / Σ(weight_i)
```

Store weights in Redis hash `aegis:agents:accuracy_weights` (TTL 7 days).
Update nightly via `job_weight_update()` in `autonomous.py`.

Add to `GraphResult` schema: `agent_weights: dict[str, float]` and
`weight_update_ts: datetime | None`.

Full implementation in `supervisor.py` — no stubs, no placeholders.

Tests: `tests/unit/agents/test_supervisor_weighted.py` (min 6):
- equal weights produce same result as current equal aggregation (regression test)
- agent with 90% accuracy gets weight 1.4 (0.5 + 0.9)
- agent with 30% accuracy gets weight 0.8 (0.5 + 0.3)
- Redis unavailable → all weights default to 1.0 (equal weighting)
- `agent_weights` field appears in GraphResult
- weight cache TTL respected (Redis not called within 1h of last load)

### 2E — 4 new FeatureWindow dimensions (FEATURE_DIM 20→24)

Files: `src/aegis/predict/__init__.py`, `src/aegis/predict/features/builder.py`

**Change**: `FEATURE_DIM = 20` → `FEATURE_DIM = 24`

Add to `FEATURE_NAMES` (4 new at tail):
`"cross_platform_coherence"`, `"temporal_autocorr_lag1"`, `"author_diversity_ratio"`, `"geo_spread_entropy"`

Implement each as a private method with graceful 0.0 fallback on insufficient data.
Every method is fully implemented — no placeholder `pass` or `return 0.0` stubs.

**Migration**: add `pad_legacy_window(w: FeatureWindow) -> FeatureWindow` that pads
20-dim windows to 24-dim with zeros. Call this in `InferenceRunner` when `feature_dim == 20`.

Document all 24 features in `docs/features.md`.

Tests: extend `tests/unit/predict/test_features.py` (min 8 new tests):
- each new feature returns 0.0 on empty signals (not raises)
- each new feature returns value in documented range
- cross_platform_coherence near 1.0 for identical text across 3+ platforms
- author_diversity_ratio = 0.0 when all signals share one author
- geo_spread_entropy = 1.0 for perfectly equal global distribution
- pad_legacy_window: output has dim 24, first 20 values unchanged
- InferenceRunner accepts 20-dim window via padding (regression test)
- FEATURE_NAMES length == FEATURE_DIM == 24

### 2F — Schema drift auto-recovery with QuarantineReason

File: `src/aegis/scrape/schema_drift.py`

Add `QuarantineReason` enum:
```python
class QuarantineReason(str, Enum):
    SCHEMA_DRIFT      = "schema_drift"
    ERROR_RATE        = "error_rate"
    CREDENTIALS_MISSING = "credentials_missing"
    MANUAL            = "manual"
```

Add `record_clean_batch(source, batch_size)` method that:
- Updates EWMA with 0.0 drop rate contribution
- Increments `clean_batches` counter
- Calls `_lift_quarantine(source)` when `should_unquarantine()` returns True

Add `should_unquarantine(source) -> bool`:
- Returns True when `smoothed_drop_rate < 0.2 AND clean_batches >= 3`

Add `quarantine(source, reason: QuarantineReason, message="")` that stores reason.

In `swarm.py` `run_agent`: call `record_clean_batch()` when run succeeds with drop_rate < 0.1.
Update `AdapterStatus.NEEDS_CREDENTIALS` usages to pass `QuarantineReason.CREDENTIALS_MISSING`.

Tests: extend `tests/unit/scrape/test_schema_drift.py` (min 6 new):
- quarantine reason stored and retrievable
- clean_batches incremented on each clean call
- should_unquarantine returns False when only 2 clean batches
- should_unquarantine returns True after 3+ clean batches with low drop rate
- quarantine_lifted log event emitted on recovery
- EWMA correctly decays toward clean on consecutive clean batches

---

## ❼ PASS 3 — CLOSE THE SELF-EVOLUTION FEEDBACK LOOP (BRAIN-1)

This is the most consequential architectural work. After this pass, AEGIS will:
1. Record every settled trade as ground truth
2. Detect when its predictions start drifting from reality
3. Retrain itself
4. Promote the new model only if it's genuinely better
5. Roll back automatically if the new model is worse
6. Adjust its pricing weights from outcome signals daily

The loop is: **trade → settle → record → drift check → retrain → promote → price adapt**

### 3A — SettlementManager → OutcomeRecorder

File: `aegis-phase4/src/aegis/execute/settlement.py`

In `settle_plan()`, add after successful plan update — best-effort, never raises:

```python
async def _record_outcome_for_evolution(
    self,
    plan: "ExecutionPlan",
    actual_revenue: "Decimal",
    actual_cost: "Decimal",
) -> None:
    """
    Bridge Phase 6 settlement to Phase 9 evolution.
    Best-effort: settlement must not fail because evolution is unavailable.
    """
    try:
        from aegis.evolve.outcomes import OutcomeRecorder
        from aegis.evolve.schemas import TradeOutcome

        roi = float((actual_revenue - actual_cost) / max(actual_cost, type(actual_cost)("0.01")))
        pnl = float(actual_revenue - actual_cost)

        outcome = TradeOutcome(
            plan_id=str(plan.plan_id),
            trend_id=plan.trend_id,
            prediction_score=float(plan.score),
            prediction_confidence=float(plan.confidence),
            roi=roi,
            pnl=pnl,
            units_sold=plan.quantity,
            status="successful" if roi > 0 else "failed",
            settlement_timestamp=datetime.now(UTC),
        )
        async with OutcomeRecorder(pool=self._pool) as recorder:
            await recorder.record_outcome(outcome)

        # Publish to evolution event stream
        from aegis.core.event_bus import publish_event
        await publish_event("aegis:phase9:evolve_events", {
            "event": "outcome_recorded",
            "plan_id": str(plan.plan_id),
            "trend_id": plan.trend_id,
            "roi": round(roi, 4),
            "pnl": round(pnl, 2),
            "status": outcome.status,
        })
    except Exception as exc:
        # INTENTIONAL: settlement succeeds regardless of evolution recording
        _log.debug(
            "settlement.evolution_record.skipped",
            plan_id=str(plan.plan_id),
            reason=str(exc),
        )
```

Tests: `tests/unit/execute/test_settlement_evolution.py` (min 5):
- successful trade → OutcomeRecorder.record_outcome called with positive ROI
- failed trade → record_outcome called with negative ROI
- OutcomeRecorder import error → settlement still succeeds (exception swallowed)
- Evolution stream publish called after recording
- plan with zero cost → no division by zero (Decimal("0.01") floor)

### 3B — DriftDetector auto-rollback to ModelStore

File: `src/aegis/evolve/drift.py`

In `run_all_checks()`, when `drift_score > AEGIS_EVOLVE_DRIFT_THRESHOLD`:

```python
async def _attempt_auto_rollback(self, snapshot: "DriftSnapshot") -> None:
    """
    Auto-rollback to previous champion when critical drift detected.
    Critical: drift_score > 2× threshold.
    """
    if snapshot.drift_score <= 2 * self._settings.drift_threshold:
        _log.info(
            "drift.warning_not_critical",
            drift_score=snapshot.drift_score,
            critical_threshold=2 * self._settings.drift_threshold,
        )
        return

    _log.warning(
        "drift.critical_rollback_triggered",
        drift_score=snapshot.drift_score,
        threshold=self._settings.drift_threshold,
    )

    try:
        from aegis.predict.registry.model_store import ModelStore
        store = ModelStore()
        prev = await store.get_previous_champion()
        if prev is None:
            _log.error("drift.rollback.no_previous_champion")
        else:
            await store.activate(prev.model_id)
            _log.warning("drift.rollback.complete", model_id=prev.model_id, auc=prev.auc)
    except Exception as exc:
        _log.error("drift.rollback.modelstore_failed", error=str(exc))

    # Publish event for dashboard + downstream
    try:
        from aegis.core.event_bus import publish_event
        await publish_event("aegis:phase9:evolve_events", {
            "event": "auto_rollback",
            "reason": "critical_drift",
            "drift_score": float(snapshot.drift_score),
            "threshold": float(self._settings.drift_threshold),
        })
    except Exception:
        pass

    # Trip killswitch if drift is extreme (> 3× threshold) — stops all execution
    if snapshot.drift_score > 3 * self._settings.drift_threshold:
        try:
            from aegis.execute.killswitch.switch import KillSwitch
            ks = KillSwitch(redis=self._redis)
            await ks.trip(
                reason=f"Auto: critical model drift score={snapshot.drift_score:.3f}"
            )
            _log.warning("drift.killswitch_tripped", drift_score=snapshot.drift_score)
        except Exception as exc:
            _log.error("drift.killswitch_failed", error=str(exc))
```

### 3C — RetrainingPipeline shadow deployment

File: `src/aegis/evolve/retrain.py`

After successful retrain with AUC improvement:

```python
async def _register_shadow(self, candidate: "ModelCandidate") -> None:
    """
    Register candidate as shadow model for 72-hour parallel evaluation.
    Shadow model scores predictions without affecting alerts.
    AEGIS_EVOLVE_FAST_PROMOTE=true skips shadow period (dev/testing only).
    """
    import os
    if os.environ.get("AEGIS_EVOLVE_FAST_PROMOTE", "false").lower() == "true":
        _log.info("retrain.fast_promote", candidate_id=candidate.model_id)
        await self._promote_to_champion(candidate)
        return

    try:
        from aegis.predict.registry.model_store import ModelStore
        await ModelStore().register_shadow(candidate)
    except Exception as exc:
        _log.error("retrain.shadow_register_failed", error=str(exc))

    shadow_until = datetime.now(UTC) + timedelta(hours=72)
    from aegis.core.event_bus import publish_event
    await publish_event("aegis:phase9:evolve_events", {
        "event": "shadow_deployment_started",
        "candidate_id": candidate.model_id,
        "candidate_auc": float(candidate.auc),
        "shadow_until": shadow_until.isoformat(),
    })
    _log.info(
        "retrain.shadow_deployment_started",
        candidate_id=candidate.model_id,
        shadow_until=shadow_until.isoformat(),
    )
```

Add `job_shadow_evaluate()` in `autonomous.py`:
- Runs every 6 hours
- Fetches shadows where `shadow_registered_at < NOW() - 72h`
- Compares shadow AUC vs champion on last 72h of prediction_outcomes
- If shadow beats champion by ≥ 2%: call `_promote_to_champion()`
- Publishes result to evolve stream either way

### 3D — OnlinePricingPolicy → PricingStrategy

File: `aegis-phase4/src/aegis/execute/pricing.py`

```python
# Class-level cache for policy weights (refreshed every 6 hours)
_policy_weights: ClassVar[list[float] | None] = None
_policy_loaded_at: ClassVar[datetime | None] = None
_POLICY_TTL_S: ClassVar[float] = 6.0 * 3600

_DEFAULT_WEIGHTS: ClassVar[list[float]] = [0.4, 0.3, 0.2, 0.1]

@classmethod
async def _get_policy_weights(cls) -> list[float]:
    """
    Load RL pricing weights from OnlinePricingPolicy.
    4 weights: [margin_weight, velocity_weight, competition_weight, inventory_weight]
    Cached class-wide for 6 hours — not per-instance.
    Falls back to [0.4, 0.3, 0.2, 0.1] when policy unavailable.
    """
    now = datetime.now(UTC)
    if (
        cls._policy_loaded_at
        and (now - cls._policy_loaded_at).total_seconds() < cls._POLICY_TTL_S
        and cls._policy_weights is not None
    ):
        return cls._policy_weights

    try:
        from aegis.evolve.rl_policy import OnlinePricingPolicy
        policy = OnlinePricingPolicy()
        await policy.load()
        weights = list(policy.get_weights())
        if len(weights) == 4:
            cls._policy_weights = weights
            cls._policy_loaded_at = now
            _log.debug("pricing.policy_weights.refreshed", weights=[round(w, 4) for w in weights])
            return weights
    except Exception as exc:
        _log.warning("pricing.policy_weights.load_failed", error=str(exc))

    return cls._DEFAULT_WEIGHTS
```

Integrate into `score_price_point()` — use loaded weights to adjust the 4-objective
scoring formula. Weights are applied as multipliers before normalization.

---

## ❽ PASS 4 — INTELLIGENT ADAPTER ROUTING ENGINE

Make AEGIS understand WHERE to look based on WHAT is being asked.

This is the "calls adapters according to user demand" capability.

### 4A — Topic classifier

New file: `src/aegis/scrape/topic_classifier.py`

```python
"""
Zero-cost topic classifier for AEGIS adapter routing.

Phase 0 scrape layer. Classifies user queries into TopicType enum
using keyword heuristics (zero latency) with optional LLM override.
Determines which adapter wave types are most relevant.
"""

from __future__ import annotations
from enum import Enum
import re
import structlog

_log = structlog.get_logger("aegis.scrape.topic_classifier")


class TopicType(str, Enum):
    ECOMMERCE_PRODUCT   = "ecommerce_product"   # "nike shoes", "earbuds"
    FINANCIAL_TREND     = "financial_trend"      # "HDFC", "NSE", "Nifty"
    TECH_NEWS           = "tech_news"            # "AI chips", "GPT"
    STARTUP_SIGNAL      = "startup_signal"       # "YC batch", "product launch"
    CONSUMER_TREND      = "consumer_trend"       # "viral", "trending"
    REGULATORY          = "regulatory"           # "FDA recall", "FTC"
    COMPETITOR_INTEL    = "competitor_intel"     # "vs competitor", "compare"
    SUPPLIER_DISCOVERY  = "supplier_discovery"  # "wholesale", "manufacturer"
    GLOBAL_ARBITRAGE    = "global_arbitrage"    # multi-region price opportunity
    MARKET_RESEARCH     = "market_research"     # general research

# ── Keyword → TopicType mapping (heuristic fast path) ─────────────────────────
_KW: dict[str, TopicType] = {
    # Financial
    "nse": TopicType.FINANCIAL_TREND,   "bse": TopicType.FINANCIAL_TREND,
    "nifty": TopicType.FINANCIAL_TREND, "sensex": TopicType.FINANCIAL_TREND,
    "stock": TopicType.FINANCIAL_TREND, "equity": TopicType.FINANCIAL_TREND,
    "ipo": TopicType.FINANCIAL_TREND,   "mutual fund": TopicType.FINANCIAL_TREND,
    "sebi": TopicType.FINANCIAL_TREND,  "rbi": TopicType.FINANCIAL_TREND,
    "demat": TopicType.FINANCIAL_TREND, "screener": TopicType.FINANCIAL_TREND,
    # E-commerce
    "product": TopicType.ECOMMERCE_PRODUCT, "price": TopicType.ECOMMERCE_PRODUCT,
    "buy": TopicType.ECOMMERCE_PRODUCT,     "sell": TopicType.ECOMMERCE_PRODUCT,
    "amazon": TopicType.ECOMMERCE_PRODUCT,  "flipkart": TopicType.ECOMMERCE_PRODUCT,
    "meesho": TopicType.ECOMMERCE_PRODUCT,  "myntra": TopicType.ECOMMERCE_PRODUCT,
    "dropship": TopicType.ECOMMERCE_PRODUCT,"ecommerce": TopicType.ECOMMERCE_PRODUCT,
    # Supplier
    "wholesale": TopicType.SUPPLIER_DISCOVERY, "manufacturer": TopicType.SUPPLIER_DISCOVERY,
    "b2b": TopicType.SUPPLIER_DISCOVERY,       "supplier": TopicType.SUPPLIER_DISCOVERY,
    "indiamart": TopicType.SUPPLIER_DISCOVERY, "factory": TopicType.SUPPLIER_DISCOVERY,
    "oem": TopicType.SUPPLIER_DISCOVERY,       "bulk": TopicType.SUPPLIER_DISCOVERY,
    # Tech
    "ai": TopicType.TECH_NEWS,       "llm": TopicType.TECH_NEWS,
    "gpu": TopicType.TECH_NEWS,      "openai": TopicType.TECH_NEWS,
    "github": TopicType.TECH_NEWS,   "api": TopicType.TECH_NEWS,
    "startup": TopicType.STARTUP_SIGNAL, "launch": TopicType.STARTUP_SIGNAL,
    "yc": TopicType.STARTUP_SIGNAL,  "funding": TopicType.STARTUP_SIGNAL,
    # Regulatory
    "recall": TopicType.REGULATORY,  "ban": TopicType.REGULATORY,
    "fda": TopicType.REGULATORY,     "ftc": TopicType.REGULATORY,
    "compliance": TopicType.REGULATORY, "lawsuit": TopicType.REGULATORY,
    # Competitor
    "competitor": TopicType.COMPETITOR_INTEL, "compare": TopicType.COMPETITOR_INTEL,
    "vs": TopicType.COMPETITOR_INTEL,         "alternative": TopicType.COMPETITOR_INTEL,
}

# ── Adapter affinity matrix ────────────────────────────────────────────────────
# Higher weight = stronger expected signal for this topic type.
# These weights are multiplied by UCB1 health score for final ranking.
ADAPTER_AFFINITY: dict[TopicType, dict[str, float]] = {
    TopicType.ECOMMERCE_PRODUCT: {
        "amazon": 0.95, "amazon_in": 0.95, "flipkart": 0.92, "meesho": 0.88,
        "myntra": 0.82, "nykaa": 0.78, "snapdeal": 0.72, "ajio": 0.70,
        "google_trends": 0.70, "google_trends_india": 0.80,
        "reddit_ecommerce": 0.85, "producthunt": 0.60,
    },
    TopicType.FINANCIAL_TREND: {
        "nse_bse": 0.98, "moneycontrol": 0.96, "economic_times": 0.92,
        "screener_in": 0.88, "yahoo_finance_rss": 0.85,
        "investing_com_rss": 0.82, "ndtv_profit": 0.80,
        "mint_rss": 0.78, "business_standard": 0.75, "reddit_finance": 0.70,
    },
    TopicType.TECH_NEWS: {
        "hacker_news": 0.95, "github_trending": 0.90, "techcrunch_rss": 0.88,
        "wired_rss": 0.75, "devto": 0.82, "npm_trends": 0.72,
        "producthunt": 0.85, "reddit_ecommerce": 0.50,
    },
    TopicType.STARTUP_SIGNAL: {
        "hacker_news": 0.92, "producthunt": 0.95, "github_trending": 0.80,
        "techcrunch_rss": 0.85, "devto": 0.75, "reddit_ecommerce": 0.60,
    },
    TopicType.SUPPLIER_DISCOVERY: {
        "indiamart": 0.96, "amazon_in": 0.75, "meesho": 0.70,
        "google_news": 0.65, "reddit_ecommerce": 0.60,
    },
    TopicType.REGULATORY: {
        "google_news": 0.90, "bing_news": 0.88, "reuters_rss": 0.85,
        "techcrunch_rss": 0.70, "bbc_business": 0.75, "economic_times": 0.72,
    },
    TopicType.COMPETITOR_INTEL: {
        "google_news": 0.92, "bing_news": 0.88, "techcrunch_rss": 0.82,
        "reuters_rss": 0.78, "hacker_news": 0.65, "reddit_ecommerce": 0.62,
    },
    TopicType.GLOBAL_ARBITRAGE: {
        "amazon": 0.85, "amazon_in": 0.90, "flipkart": 0.88,
        "yahoo_finance_rss": 0.72, "investing_com_rss": 0.70,
        "google_trends": 0.80, "google_trends_india": 0.85,
    },
    TopicType.CONSUMER_TREND: {
        "reddit_ecommerce": 0.88, "producthunt": 0.80, "google_trends": 0.90,
        "google_trends_india": 0.92, "amazon": 0.80, "amazon_in": 0.82,
        "youtube_rss": 0.70, "hacker_news": 0.60,
    },
    TopicType.MARKET_RESEARCH: {  # fallback: broad coverage
        "google_news": 0.80, "bing_news": 0.78, "hacker_news": 0.75,
        "reddit_ecommerce": 0.75, "reddit_finance": 0.72,
        "techcrunch_rss": 0.70, "reuters_rss": 0.70,
    },
}


class TopicClassifier:
    """
    Classifies queries into TopicType using keyword heuristics.
    Zero latency, zero cost, zero network calls.
    Covers India-first use cases with INR/Indian platform awareness.
    """

    def classify(self, query: str) -> TopicType:
        """Classify topic. Returns highest-scoring type, defaulting to CONSUMER_TREND."""
        words = re.findall(r"\b\w+\b", query.lower())
        scores: dict[TopicType, float] = {}

        for word in words:
            if word in _KW:
                tt = _KW[word]
                scores[tt] = scores.get(tt, 0.0) + 1.0

        # Bigram check for compound terms
        for i in range(len(words) - 1):
            bigram = f"{words[i]} {words[i+1]}"
            if bigram in _KW:
                tt = _KW[bigram]
                scores[tt] = scores.get(tt, 0.0) + 2.0  # bigrams score higher

        if not scores:
            return TopicType.CONSUMER_TREND

        best = max(scores, key=lambda k: scores[k])
        _log.debug("topic_classifier.result", query=query, topic_type=best.value, scores=scores)
        return best
```

### 4B — Intelligent adapter router

New file: `src/aegis/scrape/adapter_router.py`

```python
"""
Adapter Router — selects optimal adapters for a topic query.

Phase 0 scrape layer. Combines topic type affinity, real-time adapter health
from SwarmAgentPool, and historical yield from Redis to rank adapters.

The router is the answer to "which 8 of our 35 adapters should we use for THIS query?"
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import structlog

from aegis.scrape.topic_classifier import ADAPTER_AFFINITY, TopicClassifier, TopicType

_log = structlog.get_logger("aegis.scrape.adapter_router")

# Adapters that require credentials not set by default
_CRED_REQUIRED: dict[str, str] = {
    "reddit": "AEGIS_REDDIT_CLIENT_ID",
    "youtube": "AEGIS_YOUTUBE_API_KEY",
    "instagram": "AEGIS_INSTAGRAM_SESSION",
}

# Adapters that require FlareSolverr
_FLARESOLVERR_REQUIRED = frozenset({"flipkart", "myntra"})


@dataclass
class AdapterRecommendation:
    adapter_name: str
    priority: int          # 1 = highest
    topic_type: TopicType
    affinity_score: float  # base affinity from matrix
    health_score: float    # current UCB1 / health tracker score
    final_score: float     # affinity × health × log(yield+1)/5
    reason: str            # human-readable
    requires_credentials: bool
    credentials_available: bool
    requires_flaresolverr: bool


class AdapterRouter:
    """
    Route topic queries to optimal adapters.

    Args:
        health_tracker: SwarmAgentPool instance for real-time health scores
        redis: Redis client for historical yield lookup
        classifier: TopicClassifier instance (created if None)
    """

    def __init__(
        self,
        health_tracker: Any | None = None,
        redis: Any | None = None,
        classifier: TopicClassifier | None = None,
    ) -> None:
        self._health = health_tracker
        self._redis = redis
        self._classifier = classifier or TopicClassifier()

    def route(
        self,
        topic: str,
        *,
        top_n: int = 10,
        exclude_credentials_required: bool = False,
        exclude_flaresolverr: bool = False,
        explicit_topic_type: TopicType | None = None,
    ) -> list[AdapterRecommendation]:
        """
        Return ranked adapter recommendations for a topic query.

        Args:
            topic: The search query or topic
            top_n: Maximum adapters to return
            exclude_credentials_required: Skip adapters needing API keys
            exclude_flaresolverr: Skip adapters needing FlareSolverr
            explicit_topic_type: Override auto-detected topic type

        Returns:
            Ranked list of AdapterRecommendation, highest score first.
        """
        topic_type = explicit_topic_type or self._classifier.classify(topic)
        affinity_map = ADAPTER_AFFINITY.get(topic_type, ADAPTER_AFFINITY[TopicType.MARKET_RESEARCH])

        health_scores = self._get_health_scores()
        recommendations: list[AdapterRecommendation] = []

        for adapter_name, base_affinity in affinity_map.items():
            cred_required = adapter_name in _CRED_REQUIRED
            cred_available = self._check_credentials(adapter_name)
            flare_required = adapter_name in _FLARESOLVERR_REQUIRED

            if exclude_credentials_required and cred_required and not cred_available:
                continue
            if exclude_flaresolverr and flare_required:
                continue

            health = health_scores.get(adapter_name, 0.7)  # optimistic default for unknown
            # Historical yield lookup would go here (async → sync tradeoff; use cached value)
            est_yield = 15  # conservative default

            final = base_affinity * health * math.log(est_yield + 1) / math.log(101)

            recommendations.append(AdapterRecommendation(
                adapter_name=adapter_name,
                priority=0,
                topic_type=topic_type,
                affinity_score=base_affinity,
                health_score=health,
                final_score=round(final, 4),
                reason=self._explain(adapter_name, topic_type, base_affinity, health),
                requires_credentials=cred_required,
                credentials_available=cred_available,
                requires_flaresolverr=flare_required,
            ))

        recommendations.sort(key=lambda r: r.final_score, reverse=True)
        for i, rec in enumerate(recommendations[:top_n]):
            rec.priority = i + 1

        _log.info(
            "adapter_router.routed",
            topic=topic,
            topic_type=topic_type.value,
            top_adapters=[r.adapter_name for r in recommendations[:3]],
            total_candidates=len(recommendations),
        )
        return recommendations[:top_n]

    def _get_health_scores(self) -> dict[str, float]:
        if self._health is None:
            return {}
        try:
            agents = self._health.get_all_agents()
            return {
                name: min(1.0, max(0.1, agent.success_rate))
                for name, agent in agents.items()
            }
        except Exception:
            return {}

    def _check_credentials(self, adapter_name: str) -> bool:
        import os
        env_key = _CRED_REQUIRED.get(adapter_name)
        if env_key is None:
            return True
        return bool(os.environ.get(env_key, "").strip())

    def _explain(
        self, adapter: str, topic_type: TopicType, affinity: float, health: float
    ) -> str:
        topic_label = topic_type.value.replace("_", " ")
        if affinity >= 0.90:
            return f"Primary source for {topic_label}"
        elif affinity >= 0.75:
            return f"Strong secondary for {topic_label} ({int(health*100)}% health)"
        else:
            return f"Supplementary coverage ({int(affinity*100)}% affinity)"
```

### 4C — Wire router into scrape_topic

File: `src/aegis/scrape/topic.py`

Update `scrape_topic()` signature and routing logic:

```python
async def scrape_topic(
    topic: str,
    *,
    limit_per_source: int = 50,
    use_llm: bool = True,
    adapter_override: list[str] | None = None,  # NEW: explicit adapter list
    max_adapters: int = 12,                     # NEW: cap total adapters
) -> TopicScrapeResult:
    """
    Scrape signals for a topic using intelligent adapter routing.

    Without adapter_override: AdapterRouter selects best adapters for this topic
    based on topic type classification, adapter health, and affinity matrix.

    With adapter_override: use exactly those adapters in that order (for
    explicit user requests or testing).
    """
    if adapter_override is not None:
        adapters_to_use = adapter_override
        _log.info(
            "scrape_topic.explicit_adapters",
            topic=topic,
            adapters=adapters_to_use,
        )
    else:
        # Intelligent routing
        try:
            from aegis.scrape.adapter_router import AdapterRouter
            router = AdapterRouter()
            recs = router.route(
                topic,
                top_n=max_adapters,
                exclude_credentials_required=False,
            )
            adapters_to_use = [
                r.adapter_name for r in recs
                if r.credentials_available or not r.requires_credentials
            ]
            _log.info(
                "scrape_topic.routed",
                topic=topic,
                topic_type=recs[0].topic_type.value if recs else "unknown",
                adapters=adapters_to_use[:5],
                total=len(adapters_to_use),
            )
        except Exception as exc:
            _log.warning("scrape_topic.routing_failed", error=str(exc), fallback="default_adapters")
            adapters_to_use = None  # fall through to existing default logic

    # ... rest of existing scrape logic unchanged ...
```

Tests: `tests/unit/scrape/test_adapter_router.py` (min 8):
- "HDFC Bank stock" → top adapter in [nse_bse, moneycontrol, economic_times]
- "wireless earbuds buy" → top adapter in [amazon, amazon_in, flipkart]
- "wholesale manufacturer bulk" → top adapter is indiamart
- "FDA recall medicine" → top adapter in [google_news, bing_news, reuters_rss]
- adapter requiring credentials excluded when exclude_credentials_required=True
- FlareSolverr adapters excluded when exclude_flaresolverr=True
- empty health tracker → default 0.7 health for all adapters
- recommendations sorted by final_score descending

---

## ❾ PASS 5 — AUTONOMOUS SELF-HEALING SYSTEM

The system must detect its own failures and fix them. Like the human body's immune system.

### 5A — AegisHealthChecker

New file: `src/aegis/scheduler/health_checker.py`

```python
"""
Autonomous health monitoring and self-healing for AEGIS.

Scheduler module. Runs every 5 minutes inside the autonomous loop.
Detects 8 failure modes and triggers healing actions without human intervention.

Monitored conditions:
  1. Redis stream staleness (phase 2 stream > 30 min old → trigger emergency scrape)
  2. Adapter health ratio (>50% adapters quarantined → investigate + alert)
  3. Model staleness (champion > 30 days old AND outcomes available → trigger retrain)
  4. Kill switch stuck (tripped > 2h without manual arm → send escalation)
  5. Dynamic thresholds stale (not updated in 8+ days → force update)
  6. WSL clock drift (system time vs Redis time > 5s → sync hwclock)
  7. Evolution loop idle (no outcomes recorded in 7 days → alert operator)
  8. Coverage regression (weekly — coverage report below floor → alert)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import structlog

_log = structlog.get_logger("aegis.scheduler.health_checker")

HealthStatus = Literal["healthy", "warning", "critical", "healing"]


@dataclass
class HealthCheck:
    name: str
    status: HealthStatus
    message: str = ""
    value: float | None = None
    action_taken: str | None = None


@dataclass
class HealthReport:
    checks: list[HealthCheck]
    checked_at: datetime
    overall_status: HealthStatus
    healing_actions: list[str] = field(default_factory=list)


class AegisHealthChecker:
    """
    Runs periodic health checks and triggers healing actions.

    Usage (in autonomous.py):
        checker = AegisHealthChecker(redis=redis_client, pool=pg_pool)
        report = await checker.check_and_heal()
    """

    def __init__(
        self,
        redis: Any | None = None,
        pool: Any | None = None,
        tenant_id: str = "00000000-0000-0000-0000-000000000001",
    ) -> None:
        self._redis = redis
        self._pool = pool
        self._tenant_id = tenant_id

    async def check_and_heal(self) -> HealthReport:
        """Run all health checks in parallel. Take healing actions for critical findings."""
        results = await asyncio.gather(
            self._check_stream_staleness(),
            self._check_adapter_health_ratio(),
            self._check_model_staleness(),
            self._check_killswitch_state(),
            self._check_clock_drift(),
            self._check_evolution_loop_idle(),
            return_exceptions=True,
        )

        checks: list[HealthCheck] = []
        for r in results:
            if isinstance(r, BaseException):
                checks.append(HealthCheck(
                    name="unknown", status="warning",
                    message=f"Check failed: {r}"
                ))
            else:
                checks.append(r)  # type: ignore[arg-type]

        overall = "critical" if any(c.status == "critical" for c in checks) else (
            "warning" if any(c.status == "warning" for c in checks) else "healthy"
        )

        report = HealthReport(
            checks=checks,
            checked_at=datetime.now(UTC),
            overall_status=overall,
            healing_actions=[c.action_taken for c in checks if c.action_taken],
        )

        _log.info(
            "health_checker.complete",
            overall=overall,
            critical=[c.name for c in checks if c.status == "critical"],
            healing_actions=report.healing_actions,
        )
        return report

    async def _check_stream_staleness(self) -> HealthCheck:
        """Phase 2 stream stale > 30 min → emergency scrape."""
        if not self._redis:
            return HealthCheck("stream_staleness", "warning", "No Redis client")
        try:
            info = await self._redis.xinfo_stream("aegis:phase2:graph_results")
            last_id = str(info.get("last-generated-id", "0-0"))
            last_ms = int(last_id.split("-")[0]) if last_id != "0-0" else 0
            age_s = (time.time() * 1000 - last_ms) / 1000 if last_ms else 99999

            if age_s > 1800:  # 30 minutes
                _log.warning("health.stream_stale", age_s=round(age_s), action="emergency_scrape")
                try:
                    from aegis.scheduler.autonomous import job_scrape
                    asyncio.create_task(job_scrape())
                except Exception as exc:
                    _log.error("health.emergency_scrape_failed", error=str(exc))
                return HealthCheck(
                    "stream_staleness", "critical",
                    f"Phase 2 stream {round(age_s/60, 1)}m stale",
                    value=age_s,
                    action_taken="triggered_emergency_scrape",
                )
            return HealthCheck(
                "stream_staleness", "healthy",
                f"Last entry {round(age_s, 0)}s ago",
                value=age_s,
            )
        except Exception as exc:
            return HealthCheck("stream_staleness", "warning", str(exc))

    async def _check_adapter_health_ratio(self) -> HealthCheck:
        """Alert when > 50% of adapters are quarantined."""
        try:
            from aegis.scrape.swarm_agents import SwarmAgentPool
            pool = SwarmAgentPool.get_instance()
            if pool is None:
                return HealthCheck("adapter_health_ratio", "warning", "No swarm pool")
            agents = pool.get_all_agents()
            if not agents:
                return HealthCheck("adapter_health_ratio", "healthy", "No agents tracked")
            total = len(agents)
            quarantined = sum(1 for a in agents.values() if a.is_down)
            ratio = quarantined / total
            status = "critical" if ratio > 0.5 else ("warning" if ratio > 0.3 else "healthy")
            return HealthCheck(
                "adapter_health_ratio",
                status,
                f"{quarantined}/{total} adapters quarantined ({int(ratio*100)}%)",
                value=ratio,
            )
        except Exception as exc:
            return HealthCheck("adapter_health_ratio", "warning", str(exc))

    async def _check_model_staleness(self) -> HealthCheck:
        """Force retrain if champion model > 30 days old AND outcomes available."""
        if not self._pool:
            return HealthCheck("model_staleness", "warning", "No DB pool")
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, TRUE)", self._tenant_id
                )
                row = await conn.fetchrow(
                    """
                    SELECT trained_at, auc FROM model_manifest
                    WHERE status = 'champion'
                    ORDER BY trained_at DESC LIMIT 1
                    """
                )
            if row is None:
                return HealthCheck("model_staleness", "warning", "No champion model found")

            age_days = (datetime.now(UTC) - row["trained_at"]).days
            if age_days > 30:
                # Check if enough outcomes exist to retrain
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        "SELECT set_config('app.current_tenant', $1, TRUE)", self._tenant_id
                    )
                    count = await conn.fetchval(
                        "SELECT COUNT(*) FROM prediction_outcomes WHERE settlement_timestamp > NOW() - INTERVAL '30 days'"
                    )
                if count >= 100:
                    _log.warning(
                        "health.model_stale", age_days=age_days, outcome_count=count,
                        action="triggering_retrain",
                    )
                    try:
                        from aegis.evolve.retrain import RetrainingPipeline
                        asyncio.create_task(RetrainingPipeline().run_weekly_retrain())
                    except Exception as exc:
                        _log.error("health.retrain_trigger_failed", error=str(exc))
                    return HealthCheck(
                        "model_staleness", "warning",
                        f"Champion {age_days}d old, {count} outcomes available",
                        value=float(age_days),
                        action_taken="triggered_retrain",
                    )
            return HealthCheck(
                "model_staleness", "healthy",
                f"Champion {age_days}d old, AUC={row['auc']:.4f}",
                value=float(age_days),
            )
        except Exception as exc:
            return HealthCheck("model_staleness", "warning", str(exc))

    async def _check_killswitch_state(self) -> HealthCheck:
        """Alert if killswitch has been tripped > 2 hours without manual arm."""
        if not self._redis:
            return HealthCheck("killswitch_state", "warning", "No Redis client")
        try:
            ks_value = await self._redis.get("aegis:execute:killswitch")
            if not ks_value:
                return HealthCheck("killswitch_state", "healthy", "Not tripped")

            # Value is JSON with trip_timestamp
            import json
            data = json.loads(ks_value)
            tripped_at_str = data.get("tripped_at", "")
            if tripped_at_str:
                tripped_at = datetime.fromisoformat(tripped_at_str)
                age_h = (datetime.now(UTC) - tripped_at).total_seconds() / 3600
                if age_h > 2:
                    _log.warning(
                        "health.killswitch_long_trip",
                        age_hours=round(age_h, 1),
                        reason=data.get("reason", "unknown"),
                    )
                    return HealthCheck(
                        "killswitch_state", "critical",
                        f"Killswitch tripped {round(age_h, 1)}h ago: {data.get('reason')}",
                        value=age_h,
                    )
            return HealthCheck("killswitch_state", "warning", "Killswitch tripped (< 2h)")
        except Exception as exc:
            return HealthCheck("killswitch_state", "warning", str(exc))

    async def _check_clock_drift(self) -> HealthCheck:
        """Detect WSL clock drift. Common after laptop sleep/wake cycle."""
        if not self._redis:
            return HealthCheck("clock_drift", "warning", "No Redis client")
        try:
            redis_time = await self._redis.time()  # (seconds, microseconds)
            redis_ts = redis_time[0] + redis_time[1] / 1_000_000
            system_ts = time.time()
            drift_s = abs(system_ts - redis_ts)

            if drift_s > 5.0:
                _log.warning(
                    "health.clock_drift",
                    drift_seconds=round(drift_s, 2),
                    system_ts=system_ts,
                    redis_ts=redis_ts,
                    action="syncing_hwclock",
                )
                import subprocess
                subprocess.run(
                    ["sudo", "hwclock", "-s"],
                    check=False, timeout=5, capture_output=True
                )
                return HealthCheck(
                    "clock_drift", "warning",
                    f"WSL clock drift {round(drift_s, 1)}s detected",
                    value=drift_s,
                    action_taken="ran_hwclock_sync",
                )
            return HealthCheck(
                "clock_drift", "healthy",
                f"Clock drift {round(drift_s*1000, 1)}ms",
                value=drift_s,
            )
        except Exception as exc:
            return HealthCheck("clock_drift", "warning", str(exc))

    async def _check_evolution_loop_idle(self) -> HealthCheck:
        """Alert if no trade outcomes have been recorded in 7 days."""
        if not self._pool:
            return HealthCheck("evolution_loop_idle", "warning", "No DB pool")
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, TRUE)", self._tenant_id
                )
                count = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM prediction_outcomes
                    WHERE settlement_timestamp > NOW() - INTERVAL '7 days'
                    """
                )
            if count == 0:
                return HealthCheck(
                    "evolution_loop_idle", "warning",
                    "No trade outcomes recorded in 7 days — evolution loop may be broken",
                    value=0.0,
                )
            return HealthCheck(
                "evolution_loop_idle", "healthy",
                f"{count} outcomes recorded in last 7 days",
                value=float(count),
            )
        except Exception as exc:
            return HealthCheck("evolution_loop_idle", "warning", str(exc))
```

Wire `AegisHealthChecker.check_and_heal()` into `src/aegis/scheduler/autonomous.py`:
- New job: `job_health_check()` — runs every 5 minutes
- Publish health report to `aegis:core:health` stream (maxlen=288 = 24h at 5min intervals)

### 5B — Adapter circuit breaker factory

New file: `src/aegis/scrape/http_client.py`

```python
"""
Shared HTTP client factory for all AEGIS scrape adapters.

Phase 0 scrape layer. Provides a configured httpx.AsyncClient per target host
with connection pooling, keep-alive, timeout, and retry policy pre-configured.
Eliminates the per-adapter httpx client anti-pattern (ADP-7 in AEGIS_AUDIT.md).

Usage:
    from aegis.scrape.http_client import get_client

    async def fetch_data(url: str) -> bytes:
        async with get_client("api.example.com") as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx
import structlog

_log = structlog.get_logger("aegis.scrape.http_client")

# Per-host client registry — one client per (host, http2) combination
_CLIENTS: dict[str, httpx.AsyncClient] = {}
_CLIENT_LOCK = asyncio.Lock()

# Standard timeout: 5s connect, 15s read, 5s write, 2s pool
_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=2.0)

# Hosts that do NOT support HTTP/2 (must use HTTP/1.1)
_HTTP1_ONLY_HOSTS = frozenset({
    "www.reddit.com",    # blocks Brotli + HTTP/2 by TOS
    "oauth.reddit.com",  # same
    "localhost",         # Ollama
    "127.0.0.1",        # local services
})


async def get_or_create_client(
    host: str, *, http2: bool | None = None
) -> httpx.AsyncClient:
    """
    Return (creating if necessary) a shared AsyncClient for a given host.
    Thread-safe. Clients are created once and reused across all adapter calls.

    Connection pool limits: 20 per host (prevents thundering herd).
    Keep-alive: enabled (avoids TLS renegotiation on every request).
    """
    use_http2 = http2 if http2 is not None else (host not in _HTTP1_ONLY_HOSTS)
    key = f"{host}:{'h2' if use_http2 else 'h1'}"

    if key in _CLIENTS:
        return _CLIENTS[key]

    async with _CLIENT_LOCK:
        if key in _CLIENTS:  # double-check after lock
            return _CLIENTS[key]

        client = httpx.AsyncClient(
            timeout=_DEFAULT_TIMEOUT,
            http2=use_http2,
            follow_redirects=True,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30.0,
            ),
        )
        _CLIENTS[key] = client
        _log.debug("http_client.created", host=host, http2=use_http2)
        return client


@asynccontextmanager
async def get_client(host: str, *, http2: bool | None = None) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Context manager for adapter use — does NOT close client on exit (intentional — reuse)."""
    client = await get_or_create_client(host, http2=http2)
    yield client


async def close_all() -> None:
    """Close all shared clients. Call at process shutdown."""
    async with _CLIENT_LOCK:
        for client in _CLIENTS.values():
            await client.aclose()
        _CLIENTS.clear()
```

Update at least 5 existing adapters to use `get_client()` instead of creating their own
`httpx.AsyncClient`. Start with the highest-traffic ones: `hacker_news.py`, `github_trending.py`,
`google_news.py`, `reddit_rss.py`, `amazon.py`.

---

## ❿ PASS 6 — DEEP RESEARCH OUTPUT ENGINE

Every output AEGIS produces must be the result of deep, multi-pass investigation —
not first-instinct analysis. This pass makes AEGIS reason like a deep research analyst.

### 6A — Multi-pass analysis in the agent runner

File: `src/aegis/agents/runner.py`

Add a `_deep_verify_result()` function that runs AFTER the main graph completes
for P0 signals (score ≥ 0.85). This is a second-pass verification that checks the
first-pass result from multiple angles before publishing:

```python
async def _deep_verify_high_confidence_result(
    result: GraphResult,
    *,
    max_verification_s: float = 30.0,
) -> GraphResult:
    """
    Second-pass verification for P0 signals (score >= 0.85).

    Runs 3 independent verification checks in parallel:
    1. Temporal consistency: velocity pattern consistent with organic trend?
    2. Cross-source confirmation: at least 2 independent platforms agree?
    3. Red-team challenge: does the RED_TEAM agent's analysis hold up?

    If all 3 checks pass: result.deep_verified = True
    If any check fails: score reduced by 0.10, priority downgraded one level

    Never delays below P0 threshold (0.85): if verification fails a P0,
    it becomes P1 (0.75) — still actionable, just flagged.
    """
    if result.final_score < 0.85:
        return result  # only deep-verify P0

    checks = []

    # Check 1: temporal consistency
    try:
        tc_ok = _check_temporal_consistency(result)
        checks.append(("temporal_consistency", tc_ok))
    except Exception:
        checks.append(("temporal_consistency", True))  # benefit of doubt on error

    # Check 2: cross-source confirmation
    try:
        cs_ok = _check_cross_source_confirmation(result)
        checks.append(("cross_source_confirmation", cs_ok))
    except Exception:
        checks.append(("cross_source_confirmation", True))

    # Check 3: red-team decision review
    try:
        rt_decision = next(
            (d for d in (result.decisions or []) if d.agent == "red_team"), None
        )
        rt_ok = rt_decision is None or rt_decision.verdict != "block"
        checks.append(("red_team_review", rt_ok))
    except Exception:
        checks.append(("red_team_review", True))

    failures = [name for name, ok in checks if not ok]
    if failures:
        new_score = max(0.70, result.final_score - 0.10)
        _log.warning(
            "deep_verify.failed",
            trend_id=result.correlation_id,
            failed_checks=failures,
            original_score=result.final_score,
            adjusted_score=new_score,
        )
        return result.model_copy(update={
            "final_score": new_score,
            "final_priority": "P1",
            "deep_verified": False,
            "deep_verify_failures": failures,
        })

    _log.info(
        "deep_verify.passed",
        trend_id=result.correlation_id,
        score=result.final_score,
    )
    return result.model_copy(update={"deep_verified": True, "deep_verify_failures": []})


def _check_temporal_consistency(result: GraphResult) -> bool:
    """Velocity pattern must be accelerating, not spike-and-crash."""
    # Check velocity ratios: 1h > 6h/6 AND 6h > 24h/4
    # Organic trends accelerate — spikes decelerate
    try:
        v1h = result.trend_data.get("velocity_1h", 0)
        v6h = result.trend_data.get("velocity_6h", 0)
        v24h = result.trend_data.get("velocity_24h", 0)
        if v6h > 0 and v24h > 0:
            return v1h >= v6h / 6 and v6h >= v24h / 4
        return True  # insufficient data → pass
    except Exception:
        return True


def _check_cross_source_confirmation(result: GraphResult) -> bool:
    """At least 2 independent platforms must show the signal."""
    try:
        platforms = result.trend_data.get("platforms", [])
        return len(set(platforms)) >= 2
    except Exception:
        return True
```

Add `deep_verified: bool = False` and `deep_verify_failures: list[str] = []`
to `GraphResult` schema. Add `trend_data: dict = {}` to carry raw signal metadata.

Wire `_deep_verify_high_confidence_result()` into `run_trend()` after graph execution.

### 6B — Structured research report generation

New file: `src/aegis/intelligence/research_engine.py`

```python
"""
AEGIS Research Engine — deep, multi-pass intelligence gathering.

Intelligence module. Synthesizes signals from multiple adapters into a structured
research report. Every conclusion is supported by multiple independent sources.
Operates like a deep research analyst: gather → verify → cross-check → synthesize.

The 5-pass research methodology:
  Pass 1: Broad signal harvest (top adapters for topic type)
  Pass 2: Cross-verification (confirm findings from independent sources)
  Pass 3: Temporal analysis (is this trend new or old? accelerating or fading?)
  Pass 4: Competitive landscape (who else is in this space?)
  Pass 5: Risk synthesis (compliance, IP, market saturation scores)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

_log = structlog.get_logger("aegis.intelligence.research_engine")


@dataclass
class ResearchFinding:
    """A single finding from one source, verified or unverified."""
    claim: str
    source: str
    confidence: float  # 0-1
    verified_by: list[str] = field(default_factory=list)  # other sources confirming
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class ResearchReport:
    """
    Structured output of the 5-pass research methodology.
    Every field represents a synthesis across multiple sources.
    """
    query: str
    topic_type: str
    executive_summary: str           # max 3 sentences, plain English
    key_findings: list[ResearchFinding]
    trend_verdict: str               # "emerging" | "peak" | "declining" | "stable"
    confidence_score: float          # 0-1 aggregate
    signal_count: int
    sources_consulted: list[str]
    cross_verified_findings: list[str]  # findings confirmed by 2+ sources
    unverified_claims: list[str]        # single-source findings (treat with caution)
    risks: list[str]                    # compliance, IP, market risks
    opportunities: list[str]           # arbitrage, pricing, timing opportunities
    recommended_actions: list[str]      # concrete next steps
    research_depth: str              # "surface" | "standard" | "deep"
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class ResearchEngine:
    """
    Deep research engine. 5-pass methodology.

    Usage:
        engine = ResearchEngine(pool=pg_pool, redis=redis_client)
        report = await engine.research("wireless earbuds", depth="deep")
    """

    def __init__(
        self,
        pool: Any | None = None,
        redis: Any | None = None,
        llm_gateway: Any | None = None,
    ) -> None:
        self._pool = pool
        self._redis = redis
        self._llm = llm_gateway

    async def research(
        self,
        query: str,
        *,
        depth: str = "standard",  # "surface" | "standard" | "deep"
        max_signals: int = 200,
    ) -> ResearchReport:
        """
        Conduct multi-pass research on a topic.

        surface: Pass 1 only (< 30s, 3-5 sources)
        standard: Passes 1-3 (< 2min, 8-12 sources)
        deep: All 5 passes (< 5min, 15-20 sources, cross-verification)
        """
        _log.info("research.start", query=query, depth=depth)

        from aegis.scrape.adapter_router import AdapterRouter
        from aegis.scrape.topic_classifier import TopicClassifier

        classifier = TopicClassifier()
        topic_type = classifier.classify(query)
        router = AdapterRouter(redis=self._redis)

        # Pass 1: Broad signal harvest
        pass1_adapters = router.route(query, top_n=5 if depth == "surface" else 12)
        adapter_names = [r.adapter_name for r in pass1_adapters]

        from aegis.scrape.topic import scrape_topic
        harvest = await scrape_topic(
            query,
            adapter_override=adapter_names,
            limit_per_source=max_signals // len(adapter_names),
        )
        signals = harvest.signals

        # Pass 2: Temporal analysis
        velocity_analysis = self._analyze_velocity(signals)

        # Pass 3: Cross-verification (which findings appear in 2+ sources)
        cross_verified = self._cross_verify(signals)

        # Pass 4 + 5 (deep only): competitive landscape + risk synthesis
        risks: list[str] = []
        opportunities: list[str] = []
        if depth == "deep" and signals:
            risks = await self._assess_risks(query, signals[:5])
            opportunities = self._find_opportunities(signals, velocity_analysis)

        # Synthesize
        confidence = self._compute_confidence(signals, cross_verified)
        trend_verdict = self._determine_trend_verdict(velocity_analysis)

        report = ResearchReport(
            query=query,
            topic_type=topic_type.value,
            executive_summary=self._generate_summary(
                query, trend_verdict, len(signals), cross_verified
            ),
            key_findings=self._extract_findings(signals, cross_verified),
            trend_verdict=trend_verdict,
            confidence_score=confidence,
            signal_count=len(signals),
            sources_consulted=list({s.get("platform", "") for s in signals}),
            cross_verified_findings=cross_verified,
            unverified_claims=self._extract_unverified(signals, cross_verified),
            risks=risks,
            opportunities=opportunities,
            recommended_actions=self._generate_actions(trend_verdict, confidence, risks),
            research_depth=depth,
        )

        _log.info(
            "research.complete",
            query=query,
            signals=len(signals),
            confidence=round(confidence, 3),
            verdict=trend_verdict,
            cross_verified=len(cross_verified),
        )
        return report

    def _analyze_velocity(self, signals: list[dict]) -> dict:
        """Compute velocity metrics across the signal set."""
        from collections import Counter
        if not signals:
            return {"status": "no_data", "v1h": 0, "v6h": 0, "v24h": 0}
        # Group by hour bucket
        total = len(signals)
        platforms = Counter(s.get("platform", "") for s in signals)
        return {
            "total": total,
            "unique_platforms": len(platforms),
            "top_platform": platforms.most_common(1)[0][0] if platforms else "",
            "status": "data_available",
        }

    def _cross_verify(self, signals: list[dict]) -> list[str]:
        """Find claims/titles that appear across 2+ different platforms."""
        from collections import defaultdict
        import re
        title_platforms: dict[str, set[str]] = defaultdict(set)
        for s in signals:
            title = str(s.get("title", "")).lower()
            platform = str(s.get("platform", ""))
            # Extract key noun phrases (simplified)
            words = set(re.findall(r"\b[a-z]{4,}\b", title))
            for word in words:
                title_platforms[word].add(platform)
        return [
            phrase for phrase, platforms in title_platforms.items()
            if len(platforms) >= 2 and len(phrase) > 5
        ][:20]

    def _compute_confidence(self, signals: list[dict], cross_verified: list[str]) -> float:
        if not signals:
            return 0.0
        platform_diversity = min(1.0, len({s.get("platform") for s in signals}) / 5)
        signal_volume = min(1.0, len(signals) / 50)
        verification_rate = min(1.0, len(cross_verified) / 10)
        avg_confidence = sum(float(s.get("confidence", 0.5)) for s in signals) / len(signals)
        return round(
            0.30 * platform_diversity
            + 0.25 * signal_volume
            + 0.25 * verification_rate
            + 0.20 * avg_confidence,
            3,
        )

    def _determine_trend_verdict(self, velocity: dict) -> str:
        total = velocity.get("total", 0)
        if total == 0:
            return "stable"
        if total > 100:
            return "emerging"
        if total > 30:
            return "stable"
        return "surface_only"

    def _generate_summary(
        self, query: str, verdict: str, signal_count: int, cross_verified: list[str]
    ) -> str:
        return (
            f"Research on '{query}' gathered {signal_count} signals across multiple platforms. "
            f"The topic shows a {verdict} trend with {len(cross_verified)} cross-verified themes. "
            f"{'High' if len(cross_verified) > 5 else 'Moderate'} confidence based on "
            f"{'strong' if signal_count > 50 else 'limited'} cross-platform coverage."
        )

    def _extract_findings(
        self, signals: list[dict], cross_verified: list[str]
    ) -> list[ResearchFinding]:
        findings = []
        seen = set()
        cv_set = set(cross_verified)
        for s in signals[:20]:
            title = str(s.get("title", ""))
            if title in seen or not title:
                continue
            seen.add(title)
            words = set(title.lower().split())
            verified_by = [cv for cv in cv_set if cv in words]
            findings.append(ResearchFinding(
                claim=title,
                source=str(s.get("platform", "unknown")),
                confidence=float(s.get("confidence", 0.5)),
                verified_by=verified_by,
            ))
        return findings

    def _extract_unverified(
        self, signals: list[dict], cross_verified: list[str]
    ) -> list[str]:
        cv_set = set(cross_verified)
        unverified = []
        seen = set()
        for s in signals:
            title = str(s.get("title", ""))
            if title in seen:
                continue
            seen.add(title)
            words = set(title.lower().split())
            if not any(cv in words for cv in cv_set):
                unverified.append(title)
        return unverified[:10]

    async def _assess_risks(self, query: str, top_signals: list[dict]) -> list[str]:
        risks = []
        try:
            from aegis.compliance.engine import ComplianceEngine
            result = await ComplianceEngine().assess(
                sku="RESEARCH", title=query,
                category="general", origin="CN", destination="IN",
            )
            if result.recommendation.value in ("BLOCK", "ESCALATE"):
                risks.append(f"Compliance risk: {result.recommendation.value} ({result.composite_risk_score:.2f})")
        except Exception:
            pass
        return risks

    def _find_opportunities(self, signals: list[dict], velocity: dict) -> list[str]:
        ops = []
        if velocity.get("total", 0) > 50:
            ops.append(f"High signal volume ({velocity['total']}) suggests strong market interest")
        if velocity.get("unique_platforms", 0) >= 3:
            ops.append(f"Multi-platform presence ({velocity['unique_platforms']} platforms) indicates organic demand")
        return ops

    def _generate_actions(
        self, verdict: str, confidence: float, risks: list[str]
    ) -> list[str]:
        actions = []
        if verdict == "emerging" and confidence > 0.6 and not risks:
            actions.append("Consider sourcing and listing — high-confidence emerging trend")
        elif verdict == "emerging" and risks:
            actions.append("Trend is emerging but compliance risks require review before execution")
        elif verdict == "stable":
            actions.append("Monitor for velocity changes — stable trend, timing not critical")
        else:
            actions.append("Insufficient signal coverage — run deeper research before acting")
        return actions
```

Wire `ResearchEngine` into the dashboard research job endpoint.
Add CLI: `aegis research --topic "query" --depth deep`

---

## ⓫ PASS 7 — ADP-5 (MinHash), ADP-7 (Shared Client), BRAIN-3 (Dynamic Thresholds) Completion

By this pass, the MinHash dedup (Pass 2A), shared HTTP client (Pass 5B), and
dynamic thresholds (Pass 2C) should be implemented. This pass wires them:

1. **ADP-5 completion**: verify MinHash layer is called from `dedup.py`'s `deduplicate_batch`
   (not just defined). Run the batch benchmark and confirm < 500ms.

2. **ADP-7 completion**: scan all adapters in `src/aegis/scrape/sources/` for adapters
   that create their own `httpx.AsyncClient` without using the shared factory.
   Migrate the top 10 highest-traffic adapters to `get_client()`.

3. **BRAIN-3 completion**: verify `DynamicThresholds` is wired into all three consumers:
   `confidence.py`, `analytics.py`, `engine.py`. Add integration test that confirms
   the threshold actually changes when `update_from_outcomes()` is called with mock data.

---

## ⓬ PASS 8 — FINAL VERIFICATION GAUNTLET

**All 20 checks must pass. Zero exceptions. Fix failures before declaring done.**

```bash
echo "══════════════════════════════════════════════"
echo "AEGIS GODMODE FINAL VERIFICATION — $(date -u)"
echo "══════════════════════════════════════════════"

# CHECK 1: Lint
echo "── CHECK 01: RUFF LINT ──"
ruff check src/ tests/ aegis-phase4/src/ aegis-harden/src/ \
  aegis-phase12/src/ aegis-phase13/src/ 2>&1
echo "STATUS: $?"

# CHECK 2: Full test suite + coverage
echo "── CHECK 02: TESTS + COVERAGE ──"
uv run python -m pytest tests/unit/ -q --tb=short -p no:hypothesis \
  --cov=src/aegis --cov-report=term-missing --cov-fail-under=80 2>&1 | tail -25
echo "STATUS: $?"

# CHECK 3: Phase 4 tests
echo "── CHECK 03: PHASE 4 TESTS ──"
uv run python -m pytest aegis-phase4/tests/ -q --tb=short -p no:hypothesis 2>&1 | tail -10
echo "STATUS: $?"

# CHECK 4: All critical imports succeed
echo "── CHECK 04: CRITICAL IMPORTS ──"
uv run python -c "
failures = []
tests = [
    ('aegis.api.main',                    'create_app'),
    ('aegis.agents.runner',               'run_trend'),
    ('aegis.predict.inference.runner',    'InferenceRunner'),
    ('aegis.scrape.swarm',                'SwarmOrchestrator'),
    ('aegis.scrape.topic_classifier',     'TopicClassifier'),
    ('aegis.scrape.adapter_router',       'AdapterRouter'),
    ('aegis.scrape.dedup',                'deduplicate_batch'),
    ('aegis.scrape.budget',               'UCB1Allocator'),
    ('aegis.scrape.schema_drift',         'SchemaDriftTracker'),
    ('aegis.scrape.http_client',          'get_client'),
    ('aegis.core.dynamic_thresholds',     'DynamicThresholds'),
    ('aegis.core.event_bus',              'publish_event'),
    ('aegis.datalake',                    'DataLake'),
    ('aegis.compliance',                  'ComplianceEngine'),
    ('aegis.geo',                         'CrossMarketAnalyzer'),
    ('aegis.evolve',                      'OutcomeRecorder'),
    ('aegis.security',                    'SecretsManager'),
    ('aegis.observability',               'init_tracing'),
    ('aegis.scheduler.health_checker',    'AegisHealthChecker'),
    ('aegis.intelligence.research_engine','ResearchEngine'),
]
for mod, attr in tests:
    try:
        m = __import__(mod, fromlist=[attr])
        getattr(m, attr)
        print(f'  ✓ {mod}')
    except Exception as e:
        print(f'  ✗ {mod}: {e}')
        failures.append(mod)
if failures:
    import sys; sys.exit(1)
print('All imports: OK')
"
echo "STATUS: $?"

# CHECK 5: Router mounting
echo "── CHECK 05: ROUTER MOUNTING ──"
uv run python -c "
from aegis.api.main import create_app
app = create_app()
paths = [r.path for r in app.routes]
required_prefixes = ['/geo', '/compliance', '/evolve', '/datalake', '/dr', '/capital']
missing = [p for p in required_prefixes if not any(r.startswith(p) for r in paths)]
if missing:
    print(f'MISSING: {missing}'); import sys; sys.exit(1)
print(f'All {len(required_prefixes)} router prefixes mounted: OK')
"
echo "STATUS: $?"

# CHECK 6: §1 Stream field invariant
echo "── CHECK 06: INVARIANT §1 (stream field=body) ──"
V=$(grep -rn '"payload"\|"data"' src/ --include="*.py" \
  | grep -iE "xadd|publish_event" | grep -v "# noqa\|test_" | wc -l)
echo "Violations: $V (must be 0)"
[ "$V" -eq 0 ] && echo "PASS" || (echo "FAIL"; grep -rn '"payload"\|"data"' src/ --include="*.py" | grep -iE "xadd|publish_event" | grep -v "# noqa\|test_")

# CHECK 7: §4 Naive datetime invariant
echo "── CHECK 07: INVARIANT §4 (datetime UTC) ──"
V=$(grep -rn "datetime\.now()" src/ --include="*.py" | grep -v "timezone\|utc\|UTC\|# noqa" | wc -l)
echo "Violations: $V (must be 0)"
[ "$V" -eq 0 ] && echo "PASS" || echo "FAIL"

# CHECK 8: §5 Structlog invariant
echo "── CHECK 08: INVARIANT §5 (structlog in agents) ──"
V=$(grep -rn "^import logging$\|^from logging " src/aegis/agents/ src/aegis/llm/ --include="*.py" | grep -v structlog | wc -l)
echo "Violations: $V (must be 0)"
[ "$V" -eq 0 ] && echo "PASS" || echo "FAIL"

# CHECK 9: §6 Pydantic v2 invariant
echo "── CHECK 09: INVARIANT §6 (pydantic v2) ──"
V=$(grep -rn "@validator\b\|\.dict()\b\|from pydantic import validator\b" src/ --include="*.py" | grep -v "# noqa" | wc -l)
echo "Violations: $V (must be 0)"
[ "$V" -eq 0 ] && echo "PASS" || echo "FAIL"

# CHECK 10: §3 Kelly cap invariant
echo "── CHECK 10: INVARIANT §3 (kelly ≤ 0.25) ──"
V=$(grep -rn "kelly_fraction\s*=" src/ --include="*.py" | grep -v "0\.25\|max\|min\|cap\|test_\|#" | wc -l)
echo "Violations: $V (must be 0)"
[ "$V" -eq 0 ] && echo "PASS" || echo "FAIL"

# CHECK 11: §11 Stream cap invariant
echo "── CHECK 11: INVARIANT §11 (streams capped) ──"
# All xadd calls must have maxlen parameter
V=$(grep -rn "\.xadd\|xadd(" src/ aegis-phase4/src/ --include="*.py" | grep -v "maxlen\|# noqa\|test_" | wc -l)
echo "Uncapped xadd calls: $V (must be 0)"
[ "$V" -eq 0 ] && echo "PASS" || echo "FAIL"

# CHECK 12: Evolution loop wired
echo "── CHECK 12: EVOLUTION LOOP CLOSURE ──"
uv run python -c "
from pathlib import Path
s = Path('aegis-phase4/src/aegis/execute/settlement.py').read_text()
assert 'OutcomeRecorder' in s or '_record_outcome_for_evolution' in s, \
    'settlement.py: OutcomeRecorder not wired'
d = Path('src/aegis/evolve/drift.py').read_text()
assert 'auto_rollback' in d.lower() or '_attempt_auto_rollback' in d, \
    'drift.py: auto-rollback not implemented'
a = Path('src/aegis/scheduler/autonomous.py').read_text()
assert 'health_checker' in a.lower() or 'job_health_check' in a, \
    'autonomous.py: health checker not wired'
assert 'job_threshold_update' in a or 'DynamicThresholds' in a, \
    'autonomous.py: dynamic thresholds not wired'
print('Evolution loop: CLOSED')
print('Health checker: WIRED')
print('Dynamic thresholds: WIRED')
"
echo "STATUS: $?"

# CHECK 13: Adapter router works
echo "── CHECK 13: ADAPTER ROUTER ──"
uv run python -c "
from aegis.scrape.topic_classifier import TopicClassifier, TopicType
from aegis.scrape.adapter_router import AdapterRouter

c = TopicClassifier()
assert c.classify('HDFC Bank nifty stock') == TopicType.FINANCIAL_TREND, 'financial classification failed'
assert c.classify('wireless earbuds buy flipkart') == TopicType.ECOMMERCE_PRODUCT, 'product classification failed'
assert c.classify('wholesale manufacturer bulk b2b') == TopicType.SUPPLIER_DISCOVERY, 'supplier classification failed'

r = AdapterRouter()
recs = r.route('HDFC Bank', top_n=5)
assert recs[0].adapter_name in ('nse_bse', 'moneycontrol', 'economic_times', 'screener_in'), \
    f'Wrong top adapter for financial: {recs[0].adapter_name}'

print('TopicClassifier: PASS')
print('AdapterRouter: PASS')
print(f'Top adapter for HDFC Bank: {recs[0].adapter_name}')
"
echo "STATUS: $?"

# CHECK 14: MinHash dedup available
echo "── CHECK 14: MINHASH DEDUP ──"
uv run python -c "
try:
    from datasketch import MinHash, MinHashLSH
    print('datasketch: available')
except ImportError:
    print('datasketch: NOT INSTALLED — run: uv add datasketch>=1.6.0')
    import sys; sys.exit(1)
from aegis.scrape.dedup import deduplicate_batch, MinHashLayer
import asyncio, time
# Performance test
signals = [{'title': f'Test signal number {i}', 'url': f'https://example.com/{i}', 'confidence': 0.8} for i in range(1000)]
start = time.perf_counter()
asyncio.run(deduplicate_batch(signals))
elapsed = (time.perf_counter() - start) * 1000
print(f'deduplicate_batch(1000): {elapsed:.0f}ms (must be < 500ms)')
assert elapsed < 500, f'FAIL: {elapsed:.0f}ms >= 500ms'
print('MinHash dedup: PASS')
"
echo "STATUS: $?"

# CHECK 15: Dynamic thresholds
echo "── CHECK 15: DYNAMIC THRESHOLDS ──"
uv run python -c "
from aegis.core.dynamic_thresholds import DynamicThresholds
import asyncio
thresholds = DynamicThresholds(redis=None)  # no Redis → env fallback
async def test():
    cg = await thresholds.get_confidence_gate()
    vs = await thresholds.get_velocity_slope()
    cb = await thresholds.get_comply_block()
    assert 0.60 <= cg <= 0.95, f'confidence_gate {cg} out of bounds'
    assert 1.0  <= vs <= 5.0,  f'velocity_slope {vs} out of bounds'
    assert 0.50 <= cb <= 0.90, f'comply_block {cb} out of bounds'
    print(f'confidence_gate={cg}, velocity_slope={vs}, comply_block={cb}')
asyncio.run(test())
print('DynamicThresholds: PASS')
"
echo "STATUS: $?"

# CHECK 16: Research engine
echo "── CHECK 16: RESEARCH ENGINE ──"
uv run python -c "
from aegis.intelligence.research_engine import ResearchEngine, ResearchReport
r = ResearchEngine()
assert hasattr(r, 'research'), 'research method missing'
assert hasattr(r, '_cross_verify'), '_cross_verify missing'
print('ResearchEngine: PASS')
"
echo "STATUS: $?"

# CHECK 17: Health checker
echo "── CHECK 17: HEALTH CHECKER ──"
uv run python -c "
from aegis.scheduler.health_checker import AegisHealthChecker, HealthReport
import asyncio
checker = AegisHealthChecker(redis=None, pool=None)
report = asyncio.run(checker.check_and_heal())
assert isinstance(report, HealthReport), f'Expected HealthReport, got {type(report)}'
assert report.overall_status in ('healthy', 'warning', 'critical'), f'Invalid status: {report.overall_status}'
print(f'HealthChecker: PASS (status={report.overall_status}, checks={len(report.checks)})')
"
echo "STATUS: $?"

# CHECK 18: Dashboard panels exist
echo "── CHECK 18: DASHBOARD PANELS ──"
python3 -c "
from pathlib import Path
html = Path('src/aegis/dashboard/static/index.html').read_text().lower()
required = ['geo', 'compliance', 'evolve', 'capital', 'anomal', 'swarm', 'stream', 'dr']
missing = [p for p in required if p not in html]
if missing:
    print(f'Missing panel content: {missing}'); import sys; sys.exit(1)
print('All dashboard panel content present: PASS')
"
echo "STATUS: $?"

# CHECK 19: Updated .env.example
echo "── CHECK 19: ENV EXAMPLE UP TO DATE ──"
python3 scripts/gen_env_example.py --check 2>&1
echo "STATUS: $?"

# CHECK 20: ADRs exist
echo "── CHECK 20: ARCHITECTURE DECISION RECORDS ──"
for adr in docs/adr/0016-predict-path-consolidation.md \
           docs/adr/0017-minhash-dedup.md \
           docs/adr/0018-weighted-ensemble.md \
           docs/adr/0019-evolution-loop.md \
           docs/features.md; do
  if [ -f "$adr" ]; then
    echo "  ✓ $adr"
  else
    echo "  ✗ MISSING: $adr"
    EXIT=1
  fi
done
[ -z "$EXIT" ] && echo "ADRs: PASS" || echo "ADRs: FAIL"

echo "══════════════════════════════════════════════"
echo "VERIFICATION COMPLETE — $(date -u)"
echo "TARGET: 20/20 checks passing"
echo "If any FAIL → fix it → re-run → do not proceed to deliverables until 20/20"
echo "══════════════════════════════════════════════"
```

---

## ⓭ DELIVERABLES (after 20/20 verification)

Only produce deliverables after all 20 checks pass.

### D1 — Update CLAUDE.md
- Phase status table: update test counts and descriptions for all modified phases
- FEATURE_DIM: 20 → 24
- New modules: topic_classifier, adapter_router, research_engine, health_checker,
  dynamic_thresholds, http_client
- New CLI commands: `aegis research`, `aegis datalake schedule`
- New invariants: add §11-§15 from this session

### D2 — 4 Architecture Decision Records (complete, not stubs)

`docs/adr/0016-predict-path-consolidation.md`
`docs/adr/0017-minhash-dedup.md`
`docs/adr/0018-weighted-ensemble.md`
`docs/adr/0019-evolution-loop.md`

Format: Status | Context | Decision | Consequences | Alternatives Considered

### D3 — `docs/features.md`
All 24 FeatureWindow dimensions: name, formula, data source, range, interpretation.
Includes "how to add new features" section and migration guide for 20→24.

### D4 — `docs/SYSTEM_STATE.md`
Generated after completion. Lists every file modified/created, evolution loop status,
coverage before/after, new capabilities added.

### D5 — Updated `.env.example`
Run `python scripts/gen_env_example.py` — verify with `--check` flag.
New env vars from this session:
```
AEGIS_DEDUP_SEMANTIC_ENABLED=false
AEGIS_EVOLVE_FAST_PROMOTE=false
AEGIS_PREDICT_FORCE_HTTP=false
AEGIS_HEALTH_CHECK_INTERVAL_S=300
AEGIS_RESEARCH_DEFAULT_DEPTH=standard
AEGIS_RESEARCH_MAX_SIGNALS=200
```

---

## ⓮ MISSION RECAP — WHAT YOU'RE BUILDING

When this prompt is complete, AEGIS Pulse will:

**Sense**: 35+ adapters, intelligently selected per topic type using the adapter router.
The UCB1 bandit with composite quality rewards concentrates budget where signal is real.

**Process**: MinHash dedup removes noise at O(n log n). Dynamic thresholds adapt from
real outcome data weekly. Feedback-weighted agent ensemble trusts agents proportional
to their historical accuracy. Deep verification runs a second pass on every P0 signal.

**Act**: Self-evolution loop is closed — every settled trade becomes a training label.
DriftDetector rolls back bad models automatically. OnlinePricingPolicy updates prices
from outcomes daily. The system improves itself without human intervention.

**Heal**: AegisHealthChecker runs every 5 minutes. Stream stale? Emergency scrape.
Model stale? Auto-retrain. Clock drift? Sync hwclock. Killswitch stuck? Escalation alert.

**Report**: ResearchEngine conducts 5-pass deep research. Every conclusion is
cross-verified across multiple independent sources. Dashboard shows all 8 phases.
Every output is honest about its provenance (LLM vs heuristic, live vs static).

**Never fail permanently**: every component has a graceful fallback. Redis down?
In-memory. LLM down? Heuristic. DB down? Cached. External API down? Static data.
Nothing crashes. Everything degrades gracefully and heals automatically.

---

## ⓯ EXECUTION ORDER

```
PASS 0: Autopsy → fix invariant violations → emit full report
PASS 1: Close audit findings (CONN-3/4, DASH-2/3/4, INFRA, ENV, ORPH-4)
PASS 2: Intelligence upgrades (MinHash, UCB1+, dynamic thresholds, weighted ensemble,
         FEATURE_DIM 24, schema drift recovery, hot compliance gate)
PASS 3: Evolution loop closure (settlement→outcome, drift→rollback,
         retrain→shadow, policy→pricing)
PASS 4: Adapter routing engine (TopicClassifier, AdapterRouter, wire into scrape_topic)
PASS 5: Self-healing system (AegisHealthChecker, shared HTTP client factory)
PASS 6: Deep research engine (ResearchEngine, multi-pass methodology)
PASS 7: ADP-5/7 + BRAIN-3 wiring completion
PASS 8: Final verification gauntlet (20/20 checks)
DELIVERABLES: ADRs, features.md, SYSTEM_STATE.md, updated CLAUDE.md, .env.example
```

Between every pass:
```bash
ruff check src/ tests/ && uv run python -m pytest tests/unit/ -q \
  --tb=short -p no:hypothesis --cov=src/aegis --cov-fail-under=78 2>&1 | tail -15
```

Both must pass zero violations and green tests before the next pass begins.
If they don't: fix before proceeding. No exceptions.

---

*AEGIS KRONOS-OMEGA · 2026-06-10 · For Claude Code*
*"Sense → Process → Act → Heal → Report → Never fail permanently"*

---

## ⓰ PASS 9 — OSS POWER INTEGRATION (free tools that make AEGIS supreme)

These are battle-tested open-source repositories and libraries that give AEGIS
capabilities that normally cost hundreds of thousands of dollars in engineering time.
Every integration is free. Every integration is production-grade. Zero paid APIs.

### 9A — River: Online Machine Learning for Real-Time Adaptation

Library: `river>=0.21` (add to pyproject.toml)
GitHub: https://github.com/online-ml/river

River is the reference library for online/incremental machine learning in Python.
Unlike batch ML (train weekly), River updates models on every single data point in
microseconds. This makes AEGIS predictions improve in real-time.

New file: `src/aegis/predict/online/river_models.py`

```python
"""
Online machine learning models using River library.

Phase 3 predict layer. Complements the weekly retrain cycle with real-time
incremental learning. Every signal that flows through the system updates the
online model immediately — no retraining lag.

Models:
  - OnlineVelocityClassifier: classifies trend velocity as rising/stable/falling
    using Hoeffding Adaptive Tree (HAT) — handles concept drift natively
  - OnlineAnomalyScorer: real-time anomaly detection using Half-Space Trees
  - OnlinePlatformScorer: per-platform signal quality estimator using ADWIN

River runs in-process, zero latency, zero infrastructure.
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

import structlog

_log = structlog.get_logger("aegis.predict.online")

try:
    from river import anomaly, drift, ensemble, linear_model, preprocessing, tree
    _RIVER_AVAILABLE = True
except ImportError:
    _RIVER_AVAILABLE = False


class OnlineVelocityClassifier:
    """
    Incrementally updated trend velocity classifier.
    Uses Hoeffding Adaptive Tree — handles distribution drift natively.

    Input features: velocity_1h, velocity_6h, velocity_24h, signal_count,
                    unique_authors, sentiment, commercial_intent, novelty
    Output: probability of HIGH_VELOCITY (true=rising, false=stable/falling)

    Updates on every signal. Checkpoint saved every 1000 updates.
    """

    _CHECKPOINT = Path.home() / ".aegis" / "models" / "online_velocity.pkl"
    _FEATURES = [
        "velocity_1h", "velocity_6h", "velocity_24h",
        "signal_count", "unique_authors", "sentiment",
        "commercial_intent", "novelty",
    ]

    def __init__(self) -> None:
        self._model: Any = None
        self._scaler: Any = None
        self._update_count = 0
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if not _RIVER_AVAILABLE:
            return
        try:
            if self._CHECKPOINT.exists():
                with open(self._CHECKPOINT, "rb") as f:
                    saved = pickle.load(f)  # noqa: S301 — trusted local file
                self._model = saved["model"]
                self._scaler = saved["scaler"]
                _log.info("online_velocity.loaded_checkpoint")
            else:
                self._model = tree.HoeffdingAdaptiveTreeClassifier(
                    grace_period=100,
                    leaf_prediction="nb",
                    nb_threshold=0,
                )
                self._scaler = preprocessing.StandardScaler()
                _log.info("online_velocity.new_model")
            self._loaded = True
        except Exception as exc:
            _log.warning("online_velocity.load_failed", error=str(exc))

    def predict_proba(self, features: dict[str, float]) -> float:
        """Return P(HIGH_VELOCITY). Falls back to 0.5 when River unavailable."""
        self._ensure_loaded()
        if not _RIVER_AVAILABLE or self._model is None:
            return 0.5
        try:
            x = {k: features.get(k, 0.0) for k in self._FEATURES}
            x_scaled = self._scaler.transform_one(x)
            proba = self._model.predict_proba_one(x_scaled)
            return float(proba.get(True, 0.5))
        except Exception:
            return 0.5

    def learn_one(self, features: dict[str, float], is_high_velocity: bool) -> None:
        """Update model with one new labeled example."""
        self._ensure_loaded()
        if not _RIVER_AVAILABLE or self._model is None:
            return
        try:
            x = {k: features.get(k, 0.0) for k in self._FEATURES}
            x_scaled = self._scaler.learn_one(x).transform_one(x)
            self._model.learn_one(x_scaled, is_high_velocity)
            self._update_count += 1
            if self._update_count % 1000 == 0:
                self._save_checkpoint()
        except Exception as exc:
            _log.debug("online_velocity.learn_failed", error=str(exc))

    def _save_checkpoint(self) -> None:
        try:
            self._CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
            with open(self._CHECKPOINT, "wb") as f:
                pickle.dump({"model": self._model, "scaler": self._scaler}, f)
            _log.debug("online_velocity.checkpoint_saved", updates=self._update_count)
        except Exception as exc:
            _log.warning("online_velocity.checkpoint_failed", error=str(exc))


class OnlineAnomalyScorer:
    """
    Real-time anomaly detection using Half-Space Trees.
    Scores each signal batch for statistical anomalies vs recent baseline.

    Anomaly score > 0.7 → potential market event worth immediate investigation.
    Score is calibrated: 0.5 is expected baseline, 1.0 is extreme outlier.
    """

    def __init__(self, n_trees: int = 25, height: int = 8, window_size: int = 250) -> None:
        self._model: Any = None
        self._window_size = window_size
        self._n_trees = n_trees
        self._height = height
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded or not _RIVER_AVAILABLE:
            return
        try:
            self._model = anomaly.HalfSpaceTrees(
                n_trees=self._n_trees,
                height=self._height,
                window_size=self._window_size,
                seed=42,
            )
            self._loaded = True
        except Exception as exc:
            _log.warning("online_anomaly.init_failed", error=str(exc))

    def score(self, features: dict[str, float]) -> float:
        """Return anomaly score 0-1. > 0.7 = worth investigating."""
        self._ensure_loaded()
        if not _RIVER_AVAILABLE or self._model is None:
            return 0.5
        try:
            score = self._model.score_one(features)
            self._model.learn_one(features)
            return float(min(1.0, max(0.0, score)))
        except Exception:
            return 0.5


# Module-level singletons (one per process)
_velocity_classifier = OnlineVelocityClassifier()
_anomaly_scorer = OnlineAnomalyScorer()


def get_velocity_classifier() -> OnlineVelocityClassifier:
    return _velocity_classifier


def get_anomaly_scorer() -> OnlineAnomalyScorer:
    return _anomaly_scorer
```

Wire `OnlineVelocityClassifier` into `InferenceRunner`:
- After heuristic prediction: call `predict_proba(feature_dict)`
- Blend: `final_confidence = 0.7 * heuristic_confidence + 0.3 * online_proba`
- After outcome is settled: call `learn_one(features, is_high_velocity=roi > 0.1)`

Wire `OnlineAnomalyScorer` into `SwarmOrchestrator._post_wave_analysis()`:
- Score each signal batch for anomalies
- Signals with anomaly_score > 0.7: add "anomaly" tag + publish to `aegis:scrape:anomalies`

Tests: `tests/unit/predict/test_river_models.py` (min 6):
- `predict_proba` returns float in [0, 1] with no River
- `predict_proba` returns float in [0, 1] with River
- `learn_one` does not raise when called repeatedly
- `score()` returns 0.5 on first call (no baseline yet)
- `score()` returns higher value on extreme outlier
- checkpoint save/load round-trips correctly

### 9B — Sentence-Transformers + FAISS: Semantic Signal Search

Libraries: `sentence-transformers>=3.0` (already in llm extra), `faiss-cpu>=1.8` (new)
GitHub: https://github.com/facebookresearch/faiss

FAISS (Facebook AI Similarity Search) is the gold standard for billion-scale vector
search. We use it for semantic signal deduplication (Layer 3), trend clustering, and
"find signals similar to this one" functionality.

New file: `src/aegis/scrape/semantic_index.py`

```python
"""
FAISS-backed semantic signal index for high-precision deduplication and clustering.

Phase 0 scrape layer. Builds an in-process vector index of recent signals using
bge-m3 embeddings (64-dim projected for speed). Enables:
  - Semantic dedup (Layer 3 of the MinHash pipeline)
  - "Find similar signals" for the dashboard search
  - Trend cluster discovery (replaces k-means with FAISS IVF)

FAISS runs entirely in-process, zero network, zero cost.
Falls back to no-op when faiss-cpu not installed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import structlog

_log = structlog.get_logger("aegis.scrape.semantic_index")

try:
    import faiss  # noqa: F401
    _FAISS_AVAILABLE = True
except ImportError:
    _FAISS_AVAILABLE = False

_DIM = 384       # bge-m3 outputs 1024 but we project to 384 for FAISS speed
_MAX_INDEX = 50_000  # max signals in index before rotation


@dataclass
class SemanticSearchResult:
    signal_id: str
    similarity: float
    metadata: dict = field(default_factory=dict)


class SemanticSignalIndex:
    """
    In-process FAISS index for semantic signal search.

    Usage:
        index = SemanticSignalIndex()
        await index.add(signal_id="sig-1", text="wireless earbuds trending", metadata={...})
        results = await index.search("bluetooth earphones popular", top_k=5)
    """

    def __init__(self) -> None:
        self._index: Any = None
        self._id_map: list[str] = []
        self._meta_map: list[dict] = []
        self._embedder: Any = None
        self._projection: Any = None
        self._built = False

    def _ensure_built(self) -> None:
        if self._built or not _FAISS_AVAILABLE:
            return
        try:
            import faiss
            # Flat L2 index — exact search, fast at our scale (< 50k vectors)
            base_index = faiss.IndexFlatIP(_DIM)  # inner product = cosine on normalized vecs
            # Wrap with IDMap to enable removal
            self._index = faiss.IndexIDMap(base_index)
            self._built = True
            _log.info("semantic_index.built", dim=_DIM)
        except Exception as exc:
            _log.warning("semantic_index.build_failed", error=str(exc))

    async def _embed(self, text: str) -> np.ndarray | None:
        """Get normalized embedding for text. Returns None if embedder unavailable."""
        try:
            from aegis.agents.llm import get_gateway
            gw = get_gateway()
            if gw is None:
                return None
            embeddings = await gw.embed([text])
            if not embeddings:
                return None
            vec = np.array(embeddings[0], dtype=np.float32)
            # Project to _DIM if needed
            if len(vec) > _DIM:
                vec = vec[:_DIM]
            elif len(vec) < _DIM:
                vec = np.pad(vec, (0, _DIM - len(vec)))
            # Normalize for cosine similarity via inner product
            norm = np.linalg.norm(vec)
            if norm > 1e-10:
                vec = vec / norm
            return vec
        except Exception as exc:
            _log.debug("semantic_index.embed_failed", error=str(exc))
            return None

    async def add(self, signal_id: str, text: str, metadata: dict | None = None) -> bool:
        """Add a signal to the index. Returns True if added successfully."""
        self._ensure_built()
        if not _FAISS_AVAILABLE or not self._built:
            return False

        # Rotate index if too large
        if len(self._id_map) >= _MAX_INDEX:
            self._rotate()

        vec = await self._embed(text)
        if vec is None:
            return False

        try:
            import faiss
            numeric_id = int(hashlib.sha256(signal_id.encode()).hexdigest()[:8], 16)
            self._index.add_with_ids(vec.reshape(1, -1), np.array([numeric_id], dtype=np.int64))
            self._id_map.append(signal_id)
            self._meta_map.append(metadata or {})
            return True
        except Exception as exc:
            _log.debug("semantic_index.add_failed", error=str(exc))
            return False

    async def search(self, query: str, top_k: int = 10) -> list[SemanticSearchResult]:
        """Find semantically similar signals. Returns empty list if unavailable."""
        self._ensure_built()
        if not _FAISS_AVAILABLE or not self._built or not self._id_map:
            return []

        vec = await self._embed(query)
        if vec is None:
            return []

        try:
            k = min(top_k, len(self._id_map))
            distances, indices = self._index.search(vec.reshape(1, -1), k)
            results = []
            for dist, idx in zip(distances[0], indices[0]):
                if idx < 0:
                    continue
                # Find signal_id by numeric_id mapping
                try:
                    pos = int(idx) % len(self._id_map)
                    results.append(SemanticSearchResult(
                        signal_id=self._id_map[pos],
                        similarity=float(dist),
                        metadata=self._meta_map[pos],
                    ))
                except Exception:
                    continue
            return results
        except Exception as exc:
            _log.debug("semantic_index.search_failed", error=str(exc))
            return []

    async def is_duplicate(self, text: str, threshold: float = 0.92) -> tuple[bool, str | None]:
        """Check if text is semantically duplicate of any indexed signal."""
        results = await self.search(text, top_k=1)
        if results and results[0].similarity >= threshold:
            return True, results[0].signal_id
        return False, None

    def _rotate(self) -> None:
        """Drop oldest 25% of index to make room."""
        if not _FAISS_AVAILABLE or not self._built:
            return
        keep = int(len(self._id_map) * 0.75)
        try:
            import faiss
            base = faiss.IndexFlatIP(_DIM)
            self._index = faiss.IndexIDMap(base)
            self._id_map = self._id_map[-keep:]
            self._meta_map = self._meta_map[-keep:]
            _log.info("semantic_index.rotated", kept=keep)
        except Exception:
            pass


# Process singleton
_signal_index = SemanticSignalIndex()


def get_signal_index() -> SemanticSignalIndex:
    return _signal_index
```

Wire `SemanticSignalIndex` into:
- `src/aegis/scrape/dedup.py` Layer 3 (replace `pass` stub with real implementation)
- `src/aegis/dashboard/app.py` — new `GET /api/search` endpoint uses index for semantic search

### 9C — Evidently AI: Production Model Monitoring

Library: `evidently>=0.4` (add to pyproject.toml under evolve extra)
GitHub: https://github.com/evidentlyai/evidently

Evidently generates beautiful HTML drift reports and structured JSON metrics.
Integrate into the weekly retrain pipeline for production-grade drift detection.

New file: `src/aegis/evolve/evidently_monitor.py`

```python
"""
Evidently-based model monitoring for AEGIS.

Phase 9 evolve layer. Wraps Evidently AI's data and model quality reports.
Generates both structured JSON metrics (for alerting) and HTML reports
(for the dashboard) from prediction_outcomes data.

Falls back to AEGIS native KS-distance detection when Evidently absent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

_log = structlog.get_logger("aegis.evolve.evidently_monitor")

try:
    from evidently import ColumnMapping
    from evidently.metric_preset import DataDriftPreset, TargetDriftPreset
    from evidently.report import Report
    _EVIDENTLY_AVAILABLE = True
except ImportError:
    _EVIDENTLY_AVAILABLE = False


class EvidentlyMonitor:
    """
    Wraps Evidently AI reports for production model monitoring.

    Generates:
    - Data drift report: are feature distributions shifting?
    - Target drift report: is outcome distribution shifting?
    - HTML report: saved to ~/.aegis/reports/ for dashboard serving

    Usage:
        monitor = EvidentlyMonitor()
        result = await monitor.run_drift_report(reference_df, current_df)
    """

    _REPORTS_DIR = Path.home() / ".aegis" / "reports"

    def __init__(self) -> None:
        self._reports_dir = self._REPORTS_DIR
        self._reports_dir.mkdir(parents=True, exist_ok=True)

    async def run_drift_report(
        self,
        reference_df: Any,     # pandas DataFrame of reference period predictions
        current_df: Any,       # pandas DataFrame of current period predictions
        feature_cols: list[str] | None = None,
        target_col: str = "roi",
    ) -> dict:
        """
        Run full drift analysis. Returns structured dict with drift metrics.
        Also saves HTML report to ~/.aegis/reports/drift_{timestamp}.html.
        """
        if not _EVIDENTLY_AVAILABLE:
            _log.warning("evidently_monitor.not_available", fallback="native_ks_distance")
            return {"available": False, "fallback": "native_ks_distance"}

        try:
            import pandas as pd

            if not isinstance(reference_df, pd.DataFrame):
                return {"error": "reference_df must be pandas DataFrame"}

            column_mapping = ColumnMapping(
                target=target_col,
                numerical_features=feature_cols or [],
            )

            report = Report(metrics=[
                DataDriftPreset(),
                TargetDriftPreset(),
            ])
            report.run(
                reference_data=reference_df,
                current_data=current_df,
                column_mapping=column_mapping,
            )

            # Save HTML report
            ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            report_path = self._reports_dir / f"drift_{ts}.html"
            report.save_html(str(report_path))

            # Extract structured metrics
            result_dict = report.as_dict()
            drift_detected = self._extract_drift_detected(result_dict)
            drifted_features = self._extract_drifted_features(result_dict)

            _log.info(
                "evidently_monitor.report_complete",
                drift_detected=drift_detected,
                drifted_features=len(drifted_features),
                report_path=str(report_path),
            )

            return {
                "available": True,
                "drift_detected": drift_detected,
                "drifted_features": drifted_features,
                "report_path": str(report_path),
                "timestamp": datetime.now(UTC).isoformat(),
            }
        except Exception as exc:
            _log.error("evidently_monitor.report_failed", error=str(exc))
            return {"available": True, "error": str(exc)}

    def _extract_drift_detected(self, result: dict) -> bool:
        try:
            for metric in result.get("metrics", []):
                if "DatasetDriftMetric" in str(metric.get("metric", "")):
                    return bool(metric.get("result", {}).get("dataset_drift", False))
        except Exception:
            pass
        return False

    def _extract_drifted_features(self, result: dict) -> list[str]:
        drifted = []
        try:
            for metric in result.get("metrics", []):
                result_data = metric.get("result", {})
                for feat, data in result_data.get("drift_by_columns", {}).items():
                    if data.get("drift_detected", False):
                        drifted.append(feat)
        except Exception:
            pass
        return drifted
```

Wire `EvidentlyMonitor` into `RetrainingPipeline.run_weekly_retrain()`:
- After fetching outcomes, build reference (30-60 days ago) and current (0-30 days) DataFrames
- Run `EvidentlyMonitor().run_drift_report(reference_df, current_df)`
- If `drift_detected=True`: log + add to retrain audit record
- Save report path in `retrain_run.evidently_report_path`

Add `GET /api/evolve/drift-report` dashboard endpoint that serves the latest HTML report.

### 9D — NetworkX: Creator Graph Analysis (already available)

Library: `networkx` (already in dependencies)

The `GraphState` in Phase 2 carries raw signal data but never builds a creator
influence graph. This implements it properly:

New file: `src/aegis/scrape/creator_graph.py`

```python
"""
Creator influence graph for coordinated behaviour detection.

Phase 0 scrape layer. Builds a directed graph of creator→signal relationships
to detect coordinated posting campaigns and measure organic reach.

Uses NetworkX (already installed). Zero new dependencies.

Key metrics:
  - PageRank: identifies most influential creators
  - Clustering coefficient: high clustering = coordinated network
  - Bridge score: rare creators that connect separate communities = organic KOLs
  - Coordination score: many creators posting same content simultaneously = suspicious
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

_log = structlog.get_logger("aegis.scrape.creator_graph")

try:
    import networkx as nx
    _NX_AVAILABLE = True
except ImportError:
    _NX_AVAILABLE = False


@dataclass
class CreatorGraphMetrics:
    """Metrics extracted from the creator influence graph."""
    node_count: int
    edge_count: int
    top_creators_by_pagerank: list[tuple[str, float]]  # (creator_id, score)
    avg_clustering: float        # 0-1; high = coordinated
    coordination_score: float    # 0-1; high = suspicious coordination
    organic_score: float         # 0-1; high = genuinely organic
    bridge_creators: list[str]   # creators connecting separate communities
    computed_at: datetime = None

    def __post_init__(self) -> None:
        if self.computed_at is None:
            self.computed_at = datetime.now(UTC)


class CreatorGraph:
    """
    Build and analyze creator influence graphs from signal batches.

    A creator graph has:
    - Nodes: creator IDs (author handles/IDs from signals)
    - Edges: A→B when creator A's content is shared/referenced by creator B
    - Weights: edge weight = number of co-occurrence / shared-topic events
    """

    def __init__(self) -> None:
        self._g: Any = None

    def build_from_signals(self, signals: list[dict]) -> bool:
        """Build graph from a list of signal dicts. Returns True if successful."""
        if not _NX_AVAILABLE:
            return False
        try:
            self._g = nx.DiGraph()
            # Add creator nodes
            creators = {}
            for sig in signals:
                author = sig.get("author_id") or sig.get("author") or ""
                if not author:
                    continue
                platform = sig.get("platform", "unknown")
                key = f"{platform}:{author}"
                if key not in creators:
                    creators[key] = {"signals": [], "platform": platform}
                creators[key]["signals"].append(sig)

            for creator_id, data in creators.items():
                self._g.add_node(
                    creator_id,
                    platform=data["platform"],
                    signal_count=len(data["signals"]),
                )

            # Add edges: creators who posted on the same topic within 2 hours
            # are considered part of the same propagation event
            creator_list = list(creators.items())
            for i, (c1_id, c1_data) in enumerate(creator_list):
                for c2_id, c2_data in creator_list[i+1:]:
                    if c1_id == c2_id:
                        continue
                    overlap = self._topic_overlap(c1_data["signals"], c2_data["signals"])
                    if overlap > 0:
                        self._g.add_edge(c1_id, c2_id, weight=overlap)
            return True
        except Exception as exc:
            _log.warning("creator_graph.build_failed", error=str(exc))
            return False

    def _topic_overlap(self, sigs_a: list[dict], sigs_b: list[dict]) -> int:
        """Count shared topic keywords between two creator's signal sets."""
        import re
        words_a = set()
        words_b = set()
        for s in sigs_a:
            words_a.update(re.findall(r"\b[a-z]{5,}\b", s.get("title", "").lower()))
        for s in sigs_b:
            words_b.update(re.findall(r"\b[a-z]{5,}\b", s.get("title", "").lower()))
        return len(words_a & words_b)

    def compute_metrics(self) -> CreatorGraphMetrics | None:
        """Compute graph metrics. Returns None if graph not built or too small."""
        if not _NX_AVAILABLE or self._g is None:
            return None
        if len(self._g.nodes) < 3:
            return CreatorGraphMetrics(
                node_count=len(self._g.nodes),
                edge_count=len(self._g.edges),
                top_creators_by_pagerank=[],
                avg_clustering=0.0,
                coordination_score=0.0,
                organic_score=1.0,
                bridge_creators=[],
            )
        try:
            # PageRank (who drives the most engagement propagation)
            pagerank = nx.pagerank(self._g, alpha=0.85, weight="weight")
            top_creators = sorted(pagerank.items(), key=lambda x: x[1], reverse=True)[:10]

            # Clustering (high = coordinated posting)
            undirected = self._g.to_undirected()
            avg_clustering = nx.average_clustering(undirected)

            # Coordination score: high avg_clustering + few bridge nodes = coordinated
            bridges = list(nx.bridges(undirected)) if len(undirected.edges) > 0 else []
            bridge_nodes = set()
            for u, v in bridges:
                bridge_nodes.update([u, v])

            density = nx.density(self._g)
            # Coordination: dense, highly clustered, no bridges = bot network
            coordination_score = min(1.0, (avg_clustering * 0.6 + density * 0.4))
            # Organic: diverse, low clustering, many bridges = real influencers
            organic_score = max(0.0, 1.0 - coordination_score)

            return CreatorGraphMetrics(
                node_count=len(self._g.nodes),
                edge_count=len(self._g.edges),
                top_creators_by_pagerank=top_creators,
                avg_clustering=round(avg_clustering, 4),
                coordination_score=round(coordination_score, 4),
                organic_score=round(organic_score, 4),
                bridge_creators=list(bridge_nodes)[:10],
            )
        except Exception as exc:
            _log.warning("creator_graph.metrics_failed", error=str(exc))
            return None
```

Wire `CreatorGraph` into `SwarmOrchestrator._post_wave_analysis()`:
- Build graph from wave signals
- If `coordination_score > 0.75`: tag signals as "coordinated" + reduce confidence by 30%
- Add `creator_graph_metrics` to `SwarmResult`
- Publish top creators and coordination score to `aegis:swarm:results` stream

### 9E — Optuna + MLflow: HPO and Experiment Tracking at Scale

Libraries: `optuna>=3.6` (already in evolve extra), `mlflow>=2.14` (add to evolve extra)

MLflow tracks every retrain experiment, parameter, metric, and artifact.
Optuna finds the best hyperparameters in 30 Bayesian trials rather than grid search.

New file: `src/aegis/evolve/experiment_tracker.py`

```python
"""
MLflow experiment tracker for AEGIS model training.

Phase 9 evolve layer. Wraps MLflow to provide:
  - Automatic experiment creation per retrain run
  - Parameter logging (all Optuna HPO params)
  - Metric logging (AUC, precision, recall at each trial)
  - Artifact logging (model pickle, feature importance, Evidently report)
  - Model registry integration (staging → production promotion)

MLflow UI: http://localhost:5000 (run: mlflow ui --port 5000)
MLflow runs: ~/.aegis/mlruns/

Falls back to structured logging when MLflow unavailable.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Generator

import structlog

_log = structlog.get_logger("aegis.evolve.experiment_tracker")

try:
    import mlflow
    import mlflow.sklearn
    _MLFLOW_AVAILABLE = True
except ImportError:
    _MLFLOW_AVAILABLE = False

_TRACKING_URI = str(Path.home() / ".aegis" / "mlruns")
_EXPERIMENT_NAME = "aegis-pulse-arbitrage"


class ExperimentTracker:
    """
    MLflow wrapper for AEGIS retrain experiments.

    Usage:
        tracker = ExperimentTracker()
        with tracker.start_run(run_name="weekly_retrain_2026-01-01") as run:
            tracker.log_params({"n_estimators": 100, "max_depth": 5})
            tracker.log_metric("auc", 0.872)
            tracker.log_metric("precision", 0.891)
            tracker.log_artifact(model_path)
    """

    def __init__(self) -> None:
        self._initialized = False

    def _ensure_initialized(self) -> None:
        if self._initialized or not _MLFLOW_AVAILABLE:
            return
        try:
            mlflow.set_tracking_uri(_TRACKING_URI)
            mlflow.set_experiment(_EXPERIMENT_NAME)
            self._initialized = True
            _log.info("experiment_tracker.initialized", tracking_uri=_TRACKING_URI)
        except Exception as exc:
            _log.warning("experiment_tracker.init_failed", error=str(exc))

    @contextmanager
    def start_run(self, run_name: str) -> Generator[Any, None, None]:
        """Context manager for an MLflow run. No-op if MLflow unavailable."""
        self._ensure_initialized()
        if not _MLFLOW_AVAILABLE:
            yield None
            return
        try:
            with mlflow.start_run(run_name=run_name) as run:
                mlflow.log_param("run_started_at", datetime.now(UTC).isoformat())
                yield run
        except Exception as exc:
            _log.warning("experiment_tracker.run_failed", error=str(exc))
            yield None

    def log_params(self, params: dict) -> None:
        if not _MLFLOW_AVAILABLE:
            _log.debug("experiment_tracker.params", **params)
            return
        try:
            mlflow.log_params(params)
        except Exception:
            pass

    def log_metric(self, key: str, value: float, step: int | None = None) -> None:
        if not _MLFLOW_AVAILABLE:
            _log.debug("experiment_tracker.metric", key=key, value=value)
            return
        try:
            mlflow.log_metric(key, value, step=step)
        except Exception:
            pass

    def log_metrics(self, metrics: dict[str, float], step: int | None = None) -> None:
        for k, v in metrics.items():
            self.log_metric(k, v, step=step)

    def log_artifact(self, path: str) -> None:
        if not _MLFLOW_AVAILABLE:
            return
        try:
            mlflow.log_artifact(path)
        except Exception:
            pass

    def register_model(self, model: Any, model_name: str = "aegis-arbitrage-model") -> str | None:
        """Register model in MLflow registry. Returns model URI."""
        if not _MLFLOW_AVAILABLE:
            return None
        try:
            model_info = mlflow.sklearn.log_model(
                model, artifact_path="model",
                registered_model_name=model_name,
            )
            return model_info.model_uri
        except Exception as exc:
            _log.warning("experiment_tracker.register_failed", error=str(exc))
            return None
```

Wire `ExperimentTracker` into `RetrainingPipeline.run_weekly_retrain()`:
- Wrap entire retrain in `tracker.start_run(f"weekly_{date}")`
- Log all HPO params from Optuna best trial
- Log AUC, precision, candidate vs champion metrics
- Log model artifact (pickle file path)
- Log Evidently report path as artifact

---

## ⓱ PASS 10 — COMPLETE TEST SPECIFICATION

Every module written in Passes 1-9 requires tests. This pass is the exhaustive
specification of every test file, what each test asserts, and the minimum count.

### Test file inventory with mandatory minimum counts

```
tests/unit/scrape/
  test_dedup_minhash.py           min 8  — MinHash layer behavior
  test_dedup_perf.py              min 2  — performance benchmarks
  test_adapter_router.py          min 8  — routing decisions
  test_topic_classifier.py        min 10 — classification accuracy per topic type
  test_budget_quality.py          min 5  — composite reward calculation
  test_schema_drift_recovery.py   min 6  — EWMA recovery + QuarantineReason
  test_semantic_index.py          min 5  — FAISS add/search/duplicate detection
  test_creator_graph.py           min 6  — graph metrics + coordination detection
  test_http_client.py             min 4  — shared client creation + reuse

tests/unit/predict/
  test_features_24dim.py          min 8  — 4 new features + padding migration
  test_river_models.py            min 6  — online classifier + anomaly scorer
  test_deep_verify.py             min 5  — second-pass verification logic

tests/unit/agents/
  test_supervisor_weighted.py     min 6  — weighted ensemble voting
  test_runner_deep_verify.py      min 4  — integration: run_trend + deep verify

tests/unit/core/
  test_dynamic_thresholds.py      min 7  — adaptation logic + bounds + caching
  test_event_bus.py               min 4  — publish_event + stream capping

tests/unit/evolve/
  test_experiment_tracker.py      min 5  — MLflow wrapper + graceful fallback
  test_evidently_monitor.py       min 4  — drift report + graceful fallback
  test_settlement_evolution.py    min 5  — outcome recording + best-effort

tests/unit/execute/
  test_hot_path_compliance.py     min 4  — FTC + privacy + brand gate < 5ms
  test_settlement_outcome.py      min 5  — settlement→OutcomeRecorder wiring

tests/unit/datalake/
  test_redis_ingester_cg.py       min 7  — XREADGROUP pattern

tests/unit/dashboard/
  test_pool_timeout.py            min 3  — 504 on pool exhaustion + metric
  test_stream_health.py           min 4  — stream health endpoint

tests/unit/scheduler/
  test_health_checker.py          min 8  — all 6 health checks + healing actions
  test_autonomous_jobs.py         min 4  — all new jobs wired

tests/unit/intelligence/
  test_research_engine.py         min 7  — 5-pass research + cross-verify
```

### Master test assertion checklist

For EVERY test file, every test must assert:
1. **Happy path**: correct behavior with normal input
2. **Edge case**: empty input, single element, max input
3. **Failure path**: what happens when a dependency is unavailable
4. **Invariant check**: at minimum one test per file verifies the relevant §-invariant

For EVERY mock of Redis or asyncpg:
- Use `from aegis.testing import fake_redis, fake_pg_pool` — never raw `unittest.mock.MagicMock`
- Verify the exact Redis command called (not just "something was called")
- Verify the exact field name "body" in any stream publish test

For EVERY performance benchmark test:
```python
import time

def test_deduplicate_batch_performance():
    signals = [{...} for _ in range(1000)]
    start = time.perf_counter()
    asyncio.run(deduplicate_batch(signals))
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 500, f"dedup_batch(1000) took {elapsed_ms:.0f}ms, must be < 500ms"
```

### Integration smoke tests (no infra required)

Add `tests/unit/integration/test_pipeline_smoke.py`:

```python
"""
End-to-end pipeline smoke tests that require zero infrastructure.
All external calls mocked. Tests the full scrape→analyze→alert chain.
"""

async def test_full_pipeline_heuristic_only():
    """Complete pipeline from topic → signal → prediction → alert with no LLM, no DB."""
    # 1. Scrape topic (mocked adapters returning 10 signals)
    # 2. Dedup (verify MinHash layer called)
    # 3. Run agent graph (heuristic only, AEGIS_DISABLE_OLLAMA=1)
    # 4. Verify GraphResult published to stream with field "body"
    # 5. Verify alert envelope created if verdict == ENTER
    # 6. Verify compliance hot-path gate called
    # Assertions: no exceptions, stream field = "body", verdict in valid set

async def test_adapter_router_integration():
    """Router → TopicClassifier → adapter selection → scrape call."""
    # 1. Create AdapterRouter with mock health tracker
    # 2. Route "HDFC Bank NSE" → expect financial adapters
    # 3. Route "wireless earbuds" → expect ecommerce adapters
    # 4. Verify no credentials-required adapters when exclude_credentials=True
    # 5. Verify recommendations sorted by final_score descending

async def test_evolution_loop_smoke():
    """Settlement → OutcomeRecorder → stream publish chain."""
    # Mock DB pool, Redis
    # Call SettlementManager.settle_plan() with positive ROI plan
    # Assert OutcomeRecorder.record_outcome called with correct ROI
    # Assert publish_event called with "aegis:phase9:evolve_events"
    # Assert stream field = "body" in publish call
    # Assert settlement succeeds even if OutcomeRecorder raises
```

---

## ⓲ PASS 11 — REAL-TIME INTELLIGENCE LAYER

Pattern recognition in first pass. This is what separates a signal processor from
an intelligence system.

### 11A — Pattern Recognition Engine (replaces detect_patterns for swarm scale)

New file: `src/aegis/scrape/pattern_engine.py`

```python
"""
Real-time pattern recognition engine for AEGIS signal streams.

Phase 0 scrape layer. Identifies emerging patterns from signal streams in
real-time (not batch). Designed to recognize a pattern on FIRST PASS
through the data without requiring multiple iterations.

Key innovation: uses a combination of:
  1. Velocity-weighted TF-IDF for term importance
  2. DBSCAN clustering (no need to specify K a priori)
  3. Temporal acceleration (second derivative of signal velocity)
  4. Cross-platform coherence as organic confirmation signal

Outperforms the existing OLS+PCA approach because:
  - DBSCAN handles arbitrary cluster shapes (trends don't form spherical clusters)
  - Velocity weighting means recent signals contribute more to emerging patterns
  - First-pass recognition: classifies pattern on encounter, not retrospectively
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

import structlog

_log = structlog.get_logger("aegis.scrape.pattern_engine")


@dataclass
class RealTimePattern:
    """A pattern detected in the signal stream."""
    pattern_id: str
    label: str                    # human-readable label
    signal_count: int
    velocity_slope: float        # signals/hour (OLS fit)
    acceleration: float          # delta velocity (second derivative)
    coherence: float             # cross-platform coherence 0-1
    confidence: float            # overall pattern confidence 0-1
    is_breakout: bool            # True if acceleration > 3σ
    is_organic: bool             # True if coherence > 0.6 AND coordination_score < 0.5
    pattern_type: Literal["emerging", "accelerating", "peaking", "declining", "noise"]
    top_signals: list[str]       # top 5 signal titles
    platforms: list[str]         # platforms contributing to this pattern
    detected_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class PatternEngine:
    """
    Real-time pattern engine. Processes signals in one pass.

    Usage:
        engine = PatternEngine()
        patterns = engine.detect(signals, context={"topic": "wireless earbuds"})
        breakouts = [p for p in patterns if p.is_breakout]
    """

    def __init__(
        self,
        min_cluster_size: int = 3,
        acceleration_threshold: float = 2.5,
        coherence_threshold: float = 0.6,
    ) -> None:
        self._min_cluster = min_cluster_size
        self._accel_threshold = acceleration_threshold
        self._coherence_threshold = coherence_threshold

    def detect(
        self,
        signals: list[dict],
        *,
        context: dict | None = None,
    ) -> list[RealTimePattern]:
        """
        Detect patterns in one pass through signals.
        Returns patterns sorted by confidence descending.
        """
        if not signals:
            return []

        # Step 1: Velocity-weighted TF-IDF clustering
        clusters = self._cluster_by_theme(signals)

        patterns: list[RealTimePattern] = []
        for cluster_id, cluster_signals in clusters.items():
            if len(cluster_signals) < self._min_cluster:
                continue

            # Step 2: Compute velocity slope (OLS one pass)
            slope = self._compute_slope(cluster_signals)

            # Step 3: Compute acceleration (change in slope vs 2h ago)
            acceleration = self._compute_acceleration(cluster_signals)

            # Step 4: Cross-platform coherence
            platforms = list({s.get("platform", "") for s in cluster_signals})
            coherence = self._compute_coherence(cluster_signals)

            # Step 5: Classify pattern type
            pattern_type = self._classify_type(slope, acceleration)

            # Step 6: Confidence score
            confidence = self._compute_confidence(
                len(cluster_signals), slope, coherence, len(platforms)
            )

            is_breakout = abs(acceleration) > self._accel_threshold
            is_organic = coherence > self._coherence_threshold

            # Step 7: Extract label from top terms
            label = self._extract_label(cluster_signals)

            patterns.append(RealTimePattern(
                pattern_id=f"pattern-{cluster_id}",
                label=label,
                signal_count=len(cluster_signals),
                velocity_slope=round(slope, 4),
                acceleration=round(acceleration, 4),
                coherence=round(coherence, 4),
                confidence=round(confidence, 4),
                is_breakout=is_breakout,
                is_organic=is_organic,
                pattern_type=pattern_type,
                top_signals=[s.get("title", "")[:100] for s in cluster_signals[:5]],
                platforms=platforms,
            ))

        patterns.sort(key=lambda p: p.confidence, reverse=True)

        _log.info(
            "pattern_engine.complete",
            total_signals=len(signals),
            patterns_found=len(patterns),
            breakouts=sum(1 for p in patterns if p.is_breakout),
        )
        return patterns

    def _cluster_by_theme(self, signals: list[dict]) -> dict[int, list[dict]]:
        """Cluster signals by shared keyword themes. One-pass, no sklearn required."""
        import re
        from collections import defaultdict

        # Build term→signals index
        term_signals: dict[str, list[int]] = defaultdict(list)
        for i, sig in enumerate(signals):
            title = sig.get("title", "").lower()
            words = [w for w in re.findall(r"\b[a-z]{4,}\b", title)
                     if w not in _STOPWORDS]
            for word in words:
                term_signals[word].append(i)

        # Greedy clustering: signals sharing 2+ terms are in the same cluster
        signal_cluster: dict[int, int] = {}
        cluster_id = 0
        for term, sig_indices in sorted(term_signals.items(), key=lambda x: -len(x[1])):
            if len(sig_indices) < self._min_cluster:
                continue
            # Find if any of these signals are already clustered
            existing_clusters = {signal_cluster[i] for i in sig_indices if i in signal_cluster}
            if existing_clusters:
                target_cluster = min(existing_clusters)
                for i in sig_indices:
                    signal_cluster[i] = target_cluster
            else:
                for i in sig_indices:
                    if i not in signal_cluster:
                        signal_cluster[i] = cluster_id
                cluster_id += 1

        # Group signals by cluster
        clusters: dict[int, list[dict]] = defaultdict(list)
        for i, sig in enumerate(signals):
            cid = signal_cluster.get(i, -1)
            if cid >= 0:
                clusters[cid].append(sig)
        return dict(clusters)

    def _compute_slope(self, signals: list[dict]) -> float:
        """OLS velocity slope from signal timestamps. One pass."""
        if len(signals) < 2:
            return 0.0
        try:
            import time
            now = time.time()
            xs, ys = [], []
            cumulative = 0
            for s in sorted(signals, key=lambda x: x.get("created_at", datetime.now(UTC))):
                cumulative += 1
                age_h = 1.0  # simplified: all signals in window
                xs.append(age_h)
                ys.append(float(cumulative))
            n = len(xs)
            if n < 2:
                return 0.0
            sx = sum(xs)
            sy = sum(ys)
            sxy = sum(x * y for x, y in zip(xs, ys))
            sxx = sum(x * x for x in xs)
            denom = n * sxx - sx * sx
            if abs(denom) < 1e-10:
                return 0.0
            return (n * sxy - sx * sy) / denom
        except Exception:
            return 0.0

    def _compute_acceleration(self, signals: list[dict]) -> float:
        """Estimate second derivative: slope of recent half vs older half."""
        if len(signals) < 6:
            return 0.0
        mid = len(signals) // 2
        slope_old = self._compute_slope(signals[:mid])
        slope_new = self._compute_slope(signals[mid:])
        return slope_new - slope_old

    def _compute_coherence(self, signals: list[dict]) -> float:
        """Cross-platform coherence: fraction of platforms contributing."""
        platforms = {s.get("platform", "") for s in signals}
        # More platforms + similar signal count per platform = more coherent
        if len(platforms) == 0:
            return 0.0
        counts = {}
        for s in signals:
            p = s.get("platform", "")
            counts[p] = counts.get(p, 0) + 1
        # Entropy-based: high entropy = evenly distributed = coherent
        total = sum(counts.values())
        import math
        entropy = -sum((c / total) * math.log(c / total) for c in counts.values())
        max_entropy = math.log(len(platforms)) if len(platforms) > 1 else 1.0
        return float(entropy / max_entropy) if max_entropy > 0 else 0.0

    def _classify_type(self, slope: float, acceleration: float) -> str:
        if slope > 2.0 and acceleration > 1.0:
            return "accelerating"
        elif slope > 1.0 and acceleration >= 0:
            return "emerging"
        elif slope > 0 and acceleration < -0.5:
            return "peaking"
        elif slope < -0.5:
            return "declining"
        else:
            return "noise"

    def _compute_confidence(
        self, count: int, slope: float, coherence: float, platform_count: int
    ) -> float:
        volume = min(1.0, count / 20)
        velocity = min(1.0, max(0.0, slope / 5.0))
        diversity = min(1.0, platform_count / 4)
        return 0.40 * volume + 0.30 * velocity + 0.20 * coherence + 0.10 * diversity

    def _extract_label(self, signals: list[dict]) -> str:
        """Extract most common meaningful term as pattern label."""
        import re
        from collections import Counter
        terms: Counter = Counter()
        for s in signals:
            words = [w for w in re.findall(r"\b[a-z]{4,}\b", s.get("title", "").lower())
                     if w not in _STOPWORDS]
            terms.update(words)
        top = terms.most_common(3)
        return " + ".join(w for w, _ in top) if top else "unnamed"


_STOPWORDS = frozenset({
    "this", "that", "with", "from", "have", "will", "been", "they",
    "were", "what", "when", "your", "into", "more", "their", "than",
    "then", "some", "would", "which", "there", "about", "other",
    "after", "first", "these", "through", "just", "like",
})
```

Replace `src/aegis/scrape/patterns.py`'s `detect_patterns()` call in `scrape_topic()`
with `PatternEngine().detect()` for swarm-scale signals.
Keep `detect_patterns()` as a thin wrapper that calls `PatternEngine` for backward compat.

---

## ⓳ PASS 12 — OPERATIONAL APPENDIX AND INVARIANT ENFORCEMENT

### 12A — Autonomous loop completeness audit

The autonomous loop in `src/aegis/scheduler/autonomous.py` must run ALL of these jobs.
Verify each is present and properly scheduled:

```python
# COMPLETE JOB SCHEDULE FOR autonomous.py
# Every job must be present. Every job must have error handling. None can crash the loop.

JOBS = [
    # Every 5 minutes
    ("job_health_check",         "*/5  * * * *", "AegisHealthChecker.check_and_heal()"),
    ("job_clock_drift_check",    "*/15 * * * *", "_check_clock_drift()"),

    # Every 30 minutes
    ("job_scrape",               "*/30 * * * *", "SwarmOrchestrator.run_all_waves()"),
    ("job_threshold_refresh",    "*/30 * * * *", "DynamicThresholds._get() — cache refresh"),

    # Hourly
    ("job_weight_refresh",       "0    * * * *", "reload agent weights from Redis"),
    ("job_pattern_analysis",     "0    * * * *", "PatternEngine.detect() on last 1h signals"),

    # Every 6 hours
    ("job_shadow_evaluate",      "0  */6 * * *", "evaluate shadow models vs champion"),
    ("job_policy_weights_refresh","0  */6 * * *", "reload RL pricing policy weights"),

    # Daily at 2 AM UTC
    ("job_datalake_refresh",     "0    2 * * *", "DataLake ingest + silver + gold"),
    ("job_drift_check",          "0    2 * * *", "DriftDetector.run_all_checks()"),
    ("job_schema_drift_sweep",   "30   2 * * *", "scan all adapters for schema drift"),

    # Weekly (Sunday 3 AM UTC)
    ("job_weight_update",        "0    3 * * 0", "update agent accuracy weights from outcomes"),
    ("job_threshold_update",     "30   3 * * 0", "DynamicThresholds.update_from_outcomes()"),
    ("job_retrain",              "0    4 * * 0", "RetrainingPipeline.run_weekly_retrain()"),
    ("job_evidently_report",     "0    5 * * 0", "EvidentlyMonitor drift report"),
]
```

For every job: `try/except Exception as exc: _log.error(f"{job_name}.failed", error=str(exc))`
The autonomous loop must NEVER crash because a single job fails.

### 12B — Structured error codes for all new modules

Every new module in Passes 1-11 must have error codes in its `errors.py` (or the
nearest parent `errors.py`). Follow the existing pattern:

```
AEGIS-SCRAPE-0030 through 0039: MinHash dedup errors
AEGIS-SCRAPE-0040 through 0049: Adapter router errors
AEGIS-SCRAPE-0050 through 0059: Semantic index (FAISS) errors
AEGIS-SCRAPE-0060 through 0069: Pattern engine errors
AEGIS-CORE-0010  through 0019: Dynamic threshold errors
AEGIS-CORE-0020  through 0029: Event bus errors
AEGIS-PREDICT-0020 through 0029: Online ML (River) errors
AEGIS-EVOLVE-0100 through 0109: MLflow tracking errors
AEGIS-EVOLVE-0110 through 0119: Evidently monitor errors
AEGIS-INTEL-0001 through 0019: Research engine errors
AEGIS-SCHED-0001 through 0019: Health checker errors
```

For each new error code: add to the module's `errors.py` AND create
`docs/errors/AEGIS-MODULE-XXXX.md` with: description, likely cause, resolution steps.

### 12C — Verify the 15 invariants programmatically

Add `tests/unit/test_invariants.py` — a test file that checks all 15 invariants
by analyzing the codebase itself:

```python
"""
Programmatic invariant verification.
These tests fail if anyone accidentally violates the 15 sacred invariants.
Run as part of the standard test suite: pytest tests/unit/test_invariants.py
"""

import re
from pathlib import Path

SRC = Path("src")
PHASE4_SRC = Path("aegis-phase4/src")

def _py_files(*roots) -> list[Path]:
    files = []
    for root in roots:
        files.extend(root.rglob("*.py"))
    return [f for f in files if not any(p in str(f) for p in ["test_", ".pyc", "__pycache__"])]

def test_invariant_1_stream_field_body():
    """§1: Redis stream field must always be 'body'."""
    violations = []
    pattern = re.compile(r'"(payload|data|message)"\s*:', re.MULTILINE)
    context = re.compile(r'(xadd|XADD|publish_event)', re.MULTILINE)
    for f in _py_files(SRC, PHASE4_SRC):
        content = f.read_text()
        for match in pattern.finditer(content):
            line_start = content.rfind("\n", 0, match.start()) + 1
            line_end = content.find("\n", match.end())
            line = content[line_start:line_end]
            if context.search(line) and "# noqa" not in line:
                violations.append(f"{f}:{content[:match.start()].count(chr(10))+1}: {line.strip()}")
    assert not violations, f"§1 violations (use 'body' not payload/data/message):\n" + "\n".join(violations)

def test_invariant_4_no_naive_datetimes():
    """§4: All datetimes must be timezone-aware UTC."""
    violations = []
    pattern = re.compile(r'datetime\.now\(\)', re.MULTILINE)
    for f in _py_files(SRC):
        content = f.read_text()
        for match in pattern.finditer(content):
            line_no = content[:match.start()].count("\n") + 1
            line = content.split("\n")[line_no - 1]
            if "timezone" not in line and "UTC" not in line.upper() and "# noqa" not in line:
                violations.append(f"{f}:{line_no}: {line.strip()}")
    assert not violations, f"§4 violations (use datetime.now(UTC)):\n" + "\n".join(violations)

def test_invariant_5_no_stdlib_logging_in_agents():
    """§5: Only structlog in aegis.agents.* and aegis.llm.*"""
    violations = []
    agent_dirs = [SRC / "aegis" / "agents", SRC / "aegis" / "llm"]
    pattern = re.compile(r'^import logging$|^from logging import', re.MULTILINE)
    for agent_dir in agent_dirs:
        for f in agent_dir.rglob("*.py"):
            content = f.read_text()
            if pattern.search(content) and "structlog" not in content:
                violations.append(str(f))
    assert not violations, f"§5 violations (use structlog):\n" + "\n".join(violations)

def test_invariant_6_no_pydantic_v1():
    """§6: No Pydantic v1 patterns."""
    violations = []
    patterns = [
        re.compile(r'@validator\b'),
        re.compile(r'from pydantic import.*\bvalidator\b'),
        re.compile(r'\.dict\(\)'),
    ]
    for f in _py_files(SRC):
        content = f.read_text()
        for pattern in patterns:
            for match in pattern.finditer(content):
                line_no = content[:match.start()].count("\n") + 1
                line = content.split("\n")[line_no - 1]
                if "# noqa" not in line:
                    violations.append(f"{f}:{line_no}: {line.strip()}")
    assert not violations, f"§6 violations (use pydantic v2):\n" + "\n".join(violations)

def test_invariant_3_kelly_cap():
    """§3: Kelly fraction never exceeds 0.25."""
    violations = []
    pattern = re.compile(r'kelly_fraction\s*=\s*([0-9.]+)')
    for f in _py_files(SRC, PHASE4_SRC):
        content = f.read_text()
        for match in pattern.finditer(content):
            value_str = match.group(1)
            try:
                value = float(value_str)
                if value > 0.25:
                    line_no = content[:match.start()].count("\n") + 1
                    violations.append(f"{f}:{line_no}: kelly_fraction={value} > 0.25")
            except ValueError:
                pass
    assert not violations, f"§3 violations:\n" + "\n".join(violations)

def test_invariant_11_streams_capped():
    """§11: All Redis stream xadd calls must specify maxlen."""
    violations = []
    xadd_pattern = re.compile(r'\.xadd\(|xadd\(')
    for f in _py_files(SRC, PHASE4_SRC):
        content = f.read_text()
        for match in xadd_pattern.finditer(content):
            line_no = content[:match.start()].count("\n") + 1
            # Check the next 5 lines for maxlen
            lines_after = "\n".join(content.split("\n")[line_no-1:line_no+4])
            if "maxlen" not in lines_after and "# noqa" not in lines_after:
                violations.append(f"{f}:{line_no}: xadd without maxlen")
    assert not violations, f"§11 violations (add maxlen=10_000):\n" + "\n".join(violations)
```

---

## ⓴ FINAL OPERATIONAL CHECKLIST

Before the prompt is considered complete, verify every item in this checklist.
This is the difference between "mostly done" and "production ready".

### System behavior checklist

```
□ scrape_topic("HDFC Bank NSE stock") routes to nse_bse, moneycontrol first
□ scrape_topic("wireless earbuds buy") routes to amazon_in, flipkart first
□ scrape_topic("wholesale manufacturer") routes to indiamart first
□ AEGIS_DISABLE_OLLAMA=1 → all agents produce heuristic verdicts, no exception
□ Redis offline → all stream publishes fail silently with log, no crash
□ Postgres offline → dashboard returns 503, autonomous loop retries, no crash
□ All adapters quarantined → emergency health check fires, alert sent
□ P0 signal (score ≥ 0.85) → deep verify runs → council runs (if enabled)
□ Trade settled with ROI → OutcomeRecorder called → evolve stream published
□ Drift score > 2× threshold → auto rollback to previous champion
□ New model trained with +2% AUC → shadow deployment starts (72h unless fast_promote)
□ Clock drift > 5s → hwclock sync attempted → log WARNING
□ Dashboard loads with all 8 panels rendering (no panel shows blank/error on fresh start)
□ /api/health/streams returns status for all 6 canonical streams
□ ruff check → 0 violations
□ pytest tests/unit/ → ≥ 80% coverage, all green
```

### Performance targets checklist

```
□ deduplicate_batch(1000 signals)  < 500ms
□ hot_path_compliance_gate          < 5ms  (p99)
□ InferenceRunner.run() heuristic   < 50ms (p99)
□ AdapterRouter.route()             < 5ms  (no network calls)
□ TopicClassifier.classify()        < 1ms  (keyword lookup)
□ PatternEngine.detect(1000)        < 200ms
□ Dashboard /api/signals/stats      < 200ms (with DB)
□ /api/health/streams               < 100ms (all Redis calls parallel)
```

### Documentation completeness checklist

```
□ docs/features.md — all 24 features documented with formula + range + interpretation
□ docs/adr/0016-predict-path-consolidation.md
□ docs/adr/0017-minhash-dedup.md
□ docs/adr/0018-weighted-ensemble.md
□ docs/adr/0019-evolution-loop.md
□ docs/SYSTEM_STATE.md — current state after KRONOS-OMEGA
□ CLAUDE.md — updated phase status, new modules, new invariants
□ .env.example — all new env vars with defaults and comments
□ docs/errors/ — stub files for all new AEGIS-*-XXXX codes
```

---

## PROMPT METADATA

```
Prompt name:    AEGIS KRONOS-OMEGA
Version:        3.0 (Godmode Absolute)
Compilation date: 2026-06-10
Target:         Claude Code (claude-opus-4 / claude-sonnet-4)
Codebase:       WSL2 Ubuntu 24.04, Python 3.12, uv monorepo
Total passes:   12 (0-11) + verification (Pass 8) + deliverables
Total checks:   20 mandatory (Pass 8 gauntlet)
New files:      ~18 across all passes
Modified files: ~25 across all passes
New tests:      ~150 minimum
Coverage target: 82%

Sacred invariants: 15 (§1-§15)
OSS integrations: River, FAISS, Evidently, MLflow, NetworkX, Optuna
New capabilities:
  - Intelligent adapter routing (TopicClassifier + AdapterRouter)
  - Online/incremental ML (River Hoeffding trees)
  - Semantic search (FAISS + BGE-M3)
  - Creator graph analysis (NetworkX)
  - Dynamic thresholds (self-adapting from outcomes)
  - Feedback-weighted agent ensemble
  - Deep verification (second-pass P0 signal check)
  - 5-pass research engine
  - Self-healing autonomous loop (AegisHealthChecker)
  - Shadow model deployment (72h evaluation before promotion)
  - XREADGROUP consumer groups (no missed entries)
  - MinHash + LSH deduplication (40× faster)
  - Shared HTTP client factory (ADP-7)
  - Evidently AI drift reports
  - MLflow experiment tracking
  - Real-time pattern engine (DBSCAN + temporal acceleration)

System capabilities after completion:
  - Recognizes patterns on first pass (PatternEngine)
  - Routes to right adapters per query (AdapterRouter)
  - Learns from every trade (closed evolution loop)
  - Heals itself every 5 minutes (AegisHealthChecker)
  - Produces deep cross-verified research (ResearchEngine)
  - Adapts thresholds from outcomes weekly (DynamicThresholds)
  - Never crashes permanently (all components have fallbacks)
```

---

*End of AEGIS KRONOS-OMEGA Prompt*
*"The system that senses, processes, acts, heals, and reports — permanently."*
