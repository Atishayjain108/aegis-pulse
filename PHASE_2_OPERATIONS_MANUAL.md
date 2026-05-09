# AEGIS Pulse — Phase 2 Operations Manual

> **Status**: Operational  
> **Validated**: 2026-05-05  
> **Architecture**: Phase 0 (scrape) → Phase 1 (DB) → Phase 2 (10-node LangGraph intelligence)

---

## 0. Quick Start — The Full Loop in 5 Commands

This is everything you need to run AEGIS Pulse from scratch every day:

```bash
# Step 1: Start all services (Postgres, Redis, MinIO, Grafana, etc.)
uv run aegis up

# Step 2: Scrape signals from 4 working sources
uv run aegis scrape --source reddit-rss --subreddit MachineLearning --limit 50
uv run aegis scrape --source hacker-news --limit 50
uv run aegis scrape --source github-trending --limit 30
uv run aegis scrape --source amazon --limit 80

# Step 3: Check what was collected
uv run aegis signals tail --limit 20

# Step 4: Run the AI agent pipeline to score the trends
uv run aegis analyze --limit 30

# Step 5: See the verdict (PROCEED / HOLD / BLOCK + score for each agent)
# Output is printed directly to the terminal — no dashboard needed
```

**What "Done. Emitted N signals" means**: N items were successfully saved to the database.  
**What you're looking for**: `Verdict: PROCEED` with a high score (> 0.6) = potential arbitrage opportunity.

---

## 1. Visual Access Guide

All UIs are available on `localhost` while the Docker Compose stack is running (`docker compose up -d`).

| Service | URL | Default credentials |
|---------|-----|---------------------|
| **Grafana** (dashboards) | http://localhost:3001 | `admin` / `aegis_dev_admin_pw` |
| **Prometheus** (raw metrics) | http://localhost:9091 | none |
| **Jaeger** (distributed traces) | http://localhost:16687 | none |
| **MinIO Console** (object store) | http://localhost:9003 | `aegis-dev-key` / `aegis-dev-secret-please-change` |
| **MinIO API** (S3 endpoint) | http://localhost:9002 | same as above |
| **FlareSolverr** (Cloudflare bypass) | http://localhost:8191 | none |

### Database GUI — DBeaver / pgAdmin

- **Host**: `localhost`  **Port**: `5433`
- **Database**: `aegis`
- **App user** (read-write): `aegis_app` / `aegis_app_dev_pw`
- **Migrate user** (DDL): `aegis_migrate` / `aegis_migrate_dev_pw`
- **Driver**: PostgreSQL (TimescaleDB-compatible)

### Redis

- **URL**: `redis://localhost:6380/0`
- No authentication in dev. Use `redis-cli -p 6380` or RedisInsight.

---

## 2. SOP — Start / Stop / Restart

### Start the full stack

```bash
docker compose up -d          # start all 7 services
docker compose ps             # verify all are (healthy)
```

### Stop the full stack (preserve data volumes)

```bash
docker compose stop
```

### Wipe data and restart from scratch

```bash
docker compose down -v        # WARNING: destroys all DB + Redis + MinIO data
docker compose up -d
```

### Service-level restart

```bash
docker compose restart aegis-postgres
docker compose restart aegis-redis
docker compose restart aegis-minio
docker compose restart aegis-grafana
docker compose restart aegis-prometheus
docker compose restart aegis-jaeger
docker compose restart aegis-flaresolverr
```

### View live logs

```bash
docker compose logs -f aegis-postgres     # Postgres
docker compose logs -f aegis-redis        # Redis
docker compose logs -f                    # all services
```

### Run database migrations manually

```bash
uv run alembic upgrade head
```

---

## 3. Manual Pipeline Execution

### 3a. Phase 0 — Data Ingestion (Scrape)

**Working adapters** (no API key required, as of 2026-05-05):

```bash
# Reddit RSS — fastest, most reliable
uv run aegis scrape --source reddit-rss --subreddit MachineLearning --limit 50
uv run aegis scrape --source reddit-rss --subreddit Entrepreneur --limit 50
uv run aegis scrape --source reddit-rss --subreddit SideProject --limit 50

# Hacker News — tech trends
uv run aegis scrape --source hacker-news --limit 50

# GitHub Trending — developer trends
uv run aegis scrape --source github-trending --limit 30

# Amazon Bestsellers — commerce/product trends (8 categories, ~30 items each)
uv run aegis scrape --source amazon --limit 80
```

**Broken adapters** (do not use — will return 0 signals):

| Adapter | Why broken |
|---------|-----------|
| `tiktok` | Creative Center API requires TikTok Ads auth (HTTP 200, code 40101) |
| `pinterest` | Unofficial search API returns 403 Forbidden |
| `nitter` | All public Nitter instances are dead or 403 |

**Adapters requiring API keys** (set in `.env` first):

