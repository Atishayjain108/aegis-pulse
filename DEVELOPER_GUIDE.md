# AEGIS Pulse — Developer Guide

**Operations reference + path to 90% test coverage**

> See [RUNBOOK.md](RUNBOOK.md) for the full step-by-step setup, UI screenshots, and troubleshooting table.  
> This file is the daily-driver reference: how to operate the system and how to improve the test suite.

---

## Table of Contents

1. [Quick Start](#1-quick-start)
2. [Daily Operations](#2-daily-operations)
3. [CLI Reference](#3-cli-reference)
4. [Infrastructure](#4-infrastructure)
5. [Phase 2 Agent Pipeline](#5-phase-2-agent-pipeline)
6. [Test Suite](#6-test-suite)
7. [Coverage Roadmap — 79% → 90%](#7-coverage-roadmap--79--90)

---

## 1. Quick Start

```bash
# One-time setup
uv sync --all-extras
docker compose up -d

# Verify everything is healthy
docker compose ps            # all 7 services should show (healthy)
uv run aegis signals tail    # shows last 20 signals in DB

# Scrape + analyze (full loop)
uv run aegis scrape --source hacker-news --limit 30
uv run aegis scrape --source github-trending --limit 20
uv run aegis scrape --source reddit-rss --subreddit MachineLearning --limit 30
uv run aegis scrape --source amazon --limit 40
AEGIS_DISABLE_OLLAMA=1 uv run aegis analyze --limit 20

# Run tests
uv run pytest tests/unit/ -q
```

---

## 2. Daily Operations

### Start / stop

```bash
docker compose up -d                        # start all services
docker compose down                         # stop (data preserved in volumes)
docker compose down -v                      # stop + wipe all data (destructive)
docker compose restart <service>            # restart a single service
docker compose logs -f aegis-postgres       # tail one service's logs
```

### Recommended daily scrape sequence (no API keys needed)

```bash
uv run aegis scrape --source reddit-rss --subreddit MachineLearning --limit 50
uv run aegis scrape --source reddit-rss --subreddit Entrepreneur --limit 50
uv run aegis scrape --source hacker-news --limit 50
uv run aegis scrape --source github-trending --limit 30
uv run aegis scrape --source amazon --limit 80
```

### Health check

```bash
docker compose ps                               # all show (healthy)
uv run aegis signals tail --limit 5            # DB reachable, data present
AEGIS_DISABLE_OLLAMA=1 uv run aegis analyze --limit 3  # pipeline runs
```

### Database access (via Docker — psql not needed locally)

```bash
docker exec -it aegis-postgres psql -U aegis_app -d aegis

# Useful queries inside psql:
SELECT COUNT(*), platform FROM signals GROUP BY platform;
SELECT title, platform, created_at FROM signals ORDER BY created_at DESC LIMIT 10;
```

### Redis inspection

```bash
docker exec -it aegis-redis redis-cli
> KEYS *        # list keys
> DBSIZE        # total key count
```

---

## 3. CLI Reference

| Command | What it does |
|---------|--------------|
| `aegis scrape --source <name> [--limit N]` | Run one scrape adapter, persist to DB |
| `aegis scrape --source reddit-rss --subreddit <sub>` | Reddit RSS (no API key) |
| `aegis signals tail [--limit N] [--platform P]` | Show recent signals from DB |
| `aegis analyze [--limit N] [--trend-id ID]` | Run Phase 2 agent pipeline |

### Adapter names

| Name | Status | Notes |
|------|--------|-------|
| `hacker-news` | Working | Algolia API, no key |
| `github-trending` | Working | GitHub HTML scrape |
| `reddit-rss` | Working | Reddit JSON API, no key |
| `amazon` | Working | Bestseller page scrape |
| `google-trends` | Working | Requires `pytrends` via `--all-extras` |
| `tiktok` | Broken | Requires TikTok Ads auth |
| `pinterest` | Broken | Returns 403 |
| `nitter` | Broken | All public instances dead |
| `reddit` | Key required | `AEGIS_REDDIT_CLIENT_ID` + `SECRET` |
| `youtube` | Key required | `AEGIS_YOUTUBE_API_KEY` |
| `instagram` | High ban risk | Needs session + `allow_red_tos=True` |

---

## 4. Infrastructure

| Service | URL / Port | Credentials | Purpose |
|---------|-----------|-------------|---------|
| TimescaleDB | `localhost:5433` | `aegis_app / aegis_app_dev_pw` | Primary DB |
| Redis | `localhost:6380` | none | Cache + Streams |
| MinIO Console | http://localhost:9003 | `minioadmin / minioadmin` | Object store UI |
| Grafana | http://localhost:3001 | `admin / aegis_dev_admin_pw` | Dashboards |
| Prometheus | http://localhost:9091 | none | Metrics |
| Jaeger UI | http://localhost:16687 | none | Distributed traces |
| FlareSolverr | `localhost:8191` | none | Cloudflare bypass |

DB DSN: `postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis`  
Redis URL: `redis://localhost:6380/0`

### Docker command gotchas (INFRA-4 / INFRA-5)

- **`exec` / `logs` take the SERVICE name, not the container name.** Service
  names are the keys in `docker-compose.yml` (`postgres`, `redis`, `dashboard`,
  `predict`, `execute-api`, `loki`, …); container names are the `aegis-`-prefixed
  runtime names (`aegis-postgres`, …). Using a container name yields a misleading
  `"service X is not running"` even when it's healthy.
  ```bash
  docker compose logs -f predict          # ✅ service name
  docker compose exec postgres psql -U aegis_app -d aegis
  docker compose logs -f aegis-predict    # ❌ container name → "not running"
  ```
- **`predict` port mapping is `8100:8000`.** The container listens on `8000`
  internally; the host sees it on **`:8100`**. Docs refer to ":8100" (host side) —
  that's correct, not a typo. In-cluster callers use `http://predict:8000`.
- **Dashboard runs on the HOST, not in Docker.** `uv run aegis dashboard serve`
  (`:8300`) is the supported path; the compose `dashboard` service is opt-in only
  behind `--profile dashboard` and carries no `docker.sock` (see INFRA-1/2).

---

## 5. Phase 2 Agent Pipeline

### How it works

```
TrendCandidate  ──►  LangGraph DAG  ──►  GraphResult
                      │
                      ├── scout           (velocity + breakout scoring)
                      ├── geo_arbitrage   (geographic spread)
                      ├── narrative       (sentiment + novelty)
                      ├── historian       (ChromaDB analogues lookup)
                      ├── sourcer         (supplier/category identification)
                      ├── auditor         (signal quality + author credibility)
                      ├── sentinel        (anomaly + manipulation detection)
                      ├── compliance      (regulatory flags)
                      ├── red_team        (falsifier challenge)
                      ├── hedge           (portfolio concentration veto)
                      └── finalize        (supervisor aggregation)
```

Every agent runs heuristic-first. LLM (if available) refines reasoning text only — it cannot change the verdict. Pipeline always completes even with no LLM keys.

### `TrendCandidate` fields (Pydantic v2 frozen)

```python
TrendCandidate(
    trend_id="my-trend-001",        # required, unique ID
    title="AI coding assistant",    # required
    signal_count=45,                # int, default 0
    unique_authors=12,              # int, default 0
    platforms=["hacker_news"],      # list[str], default []
    velocity_1h=8.5,                # float, default 0.0
    velocity_6h=3.2,                # float, default 0.0
    velocity_24h=1.8,               # float, default 0.0
    sentiment=0.65,                 # float -1..1, default 0.0
    commercial_intent=0.7,          # float 0..1, default 0.0
    novelty=0.72,                   # float 0..1, default 0.0
    coordination_risk=0.1,          # float 0..1, default 0.0
    summary="optional summary",     # str, default ""
)
```

### `GraphResult` fields (read these, not `verdict`/`score`)

```python
result.final_verdict      # AgentVerdict: PROCEED / HOLD / BLOCK
result.final_score        # float 0..1
result.final_confidence   # float 0..1
result.final_priority     # Priority enum (integer 1-5)
result.halt_reason        # str: "completed" | "vetoed_by_red_team" | ...
result.decisions          # list[AgentDecision] — one per agent
result.blocked_by         # list[str] — agent names that issued BLOCK
result.duration_ms        # float
```

### Run the pipeline programmatically

```python
import asyncio, uuid
from aegis.agents.runner import run_trend
from aegis.agents.schemas import TrendCandidate

tc = TrendCandidate(
    trend_id=f"my-{uuid.uuid4().hex[:8]}",
    title="Your trend title here",
    signal_count=30,
    platforms=["hacker_news"],
    velocity_1h=5.0,
    commercial_intent=0.6,
)
result = asyncio.run(run_trend(tc))
print(result.final_verdict, result.final_score)
```

### LLM setup (optional — heuristics work without it)

Set any of the following in `.env` to enable LLM-augmented reasoning:

```bash
AEGIS_GROQ_API_KEY=gsk_...           # Groq (recommended, free tier)
AEGIS_OPENROUTER_API_KEY=sk-or-...   # OpenRouter fallback
AEGIS_GEMINI_API_KEY=AIza...         # Gemini fallback
# AEGIS_DISABLE_OLLAMA=1             # Skip local Ollama in dev/test
```

---

## 6. Test Suite

```bash
# Run all unit tests
uv run pytest tests/unit/ -q

# With coverage report
uv run pytest tests/unit/ --cov=aegis --cov-report=term-missing -q

# Run a specific test file
uv run pytest tests/unit/agents/test_nodes.py -v

# Run tests matching a keyword
uv run pytest tests/unit/ -k "hedge" -v

# Fail fast on first error
uv run pytest tests/unit/ -x -q
```

### Test environment variables

```bash
AEGIS_ENV=test
AEGIS_DISABLE_OLLAMA=1
AEGIS_AGENT_HMAC_KEY=test-key-do-not-use-in-prod
AEGIS_PG_DSN=postgresql://aegis_app:test@127.0.0.1:5432/aegis_test
AEGIS_REDIS_URL=redis://127.0.0.1:6379/15
```

These are already set via `pyproject.toml` `[tool.pytest.ini_options]`.

### Current state

- **467 tests**, all passing  
- **79.54% coverage** (floor: 78%)  
- Tests live in `tests/unit/` and `tests/unit/agents/`

---

## 7. Coverage Roadmap — 79% → 90%

Current total: **79.54%**. Target: **90%**. Gap: ~10.5 percentage points.  
To close this gap, ~340 additional statement lines need to be covered.

The sections below list each file, its current coverage, the missing lines, and exactly what to test.

### Priority 1 — Zero coverage (quick wins, ~46 lines)

#### `src/aegis/agents/tools/signal_query.py` — 0% (32 lines)

All 32 executable lines are uncovered. The module is imported lazily so the `0%` is because no test ever calls `fetch_recent()`.

Tests to write (`tests/unit/agents/test_tools.py`):

```python
# Test 1: no shared pool configured
async def test_signal_query_no_pool(monkeypatch):
    monkeypatch.setattr("aegis.agents.tools.signal_query.get_shared_pool", lambda: None)
    # wait — it's lazily imported inside the function; patch via aegis.db.pool
    from aegis.agents.tools.signal_query import fetch_recent
    result = await fetch_recent(tenant_id="00000000-0000-0000-0000-000000000001")
    assert not result.ok
    assert result.error_code == "AEGIS-TOOL-SIGNAL-NO-POOL"

# Test 2: DB query succeeds, rows are dicts
async def test_signal_query_success(monkeypatch):
    mock_rows = [{"id": "abc", "title": "test"}, {"id": "def", "title": "other"}]
    monkeypatch.setattr("aegis.db.pool.get_shared_pool", lambda: object())  # truthy
    monkeypatch.setattr("aegis.db.signals.fetch_recent_signals",
                        AsyncMock(return_value=mock_rows))
    result = await fetch_recent(tenant_id="00000000-0000-0000-0000-000000000001", limit=5)
    assert result.ok
    assert result.metadata["count"] == 2

# Test 3: DB query raises exception
async def test_signal_query_db_error(monkeypatch):
    monkeypatch.setattr("aegis.db.pool.get_shared_pool", lambda: object())
    monkeypatch.setattr("aegis.db.signals.fetch_recent_signals",
                        AsyncMock(side_effect=RuntimeError("conn refused")))
    result = await fetch_recent(tenant_id="00000000-0000-0000-0000-000000000001")
    assert not result.ok
    assert "db query failed" in result.error_message

# Test 4: rows with model_dump (Pydantic objects)
# Test 5: rows that are neither dict nor model (raw asyncpg Records — use dict(r))
# Test 6: platform filter passed through to fetch_recent_signals
```

#### `src/aegis/agents/memory/__init__.py` — 0% (4 lines: 21-25)

Lines 21-25 are the `from .chroma_store import ...` block. They fail to import in the test environment because `chromadb` / `sentence-transformers` may not be installed with `--all-extras` in CI. Wrap the import test:

```python
# tests/unit/agents/test_memory_init.py
def test_memory_exports_importable():
    try:
        from aegis.agents.memory import ChromaMemoryStore, SharedWorkingMemory, SnapshotManager
        assert ChromaMemoryStore is not None
    except ImportError:
        pytest.skip("chromadb not installed")
```

---

### Priority 2 — Core nodes (38–62%, ~95 lines)

#### `src/aegis/agents/nodes/hedge.py` — 38% (38 lines: 91-110, 136-171, 190-208)

The uncovered blocks are: the `SharedWorkingMemory` active-pool path (lines 91-115), the concentration-scoring + verdict logic (lines 136-171), and `_augment_with_llm` (lines 190-208).

Tests to write (`tests/unit/agents/test_nodes.py` or dedicated `test_hedge.py`):

```python
from unittest.mock import AsyncMock, MagicMock
from aegis.agents.nodes.hedge import HedgeAgent
from aegis.agents.schemas import AgentVerdict

# shared_memory mock helper
def make_sm(active_trends=None, category_data=None):
    sm = AsyncMock()
    sm.active_trends = AsyncMock(return_value=active_trends or [])
    sm.get_all = AsyncMock(return_value=category_data or {})
    return sm

# Test 1: empty active pool (no SM) → PROCEED
async def test_hedge_no_pool():
    agent = HedgeAgent(shared_memory=None, use_llm=False)
    state = {"candidate": make_candidate(), "sourcer_supplier": None}
    decision = await agent._decide_heuristic(make_candidate(), state)
    assert decision.verdict == AgentVerdict.PROCEED
    assert decision.details["active_pool_size"] == 0

# Test 2: SM available, empty active_trends → PROCEED with sm_used=True
async def test_hedge_sm_empty():
    sm = make_sm(active_trends=[])
    agent = HedgeAgent(shared_memory=sm, use_llm=False)
    decision = await agent._decide_heuristic(make_candidate(), {"sourcer_supplier": {}})
    assert decision.verdict == AgentVerdict.PROCEED
    assert decision.details["shared_memory_used"] is True

# Test 3: concentration below cap → PROCEED
async def test_hedge_under_cap():
    tenant = "tenant-1"
    sm = make_sm(
        active_trends=[("tenant-1", "trend-other-1"), ("tenant-1", "trend-other-2")],
        category_data={"category": "fitness"},
    )
    agent = HedgeAgent(shared_memory=sm, use_llm=False)
    state = {"tenant_id": tenant, "trend_id": "trend-new",
             "sourcer_supplier": {"category": "electronics"}}
    decision = await agent._decide_heuristic(make_candidate(), state)
    assert decision.verdict == AgentVerdict.PROCEED

# Test 4: concentration at HOLD threshold
async def test_hedge_hold_threshold():
    # 8 trends in same category out of 10 total → ~81% → excess > HOLD threshold
    ...

# Test 5: concentration at BLOCK threshold
async def test_hedge_block():
    ...

# Test 6: _extra_state returns correct keys
# Test 7: _augment_with_llm returns None when verdict is PROCEED
# Test 8: _augment_with_llm called when verdict is HOLD (mock _llm_complete)
```

#### `src/aegis/agents/nodes/base.py` — 53% (32 lines: 83-88, 119, 130-133, 147-181)

Missing: `_augment_with_llm` being called + augmented result returned (line 83-88), `_llm_complete` when `router=None` falling back to `get_default_router` (lines 130-133), and the full `_llm_apply` method (lines 147-181).

```python
# Test 1: __call__ — heuristic raises, error_decision returned
async def test_base_heuristic_exception():
    class BrokenAgent(AgentNode):
        name = "broken"
        async def _decide_heuristic(self, c, s): raise ValueError("boom")
    agent = BrokenAgent(use_llm=False)
    result = await agent({"candidate": make_candidate()})
    decision = result["decisions"][0]
    assert "agent error" in decision.reasoning

# Test 2: _llm_apply with valid JSON {"reasoning": "...", "confidence_factor": 0.8}
def test_llm_apply_valid_json():
    from aegis.agents.nodes.base import AgentNode
    from aegis.agents.llm.providers.base import LLMResponse
    agent = ConcreteAgent(use_llm=False)
    heuristic = make_decision(confidence=0.6, reasoning="base")
    resp = LLMResponse(text='{"reasoning": "LLM insight", "confidence_factor": 0.9}',
                       provider="test", model="m", ...)
    result = agent._llm_apply(heuristic, resp)
    assert "[LLM]" in result.reasoning
    assert result.confidence == pytest.approx(0.54)  # 0.6 * 0.9

# Test 3: _llm_apply with free-form text (not JSON)
def test_llm_apply_freeform():
    resp = LLMResponse(text="This looks promising", ...)
    result = agent._llm_apply(heuristic, resp)
    assert "[LLM] This looks promising" in result.reasoning
    assert result.details["llm"]["parsed"] is False

# Test 4: _llm_apply with None resp → returns heuristic unchanged
# Test 5: _llm_complete when router=None uses get_default_router()
# Test 6: __call__ — LLM augmentation path (use_llm=True, mock _augment_with_llm)
# Test 7: __call__ — LLM augmentation raises, falls back to heuristic
# Test 8: _merge_partial — BLOCK verdict adds to blocked_by
```

#### `src/aegis/agents/nodes/historian.py` — 62% (18 lines: 79-92, 97, 106-109, 141-162)

Missing: the `store_ok=True` code path with analogues returned (lines 79-95), `mean_sim` calculation from analogues (line 97), `confidence = 0.2` when no store (lines 106-109), and full `_augment_with_llm` (lines 141-162).

```python
# Test 1: store=None → low confidence, score=0, store_available=False
async def test_historian_no_store():
    agent = HistorianAgent(store=None, use_llm=False)
    decision = await agent._decide_heuristic(make_candidate(), {})
    assert decision.confidence == 0.2
    assert decision.details["store_available"] is False

# Test 2: store returns 3 analogues → mean_sim computed, higher confidence
async def test_historian_with_analogues():
    mock_store = AsyncMock()
    mock_store.query = AsyncMock(return_value=[
        MockRecord(id="1", text="past trend A", score=0.8, metadata={}),
        MockRecord(id="2", text="past trend B", score=0.72, metadata={}),
    ])
    from aegis.agents.tools.historical import find_analogues
    # patch find_analogues to return a ToolResult.success with the records
    ...
    agent = HistorianAgent(store=mock_store, k=5, use_llm=False)
    decision = await agent._decide_heuristic(make_candidate(), {})
    assert len(decision.details["analogues"]) == 2
    assert decision.details["mean_similarity"] > 0

# Test 3: store returns empty → mean_sim=0
# Test 4: _augment_with_llm skipped when analogues is empty
# Test 5: _augment_with_llm called when analogues present (mock _llm_complete)
# Test 6: _merge_partial never adds to blocked_by (information contributor)
```

---

### Priority 3 — LLM layer (48–69%, ~55 lines)

#### `src/aegis/agents/llm/prompts.py` — 48% (12 lines: 41, 52-55, 65-68, 73-75)

Missing: `_env()` cold-build (line 41), `_get_template` `TemplateNotFound` path (lines 52-55), `render()` called end-to-end (lines 65-68), `list_available()` with existing and missing directory (lines 73-75).

```python
# tests/unit/agents/test_router.py (or new test_prompts.py)

def test_render_known_agent():
    from aegis.agents.llm.prompts import render
    # scout.jinja2 exists in src/aegis/agents/prompts/
    text, digest = render("scout", title="AI breakout", velocity_class="hot",
                          breakout_score=0.9, commercial_intent=0.7, signal_count=50,
                          unique_authors=20, platforms=["hn"])
    assert len(text) > 0
    assert len(digest) == 16

def test_render_missing_agent():
    from aegis.agents.llm.prompts import render, PromptNotFoundError
    with pytest.raises(PromptNotFoundError):
        render("nonexistent_agent_xyz")

def test_list_available():
    from aegis.agents.llm.prompts import list_available
    names = list_available()
    assert "scout" in names
    assert "hedge" in names
    assert len(names) >= 10  # one per agent

def test_list_available_missing_dir(tmp_path, monkeypatch):
    import aegis.agents.llm.prompts as p
    monkeypatch.setattr(p, "_PROMPT_DIR", tmp_path / "nonexistent")
    assert p.list_available() == []
```

> **Note:** check the Jinja2 variable names in each `.jinja2` file before writing render tests — the `StrictUndefined` env raises on missing vars.

#### `src/aegis/agents/llm/router.py` — 69% (43 lines: 126-128, 159-175, 255-323)

Missing: circuit breaker trip + cooldown recovery (lines 255-323), `complete()` returning `None` when all providers exhausted (lines 159-175), and `_is_available()` check with opened_at set (lines 126-128).

```python
# tests/unit/agents/test_router.py

async def test_circuit_breaker_trips_after_threshold():
    provider = AsyncMock(spec=LLMProvider)
    provider.name = "test"
    provider.complete = AsyncMock(side_effect=LLMProviderError("timeout"))
    router = LLMRouter([provider], config=RouterConfig(failure_threshold=3))
    for _ in range(3):
        await router.complete(system="s", user="u")
    # 4th call — provider should be skipped (circuit open)
    result = await router.complete(system="s", user="u")
    assert result is None
    assert provider.complete.call_count == 3  # not called the 4th time

async def test_circuit_breaker_recovers_after_cooldown():
    provider = AsyncMock(spec=LLMProvider)
    provider.name = "test"
    provider.complete = AsyncMock(side_effect=[
        LLMProviderError("fail"), LLMProviderError("fail"), LLMProviderError("fail"),
        MagicMock(text="ok", provider="test", model="m", ...)
    ])
    router = LLMRouter([provider], config=RouterConfig(failure_threshold=3, cooldown_s=0.01))
    for _ in range(3): await router.complete(system="s", user="u")
    await asyncio.sleep(0.05)  # let cooldown expire
    result = await router.complete(system="s", user="u")
    assert result is not None

async def test_config_error_marks_provider_dead():
    provider = AsyncMock(spec=LLMProvider)
    provider.name = "bad-config"
    provider.complete = AsyncMock(side_effect=LLMConfigError("bad api key"))
    router = LLMRouter([provider])
    await router.complete(system="s", user="u")
    # Provider should be permanently dead
    assert router._breakers["bad-config"].dead is True

async def test_all_providers_exhausted_returns_none():
    p1, p2 = AsyncMock(spec=LLMProvider), AsyncMock(spec=LLMProvider)
    p1.name, p2.name = "p1", "p2"
    p1.complete = p2.complete = AsyncMock(side_effect=LLMProviderError("err"))
    router = LLMRouter([p1, p2], config=RouterConfig(failure_threshold=1))
    result = await router.complete(system="s", user="u")
    assert result is None
```

---

### Priority 4 — Agent nodes with small gaps (73–79%, ~52 lines)

#### `src/aegis/agents/nodes/sentinel.py` — 73% (14 lines: 72-73, 79, 106-107, 151-170)

Lines 151-170 are the `_augment_with_llm` method. Lines 72-73 and 79 are edge cases in `_decide_heuristic` (high coordination_risk branch, low novelty path).

```python
# Test: high coordination_risk triggers anomaly flag
async def test_sentinel_coordination_risk():
    agent = SentinelAgent(use_llm=False)
    candidate = make_candidate(coordination_risk=0.85, novelty=0.2)
    decision = await agent._decide_heuristic(candidate, {})
    assert decision.details.get("coordination_detected") is True

# Test: _augment_with_llm called when verdict is HOLD (mock _llm_complete → None)
# Test: _augment_with_llm returns None on render exception
```

#### `src/aegis/agents/nodes/compliance.py` — 76% (13 lines: 67-71, 80, 162-183)

Lines 162-183 are `_augment_with_llm`. Lines 67-71 and 80 are high-risk flag branches.

```python
# Test: high commercial_intent + high coordination_risk triggers flags
async def test_compliance_flags_high_risk():
    agent = ComplianceAgent(use_llm=False)
    candidate = make_candidate(commercial_intent=0.95, coordination_risk=0.9)
    decision = await agent._decide_heuristic(candidate, {})
    assert len(decision.details.get("flags", [])) > 0
```

#### `src/aegis/agents/nodes/auditor.py` — 78% (11 lines: 45, 100, 185-206)

Lines 185-206 are `_augment_with_llm`. Line 45 is a branch inside `_decide_heuristic`. Line 100 is the high-author-diversity path.

#### `src/aegis/agents/nodes/narrative.py` — 78% (12 lines: 74, 83, 106, 177-196)

Lines 177-196 are `_augment_with_llm`. Lines 74, 83, 106 are sentiment edge branches.

---

### Priority 5 — Scrape layer (74–85%, ~100 lines across 4 files)

#### `src/aegis/scrape/base.py` — 74% (52 lines: 157-176, 303, 392-435, 514-594)

The largest untested block is the Cloudflare-bypass path (lines 514-535) and the rate-limiter token-bucket logic (lines 157-176). Focus on:

```python
# Test: rate limiter — semaphore blocks when at max concurrency
# Test: cancel_event checked mid-loop → exits cleanly
# Test: Cloudflare bypass invoked when 403 returned
# Test: parse() returning None → signal skipped, failed counter unchanged
# Test: parse() raising exception → increments failed counter, loop continues
```

#### `src/aegis/scrape/sources/amazon.py` — 61% (36 lines: 109, 129, 140-183, 239-241, 266-267)

Lines 140-183 are the secondary category pages (toys, electronics, etc.). They need the `httpx` response to be mocked with alternate HTML.

```python
# Test: HTTP error on category page → handled gracefully, partial results OK
# Test: empty bestseller list (no items in HTML) → 0 signals, no exception
# Test: title slug parsing edge cases ("the", "a", short slugs)
```

#### `src/aegis/scrape/sources/reddit_rss.py` — 85% (15 lines: 109, 123-164, 204-270)

```python
# Test: post with is_self=False (link post) → url field populated
# Test: parse() returns None for already-seen content_hash
# Test: nsfw posts filtered out based on over_18 flag
```

#### `src/aegis/scrape/sources/github_trending.py` — 85% (15 lines: 94, 107-133, 213-275)

```python
# Test: repository with no description → description defaults to ""
# Test: star count parsing with "k" suffix (e.g. "1.2k")
# Test: language field absent from HTML → language=None
```

---

### Priority 6 — Tools and misc (56–88%, ~30 lines)

#### `src/aegis/agents/tools/historical.py` — 56% (4 lines: 29-41)

The entire function body is uncovered. Add to `tests/unit/agents/test_tools.py`:

```python
async def test_find_analogues_filters_by_score():
    mock_store = AsyncMock()
    mock_store.query = AsyncMock(return_value=[
        SimpleNamespace(id="1", text="good", score=0.8, metadata={}),
        SimpleNamespace(id="2", text="bad", score=0.3, metadata={}),  # below min_score
    ])
    result = await find_analogues(mock_store, query_text="AI trend", k=5, min_score=0.55)
    assert result.ok
    assert result.metadata["count"] == 1  # only score=0.8 passes

async def test_find_analogues_empty_store():
    mock_store = AsyncMock()
    mock_store.query = AsyncMock(return_value=[])
    result = await find_analogues(mock_store, query_text="trend", k=5)
    assert result.ok
    assert result.metadata["count"] == 0
```

#### `src/aegis/agents/tools/base.py` — 88% (5 lines: 56, 79-82)

```python
# Test: ToolResult.failure() has ok=False, error_code set
def test_tool_result_failure():
    r = ToolResult.failure(code="ERR-001", message="something went wrong")
    assert r.ok is False
    assert r.error_code == "ERR-001"
    assert r.error_message == "something went wrong"
```

#### `src/aegis/core/metrics.py` — 67% (14 lines: 61, 85, 95-125)

Lines 95-125 are the Prometheus counter/histogram registration guards (only run once at import). They're skipped in test because prometheus_client is already initialized. Fix with:

```python
# Force re-import in isolated process, OR mock prometheus_client.CollectorRegistry
# Alternatively — mark these lines as # pragma: no cover since they're init guards
```

#### `src/aegis/agents/runner.py` — 84% (8 lines: 71, 140-165)

Lines 140-165 are the `compile_graph()` cache-miss path and the `run_trend()` pool-setup guard.

```python
# Test: run_trend() when no shared pool set → still completes (no DB tools called)
# Test: compile_graph() caches compiled graph (call twice, build only once)
```

---

### Summary table

| File | Current | Target | Est. lines to add | Strategy |
|------|---------|--------|-------------------|----------|
| `tools/signal_query.py` | 0% | 90% | 29 | Mock pool + db_signals |
| `memory/__init__.py` | 0% | 100% | 4 | Import in test |
| `nodes/hedge.py` | 38% | 90% | 34 | Mock SharedWorkingMemory |
| `nodes/base.py` | 53% | 90% | 29 | Test `_llm_apply`, error path |
| `llm/prompts.py` | 48% | 90% | 11 | Call `render()` + `list_available()` |
| `nodes/historian.py` | 62% | 90% | 16 | Mock ChromaMemoryStore |
| `tools/historical.py` | 56% | 100% | 4 | Mock ChromaMemoryStore |
| `llm/router.py` | 69% | 90% | 30 | Circuit breaker tests |
| `nodes/sentinel.py` | 73% | 90% | 10 | Edge-case inputs |
| `scrape/base.py` | 74% | 88% | 26 | Rate-limiter, cancel path |
| `nodes/compliance.py` | 76% | 90% | 10 | High-risk flag branches |
| `nodes/auditor.py` | 78% | 90% | 8 | High-diversity path |
| `nodes/narrative.py` | 78% | 90% | 9 | Sentiment edge cases |
| `scrape/amazon.py` | 61% | 80% | 18 | Error + edge paths |
| `tools/base.py` | 88% | 100% | 5 | ToolResult.failure() |
| `core/metrics.py` | 67% | 85% | 10 | Init guards or pragma |
| `agents/runner.py` | 84% | 95% | 6 | Cache + no-pool path |
| `reddit_rss.py` | 85% | 95% | 7 | Link posts, nsfw filter |
| `github_trending.py` | 85% | 95% | 7 | Missing fields, star format |
| **Total est. gain** | | | **~273 lines** | → ~85.5% stmt + branch lift |

> Covering these 19 files pushes estimated overall coverage to **≥ 90%**.

### Reaching 90% — execution order

Work in this order to reach 90% in the fewest test-writing cycles:

1. `signal_query.py` + `memory/__init__.py` + `tools/historical.py` + `tools/base.py` — all small, no external deps, ~42 lines. Run coverage after. Should jump to ~81%.
2. `nodes/base.py` + `llm/prompts.py` — foundational for all node tests. ~40 lines. Should reach ~82.5%.
3. `nodes/hedge.py` + `nodes/historian.py` — require simple AsyncMocks. ~50 lines. Should reach ~84%.
4. `llm/router.py` — circuit breaker logic, ~30 lines. Should reach ~85.5%.
5. `nodes/sentinel.py` + `nodes/compliance.py` + `nodes/auditor.py` + `nodes/narrative.py` — small patches. ~37 lines. Should reach ~87.5%.
6. `scrape/base.py` + `scrape/amazon.py` + `scrape/reddit_rss.py` + `scrape/github_trending.py` — scrape error paths. ~58 lines. Should reach ≥ 90%.

After each step run: `uv run pytest tests/unit/ --cov=aegis -q` to verify progress.
