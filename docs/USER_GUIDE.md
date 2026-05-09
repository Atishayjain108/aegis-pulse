# AEGIS Pulse — User Guide

> Day-to-day reference for the `aegis` CLI, the local dashboards,
> and routine operations. Written for engineers who are comfortable
> in a terminal but may not know every AEGIS detail yet.

---

## Table of Contents

1. [First-time setup](#1-first-time-setup)
2. [Daily startup](#2-daily-startup)
3. [All CLI commands — complete reference](#3-all-cli-commands--complete-reference)
4. [Scraping data from platforms](#4-scraping-data-from-platforms)
5. [Viewing ingested signals](#5-viewing-ingested-signals)
6. [Dashboards and web UIs](#6-dashboards-and-web-uis)
7. [Reports](#7-reports)
8. [Diagnostics and troubleshooting](#8-diagnostics-and-troubleshooting)
9. [Routine workflows](#9-routine-workflows)

---

## 1. First-time setup

Run these commands once, in order, after cloning the repo.

### 1a. Copy environment variables

```

cp .env.example .env
```

Open `.env` and confirm these two lines are present (they should be for the local stack):

```
AEGIS_PG_DSN=postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis
AEGIS_REDIS_URL=redis://localhost:6380/0
```

No other variables are required to run without API keys.

### 1b. Install Python dependencies

```bash
uv sync
```

Expected output: packages resolved and installed into `.venv/`. Takes 30–90 seconds the first time.

### 1c. Start the Docker stack

```bash
uv run aegis up
```

This starts 7 containers in the background:

| Container | Purpose | Port |
|---|---|---|
| `postgres` | TimescaleDB — stores all signals | 5433 |
| `redis` | Rate-limit cache and priority queue | 6380 |
| `minio` | Raw audit-trail Parquet objects | 9002 (S3), 9003 (console) |
| `prometheus` | Metrics collection | 9091 |
| `grafana` | Metric dashboards | 3001 |
| `jaeger` | Distributed traces | 16687 |
| `flaresolverr` | Cloudflare-bypass proxy (used by some adapters) | 8191 |

Expected output:
```
$ docker compose up -d
[+] Running 7/7
 ✔ Container aegis-pulse-postgres-1      Running
 ✔ Container aegis-pulse-redis-1         Running
...
Stack starting. Run `aegis status` in 30s to see health.
```

Wait 15–30 seconds, then run:

```bash
uv run aegis status
```

Expected output (all rows show `running` and `healthy`):
```
SERVICE                  STATE          HEALTH         PORTS
flaresolverr             running        healthy        8191->8191
grafana                  running        healthy        3001->3000
jaeger                   running        healthy        16687->16686
minio                    running        healthy        9002->9000,9003->9001
postgres                 running        healthy        5433->5432
prometheus               running        healthy        9091->9090
redis                    running        healthy        6380->6379
```

If any container shows `unhealthy`, run `uv run aegis down && uv run aegis up` to restart.

### 1d. Run the health check

```bash
uv run aegis doctor
```

Expected output — all checks should be `[OK]` except optional ones:
```
  [OK]      WSL2 runtime
  [OK]      Docker daemon
  [OK]      Python 3.12.x global
  [OK]      DNS resolution
  [WARN]    Required ports free         in-use: 5432 6379 ... (normal if you run other Docker stacks)
  [WARN]    GPU (optional)              no GPU detected — CPU-only mode (functional but slower)
  ✓ 17 checks passed  (2 warnings)
```

The two warnings are expected:
- Ports already in use = your other Docker services are running on the defaults; AEGIS uses different ports so there is no actual conflict
- No GPU = fine, everything works in CPU mode

### 1e. Apply database migrations

```bash
uv run aegis migrate
```

Expected output:
```
$ uv run alembic upgrade head
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade -> ..., initial schema
```

This creates all tables, TimescaleDB hypertables, indexes, and Row-Level Security policies. It is idempotent — safe to run again at any time.

### 1f. Verify end-to-end with a test scrape

```bash
uv run aegis scrape --source hacker-news --limit 5 --dry-run
```

Expected output — 5 JSON lines printed, no DB writes:
```json
{"platform": "hacker_news", "external_id": "43012345", "title": "Show HN: ...", "url": "https://..."}
{"platform": "hacker_news", "external_id": "43012346", "title": "Ask HN: ...", "url": "https://..."}
...
Done. Emitted 5 signals.
```

If this works, the entire pipeline is healthy.

---

## 2. Daily startup

```bash
uv run aegis up           # start containers (no-op if already running)
uv run aegis status       # confirm all 7 containers are healthy
uv run aegis doctor       # optional: run full health check
```

---

## 3. All CLI commands — complete reference

### `aegis up`

Bring up the Docker stack in detached (background) mode.

```bash
uv run aegis up
uv run aegis up --build          # rebuild local images first (rarely needed)
uv run aegis up --no-detach      # attach and stream logs (blocks terminal)
```

- **Idempotent** — safe to run even if containers are already running
- Containers restart in ~5 seconds if they were stopped
- Use `aegis status` afterwards to verify health

### `aegis down`

Stop all containers. **All data is preserved in named volumes.**

```bash
uv run aegis down
uv run aegis down --volumes      # ALSO delete volumes (asks for confirmation)
```

After `aegis down`, the next `aegis up` resumes from exactly where you left off.

### `aegis status`

Print a one-line health row per container.

```bash
uv run aegis status
```

Expected fields: `SERVICE`, `STATE` (running/exited), `HEALTH` (healthy/unhealthy/n/a), `PORTS`.

### `aegis doctor`

Run the bootstrap health checklist: WSL version, Docker daemon, Python version, DNS, disk space, ports, Playwright browsers, etc.

```bash
uv run aegis doctor
uv run aegis doctor --secrets    # fast check: only verify .env variables are set
```

All `[OK]` = ready to run. Fix any `[FAIL]` items before continuing.

### `aegis migrate`

Apply outstanding database migrations using Alembic.

```bash
uv run aegis migrate
```

Run this after `aegis up` on a fresh install, and whenever you pull new code that adds migrations. Always safe to re-run — already-applied migrations are skipped.

### `aegis scrape`

Run a source adapter and ingest signals into the database.

```bash
uv run aegis scrape --source <name> [OPTIONS]
```

Options:

| Option | Default | Description |
|---|---|---|
| `--source` | required | Adapter name (see table below) |
| `--limit N` | 50 | Stop after emitting N signals |
| `--dry-run` | off | Parse signals but skip DB insert; prints JSON to stdout |
| `--subreddit NAME` | — | Reddit / reddit-rss: which subreddit to scrape |
| `--query TEXT` | — | HackerNews, Google Trends, Nitter: search query |
| `--hashtag TEXT` | — | Instagram, TikTok: hashtag to scrape |
| `--country XX` | — | TikTok, Google Trends: 2-letter country code |

### `aegis signals tail`

Print the most recent rows from the `signals` table.

```bash
uv run aegis signals tail
uv run aegis signals tail --limit 50
uv run aegis signals tail --platform reddit
uv run aegis signals tail --platform hacker_news
```

Each row shows: `scraped_at  platform  title (truncated to 60 chars)`.

### `aegis report daily`

Print a summary of how many signals were ingested on a given day.

```bash
uv run aegis report daily                    # yesterday (UTC)
uv run aegis report daily --date 2026-05-02  # specific date
```

Expected output:
```
============================================================
AEGIS Pulse — daily report  2026-05-02 (UTC)
============================================================
Total signals: 20

By platform:
  hacker_news            10
  reddit                 10
```

Note: this report uses the `scraped_at` timestamp (when AEGIS fetched the signal), not the original post date.

### `aegis tail`

Stream the live Docker log output to your terminal. Press `Ctrl-C` to stop.

```bash
uv run aegis tail               # all containers
uv run aegis tail postgres      # single container
uv run aegis tail redis
```

### `aegis reset`

**Destructive.** Stops all containers and deletes all data volumes. You will be prompted to confirm.

```bash
uv run aegis reset
```

Use this only when you want a completely fresh database. After reset, run `aegis up` and `aegis migrate` again.

### `aegis support-bundle`

Collect a diagnostic ZIP file that is safe to share (all secrets are redacted).

```bash
uv run aegis support-bundle
uv run aegis support-bundle --out ~/aegis-bundle.zip
```

The bundle contains: container status, last 1000 log lines from each service, redacted environment variables, and version information.

---

## 4. Scraping data from platforms

### Available sources

| Source name | Platform | Requires API key? | ToS risk | Notes |
|---|---|---|---|---|
| `hacker-news` | Hacker News | No | GREEN | Algolia public API. Best first source to test. |
| `reddit-rss` | Reddit | No | GREEN | Reddit public JSON API (`/r/{sub}.json`). No login needed. |
| `github-trending` | GitHub Trending | No | GREEN | Parses github.com/trending HTML. |
| `google-trends` | Google Trends | No | GREEN | Uses `pytrends` library. |
| `tiktok` | TikTok | No | AMBER | TikTok Creative Center public JSON. |
| `pinterest` | Pinterest | No | AMBER | HTML scraping with browser fingerprint. |
| `amazon` | Amazon | No | AMBER | Playwright stealth browser. Slow (~60s). |
| `nitter` | Twitter/X | No | AMBER | Requires a working Nitter instance. Most public instances are offline; best-effort. |
| `reddit` | Reddit | Yes (PRAW) | GREEN | Official Reddit API. Set `AEGIS_REDDIT_CLIENT_ID` + `AEGIS_REDDIT_CLIENT_SECRET` in `.env`. |
| `youtube` | YouTube | Yes (Data API v3) | GREEN | Set `AEGIS_YOUTUBE_API_KEY` in `.env`. |
| `instagram` | Instagram | No (instaloader) | RED | High ban risk. Requires `allow_red_tos=True` (set automatically when you use `--source instagram` via CLI). |

### Examples

**Scrape HackerNews top stories and save to DB:**
```bash
uv run aegis scrape --source hacker-news --limit 30
```
Expected: ~2–5 seconds, "Done. Emitted 30 signals."

**Scrape Reddit front page (no API key needed):**
```bash
uv run aegis scrape --source reddit-rss --limit 25
```
Expected: <1 second (Reddit JSON API is fast), 25 signals inserted.

**Scrape a specific subreddit:**
```bash
uv run aegis scrape --source reddit-rss --subreddit MachineLearning --limit 20
```

**Scrape GitHub Trending:**
```bash
uv run aegis scrape --source github-trending --limit 25
```
Expected: ~2 seconds, up to 25 trending repos as signals.

**Preview without writing to DB (dry-run):**
```bash
uv run aegis scrape --source hacker-news --limit 5 --dry-run
```
Each signal is printed as a JSON line. Nothing is written to the database.

**Scrape Google Trends for a keyword:**
```bash
uv run aegis scrape --source google-trends --query "AI agents" --country US --limit 10
```

**Scrape TikTok trending (US):**
```bash
uv run aegis scrape --source tiktok --country US --limit 20
```

**Scrape Instagram hashtag (high ban risk — use sparingly):**
```bash
uv run aegis scrape --source instagram --hashtag "techstartup" --limit 10
```

### What happens during a scrape

1. The adapter opens an HTTP connection (or browser) to the target platform
2. It fetches and parses the raw data
3. Each item is converted to a `ProductSignal` with a content hash for deduplication
4. Signals are batched (50 at a time) and inserted into TimescaleDB via `COPY`
5. Duplicate signals (same content hash) are silently skipped via `ON CONFLICT DO NOTHING`
6. The CLI prints a count like `flushed 25 (total emitted: 25)`

---

## 5. Viewing ingested signals

### In the terminal

```bash
uv run aegis signals tail
```

Shows the 20 most-recent signals. Each line:
```
2026-05-02T17:38:08+00:00  reddit       Love them to the fullest while...
2026-05-02T17:19:55+00:00  hacker_news  Show HN: Building an autonomous...
```

Filter by platform:
```bash
uv run aegis signals tail --platform reddit
uv run aegis signals tail --platform hacker_news --limit 50
```

### Via daily report

```bash
uv run aegis report daily --date 2026-05-02
```

Shows total count and breakdown by platform for that UTC calendar day.

### Directly in the database

Connect to TimescaleDB if you want full SQL access:

```bash
# Connect from your terminal (requires psql installed on host or WSL)
psql postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis

# Or via docker exec (always available)
docker exec -it aegis-pulse-postgres-1 psql -U aegis_app -d aegis
```

Useful queries:

```sql
-- Count by platform today
SELECT platform, COUNT(*) FROM signals
WHERE scraped_at > NOW() - INTERVAL '24 hours'
GROUP BY platform ORDER BY count DESC;

-- See the last 10 signals with all fields
SELECT scraped_at, platform, tier, title, url
FROM signals ORDER BY scraped_at DESC LIMIT 10;

-- Count total signals
SELECT COUNT(*) FROM signals;
```

---

## 6. Dashboards and web UIs

Once `aegis up` is running, open these in your browser:

| URL | What it is | Login |
|---|---|---|
| http://localhost:3001 | **Grafana** — metric dashboards (ingest rates, errors, latency) | `admin` / `admin` (change in `.env` via `GF_SECURITY_ADMIN_PASSWORD`) |
| http://localhost:9091 | **Prometheus** — raw metric query UI. Useful to debug Grafana panels. | None |
| http://localhost:16687 | **Jaeger** — distributed traces. Search by service `aegis-pulse`. | None |
| http://localhost:9003 | **MinIO console** — object browser for raw audit Parquet files | `minioadmin` / value of `MINIO_ROOT_PASSWORD` in `.env` |

### Grafana walkthrough

1. Open http://localhost:3001
2. Log in with `admin` / `admin`
3. In the left sidebar, click **Dashboards** → **AEGIS Ingest**
4. The dashboard shows:
   - **Signals ingested per minute** (by platform)
   - **HTTP request rates** per adapter
   - **Error rates** and failed parse counts

### Jaeger trace walkthrough

1. Open http://localhost:16687
2. In the **Service** dropdown, select `aegis-pulse`
3. Click **Find Traces**
4. Each trace represents one `aegis scrape` run — click it to see timing per step

---

## 7. Reports

### Daily summary

```bash
uv run aegis report daily                    # yesterday
uv run aegis report daily --date 2026-05-02  # specific date (YYYY-MM-DD)
```

The report counts signals by their `scraped_at` timestamp (when AEGIS fetched them), not by the original post date.

---

## 8. Diagnostics and troubleshooting

### Quick triage

```
Problem                              → Fix
─────────────────────────────────────────────────────────────────────────
Stack won't start                   → aegis doctor; fix any [FAIL] items
A container is "unhealthy"          → aegis down && aegis up
"connection refused" on port 5433   → aegis status (is postgres running?)
aegis migrate fails                 → check aegis status; postgres must be healthy
scrape returns 0 signals            → try --dry-run; check error logs
scrape returns HTTP 403             → platform blocked the request; normal for some sources
"AEGIS-CLI-NNNN" error code         → look up in docs/errors/
```

### Common errors explained

**"PermissionError: bootstrap/aegis-doctor"**
Fixed in current version (the doctor command now auto-sets execute permission). If you see it, run:
```bash
chmod +x bootstrap/aegis-doctor
uv run aegis doctor
```

**"AEGIS-CLI-0002: neither docker compose nor docker-compose found"**
Docker is not running or not installed. On Windows with WSL2, open Docker Desktop and ensure the WSL2 backend is enabled.

**"AEGIS-CLI-0005: missing required secrets"**
Your `.env` file is missing `AEGIS_PG_DSN` or `AEGIS_REDIS_URL`. Copy `.env.example` to `.env`.

**scrape returns HTTP 403 from Reddit**
The `reddit-rss` adapter uses the correct headers. If you see 403 from the PRAW-based `reddit` adapter, your credentials in `.env` are wrong.

**`aegis signals tail` shows "(no signals)"**
Run a scrape first: `uv run aegis scrape --source hacker-news --limit 10`

**`aegis report daily` shows "Total signals: 0"**
The report uses yesterday's date by default. If you scraped today, run:
```bash
uv run aegis report daily --date $(date -u +%Y-%m-%d)
```

### Full diagnostics bundle

When in doubt, generate a support bundle and review its contents:

```bash
uv run aegis support-bundle
# writes aegis-support-YYYYMMDD-HHMMSS.zip in the current directory
# unzip and read compose-ps.txt and compose-logs.txt
```

---

## 9. Routine workflows

### First-ever scrape (after setup)

```bash
uv run aegis up
uv run aegis status          # wait until all 7 = healthy
uv run aegis migrate
uv run aegis scrape --source hacker-news --limit 50
uv run aegis signals tail    # confirm signals appear
```

### Morning startup

```bash
uv run aegis up                               # containers start or are already running
uv run aegis status                           # confirm all healthy
uv run aegis report daily                     # yesterday's summary
```

### Collect signals from multiple sources

```bash
# Run each source in sequence
uv run aegis scrape --source hacker-news   --limit 50
uv run aegis scrape --source reddit-rss    --limit 50
uv run aegis scrape --source github-trending --limit 25
uv run aegis scrape --source google-trends --query "AI" --limit 20
uv run aegis scrape --source tiktok        --country US --limit 30

# Then review what came in
uv run aegis signals tail --limit 100
uv run aegis report daily --date $(date -u +%Y-%m-%d)
```

### Watch live ingest

Open two terminals:
```bash
# Terminal 1: watch for new signals as they arrive
uv run aegis signals tail --limit 5   # re-run as needed

# Terminal 2: run a scrape
uv run aegis scrape --source reddit-rss --subreddit technology --limit 100
```

### Shutdown

```bash
uv run aegis down    # stops containers, keeps all data
```

### Full reset (start from scratch)

```bash
uv run aegis reset   # confirms prompt, deletes all data
uv run aegis up
uv run aegis migrate
```

### Adding a new scrape source

1. Create `src/aegis/scrape/sources/<name>.py` — subclass `SourceAdapter`
2. Register it in `cli/main.py` → `_ADAPTER_REGISTRY`
3. Add a `_build_adapter` case for it in `cli/main.py`
4. Test: `uv run aegis scrape --source <name> --limit 5 --dry-run`

---

## Appendix: Port reference

| Port | Service | Access |
|---|---|---|
| 5433 | TimescaleDB (Postgres) | `psql postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis` |
| 6380 | Redis | `redis-cli -p 6380` |
| 9002 | MinIO S3 API | Used internally by AEGIS |
| 9003 | MinIO Web Console | http://localhost:9003 |
| 9091 | Prometheus | http://localhost:9091 |
| 3001 | Grafana | http://localhost:3001 |
| 16687 | Jaeger UI | http://localhost:16687 |
| 8191 | FlareSolverr | Used internally by adapters (Cloudflare bypass) |