```bash
# Reddit via PRAW (requires AEGIS_REDDIT_CLIENT_ID + AEGIS_REDDIT_CLIENT_SECRET)
uv run aegis scrape --source reddit --subreddit Entrepreneur --limit 50

# YouTube (requires AEGIS_YOUTUBE_API_KEY)
uv run aegis scrape --source youtube --query "trending products 2026" --limit 20
```

### 3b. Phase 1 — Inspect signals in DB

```bash
# Signal count via psql
docker exec aegis-postgres psql -U aegis_app -d aegis \
  -c "SET app.current_tenant='00000000-0000-0000-0000-000000000001'; SELECT count(*) FROM signals;"

# Recent signals
docker exec aegis-postgres psql -U aegis_app -d aegis \
  -c "SET app.current_tenant='00000000-0000-0000-0000-000000000001';
      SELECT platform, title, ts FROM signals ORDER BY ts DESC LIMIT 10;"
```

### 3c. Phase 2 — Run the Agent Intelligence Pipeline

Create a small script or run inline:

```python
import asyncio, os
from uuid import UUID

os.environ.setdefault("AEGIS_PG_DSN", "postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis")
os.environ.setdefault("AEGIS_DISABLE_OLLAMA", "1")   # heuristic path; remove to use Ollama

TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")

async def main():
    from aegis.db.pool import PgConfig, PgPool, set_shared_pool
    from aegis.db import signals as db_signals
    from aegis.agents.schemas import TrendCandidate
    from aegis.agents.runner import run_trend

    pool = PgPool(PgConfig(dsn=os.environ["AEGIS_PG_DSN"], min_size=1, max_size=2))
    await pool.start()
    set_shared_pool(pool)

    rows = await db_signals.fetch_recent_signals(pool, tenant_id=TENANT_ID, limit=20)
    signal_ids = [str(r["signal_id"]) for r in rows[:5]]

    candidate = TrendCandidate(
        trend_id="manual-run-001",
        title="Your trend title here",
        summary="Brief summary of the trend cluster.",
        velocity_1h=50.0,
        velocity_6h=200.0,
        velocity_24h=600.0,
        sentiment=0.4,
        commercial_intent=0.5,
        novelty=0.6,
        coordination_risk=0.1,
        signal_count=len(rows),
        unique_authors=min(len(rows), 18),
        platforms=["reddit"],
        sample_signal_ids=signal_ids,
        representative_text=rows[0].get("title", "") if rows else "sample text",
    )

    result = await run_trend(candidate)
    print(f"Verdict: {result.final_verdict}  Score: {result.final_score:.3f}  Halt: {result.halt_reason}")
    for dec in result.decisions:
        print(f"  {dec.agent:15} → {dec.verdict.value} (conf={dec.confidence:.2f})")

    await pool.close()

asyncio.run(main())
```

Run with: `uv run python your_script.py`

### 3d. LLM Provider Setup (optional — enhances narrative reasoning)

Set one or more provider keys in `.env` then restart:

```bash
# Groq (free tier, fast)
GROQ_API_KEY=gsk_...

# OpenRouter (multi-model, pay-per-token)
OPENROUTER_API_KEY=sk-or-...

# Gemini (Google AI, free tier)
GEMINI_API_KEY=AI...

# Local Ollama (must be running on http://localhost:11434)
# Remove AEGIS_DISABLE_OLLAMA=1 from .env (or set to 0)
```

---

## 4. Health Checklist (run daily in production)

```bash
# 1. All containers healthy?
docker compose ps

# 2. Postgres responsive?
docker exec aegis-postgres pg_isready -U aegis_app -d aegis

# 3. Signal count growing?
docker exec aegis-postgres psql -U aegis_app -d aegis \
  -c "SET app.current_tenant='00000000-0000-0000-0000-000000000001';
      SELECT date_trunc('hour', ts), count(*) FROM signals
      GROUP BY 1 ORDER BY 1 DESC LIMIT 24;"

# 4. Redis reachable?
redis-cli -p 6380 ping

# 5. Unit tests passing?
uv run pytest tests/unit/ -q --no-header

# 6. Coverage above floor?
uv run pytest tests/unit/ --cov=aegis --cov-fail-under=78 -q --no-header

# 7. Agent pipeline smoke test (heuristic path, ~2s)
uv run python -c "
import asyncio, os
os.environ['AEGIS_DISABLE_OLLAMA']='1'
from aegis.agents.schemas import TrendCandidate
from aegis.agents.runner import run_trend
c = TrendCandidate(
    trend_id='health-check', title='Health check trend', summary='Smoke test.',
    velocity_1h=60.0, velocity_6h=240.0, velocity_24h=720.0,
    sentiment=0.5, commercial_intent=0.5, novelty=0.5, coordination_risk=0.05,
    signal_count=10, unique_authors=8, platforms=['reddit'],
    sample_signal_ids=[], representative_text='smoke test',
)
r = asyncio.run(run_trend(c))
print(f'Pipeline: {r.halt_reason} | {r.final_verdict} | {r.final_score:.3f}')
"
```

---

## 5. Key File Locations

| Component | Path |
|-----------|------|
| Environment config | `.env` (copy from `.env.example`) |
| Docker Compose | `docker-compose.yml` |
| DB migration SQL | `db/migrations/0001_init.sql` |
| Alembic config | `alembic.ini`, `alembic/` |
| Package entrypoint | `src/aegis/__init__.py` |
| Phase 2 agent nodes | `src/aegis/agents/nodes/` |
| LLM router | `src/aegis/agents/llm/router.py` |
| Agent runner | `src/aegis/agents/runner.py` |
| Prompt templates | `src/aegis/agents/prompts/*.jinja2` |
| ChromaDB store | `src/aegis/agents/memory/chroma_store.py` |
| Redis Streams bus | `src/aegis/agents/messaging/streams.py` |
| Unit tests | `tests/unit/` |
| Agent tests | `tests/unit/agents/` |
| Prometheus config | `config/prometheus.yml` |
| Grafana dashboards | `config/grafana/` |

---

## 6. Architecture Summary

```
User / Cron
    │
    ▼
[Phase 0] aegis scrape --source <adapter>
    │   ► fetches raw signals from Reddit, HN, GitHub, etc.
    │   ► writes ProductSignal rows to Postgres (signals table)
    │
    ▼
[Phase 1] Postgres (TimescaleDB)
    │   ► hypertables: signals, media, velocity_snapshots, prediction_outcomes
    │   ► RLS per tenant; continuous aggregates for hourly/daily velocity
    │   ► pgvector HNSW indexes for semantic search
    │
    ▼
[Phase 2] LangGraph 10-node pipeline
    │
    ├─► scout          — velocity threshold gate (blocks low-signal noise)
    ├─► geo_arbitrage  — regional pricing + sentiment cross-geography score
    ├─► narrative      — narrative coherence + framing analysis
    ├─► historian      — ChromaDB analogue lookup (past similar trends)
    ├─► sourcer        — source diversity + tier quality score
    ├─► auditor        — data completeness + freshness audit
    ├─► sentinel       — coordination / astroturfing detection
    ├─► compliance     — TOS risk + legal exposure check
    ├─► red_team       — adversarial stress test of the thesis
    ├─► hedge          — risk-adjusted expected-value calculation
    └─► finalize       — supervisor: aggregate → final_verdict / score / priority

Output: GraphResult (verdict: proceed / hold / block, score, confidence, decisions[])
```

### Verdict meanings

| Verdict | Meaning |
|---------|---------|
| `proceed` | High-confidence arbitrage opportunity; act now |
| `hold` | Moderate signal; monitor and re-evaluate |
| `block` | Low signal, TOS risk, or coordination detected; do not act |

### Priority levels

| Priority | Label | Meaning |
|----------|-------|---------|
| P1 | `URGENT` | Act within hours |
| P2 | `HIGH` | Act within 24h |
| P3 | `HOUSEKEEPING` | Monitor; no immediate action |
| P4 | `LOW` | Ignore unless pattern repeats |

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `Done. Emitted 0 signals.` for tiktok/pinterest/nitter | These adapters are broken (external API changes) | Use working adapters: reddit-rss, hacker-news, github-trending, amazon |
| `tiktok.auth_required code=40101` | TikTok Creative Center now requires login | No fix without TikTok Ads account; adapter is disabled for now |
| `pinterest.fetch.blocked status=403` | Pinterest unofficial API blocks unauthenticated requests | No fix without session cookie; adapter is disabled for now |
| `nitter.all_instances_failed` | All public Nitter instances are dead | No working Nitter instances exist; use reddit-rss instead |
| `content_hash mismatch` warnings during scrape | Signal fields mutated after hash computed (pre-existing) | Safe to ignore — signal is skipped, not corrupted |
| `llm.no_providers_available` log | No LLM API keys configured | Set `GROQ_API_KEY` in `.env` or leave for heuristic-only path (fully functional) |
| `scout_below_threshold` halt | Velocity too low for the trend | Normal for low-engagement content; adjust thresholds in `nodes/scout.py` |
| `ModuleNotFoundError: langgraph` | Optional dep not installed | `uv add "langgraph>=0.2"` |
| `ModuleNotFoundError: chromadb` | Optional memory dep | `uv add "chromadb>=0.4" "sentence-transformers>=2.2"` |
| Port conflict on 5433/6380/9002 | Other services using same ports | Adjust `docker-compose.yml` port mappings |
| Coverage below 78% | New code without tests | Add unit tests; run `uv run pytest --cov=aegis --cov-report=term-missing` |
| Logs look like unreadable JSON | Running in non-TTY / WSL / piped output | Normal — structlog uses JSON for machine parsing; run in a real terminal for colored output |
