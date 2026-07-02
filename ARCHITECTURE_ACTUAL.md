# AEGIS Pulse — ARCHITECTURE (ACTUAL, verified 2026-07-02)

Audit branch: `audit/full-system-20260702` (from `audit-remediation-2026-06` @ c22bd12).
Everything below was verified by running commands against the live repo/stack on 2026-07-02.
Where this contradicts CLAUDE.md or prior docs, this file wins.

> **Working-tree caveat**: 242 files are modified-but-uncommitted from the prior
> remediation session. This audit covers the working tree (the state actually
> running in the containers), not HEAD.

## 1. Repo layout (actual)

| Path | What it actually is |
|---|---|
| `src/aegis/` | Main package, `__version__ = "0.3.0"`, 30 subpackages. Includes packages NOT in CLAUDE.md's layout section: `api/`, `comply/`, `execute/`, `execution_intel/`, `intelligence/`, `memory/`, `mentor/`, `scheduler/`, `schemas/`, `trust/` |
| `aegis-phase4/` | uv workspace member (execute/alerts + Phase 6 capital) |
| `aegis-harden/` | uv workspace member (anti-bot hardening) |
| `aegis-phase12/` | uv workspace member (security/Vault/RBAC) |
| `aegis-phase13/` | uv workspace member (testing helpers) |
| `aegis-phase15/` | standalone module, own venv (DR orchestrator) |
| `archive/` | old phase snapshots (3.0 MB, gitignored, untracked — OK) |
| `db/migrations/` | **27 SQL migrations** (0001 → 0027_commerce_api_platforms) — CLAUDE.md's "0001–0004" note is stale |
| `dbt/` | dbt models/macros (present; not described in CLAUDE.md) |
| `tests/` | unit + integration + bench + eval + fixtures + load |
| `.github/workflows/` | **one workflow only**: `integration.yml` |

Total Python LOC across all source trees: **116,454**.

## 2. Scraper adapters (actual)

- **51 adapter files** in `src/aegis/scrape/sources/`.
- **37 registered** in the swarm registry (`src/aegis/scrape/swarm.py:72`).
- **10 registered** in the CLI registry (`src/aegis/cli/main.py:388`) — CLI and swarm are separate registries.
- Files present but NOT swarm-registered: `ajio.py`, `meesho.py`, `nykaa.py`, `indiamart.py` (the 2026-06-24 "dead four" removal — files remain as dead-code candidates), plus known-broken `tiktok.py`, `pinterest.py`, `nitter.py`, key-gated `reddit.py`, `youtube.py`, `instagram.py`, and CLI-only `reddit_rss.py`, `hacker_news.py` variants.
- Newest adapters (Jun 24): `bestbuy.py`, `ebay_browse.py`, `etsy.py`, `amazon_in.py`, `flipkart.py`, `myntra.py`.
- **Live-site validity per adapter was NOT verified in this session** (Phase 5 work).

## 3. Dependencies

- Root `pyproject.toml`: **37 direct deps — 28 pinned (`==`), 9 bounded-floating** (`>=,<`). All floating deps have upper bounds. `uv.lock` present → builds are reproducible regardless of floating specifiers.
- `requires-python = ">=3.12,<3.13"` — hard 3.12 constraint.
- Security-pinned (do not downgrade): `orjson==3.11.9`, `pyjwt==2.13.0`, `python-dotenv==1.2.2`.
- 5 more `pyproject.toml` files in workspace members; 4 stale ones under `archive/` (untracked).
- Full pip-audit / CVE scan deferred to Phase 2.

## 4. Docker stack (actual — richer than CLAUDE.md's "16-service" claim)

**19 services** in `docker-compose.yml`; 17 containers were up & healthy at audit time.

| Service | Image | Host port |
|---|---|---|
| postgres | timescale/timescaledb-ha:pg16 | 127.0.0.1:5433 |
| redis | redis:7.4-alpine (AOF on, maxmemory 1gb **allkeys-lru**) | 127.0.0.1:6380 |
| minio | minio/minio:**latest** | 127.0.0.1:9002/9003 |
| flaresolverr | ghcr.io/flaresolverr/flaresolverr:**latest** | 127.0.0.1:8191 |
| predict | aegis-predict:latest (built) | 127.0.0.1:8100 |
| execute-api | aegis-execute-api:latest (built) | 127.0.0.1:8200 |
| execute-drain | aegis-execute-drain:latest (built) | — |
| **autonomous** | aegis-predict:latest (shares predict image; runs `aegis autonomous run`, 15 scheduler jobs) | — |
| dashboard | aegis-dashboard:latest (built) | 127.0.0.1:8300 |
| prometheus | prom/prometheus:v2.54.1 | 127.0.0.1:9091 |
| grafana | grafana/grafana:11.2.2 | 127.0.0.1:3001 |
| jaeger | jaegertracing/all-in-one:1.62.0 | 127.0.0.1:16687 (+4319/4320 OTLP) |
| loki | grafana/loki:3.1.0 | 127.0.0.1:3100 |
| promtail | grafana/promtail:3.1.0 | — |
| prefect | prefecthq/prefect:**3-latest** | 127.0.0.1:4200 |
| ollama | ollama/ollama:0.4.7 | 127.0.0.1:11434 |
| ollama-init | curlimages/curl:8.10.1 (one-shot) | — |
| litellm | ghcr.io/berriai/litellm:main-v1.52.9 (profile) | 127.0.0.1:8080 |
| **langfuse** | (not in CLAUDE.md) | 127.0.0.1:3002 |

Posture facts: every published port binds to **127.0.0.1 only** (good). 19 `restart:` policies, 16 healthchecks, 34 resource-limit entries. 3 images float on `latest`/`3-latest` tags (minio, flaresolverr, prefect) — non-reproducible pulls.

## 5. Configuration

- `.env.example`: 179 lines, ~154 documented vars. Auto-generated from pydantic-settings — but commerce adapter credentials (eBay/BestBuy/Etsy) bypass this layer and are documented separately in `docs/FREE_COMMERCE_SETUP.md`.
- 13+ pydantic-settings classes with distinct prefixes: `AEGIS_`, `AEGIS_MINIO_`, `AEGIS_SCRAPE_`, `AEGIS_REDDIT_`, `AEGIS_YOUTUBE_`, `AEGIS_ALERT_`, `AEGIS_COMPLY_` (×2 — both `compliance/config.py` and `comply/settings.py` claim this prefix), `AEGIS_MENTOR_`, `AEGIS_BACKUP_`, `AEGIS_DATALAKE_`, `AEGIS_EVOLVE_`, plus workspace members (`AEGIS_SEC_`, `AEGIS_EXECUTE_`, `AEGIS_DR_`).

## 6. CI/CD & tests (actual)

- **CI: one lane only** — `.github/workflows/integration.yml` (ephemeral TimescaleDB+Redis, integration suite + invariants). Its own header references a "default `ci` workflow" for unit tests + ruff **which does not exist**. No pre-commit config. Nothing runs ruff/unit tests automatically on push/PR.
- 290 test files across all trees. Coverage floor configured at 78% (`--cov-fail-under=78`).
- Real suite results + measured coverage: see AEGIS_AUDIT_REPORT.md Phase 1.

## 7. Corrections to prior descriptions

1. CLAUDE.md package layout omits ~10 existing subpackages (see §1) — stale.
2. CLAUDE.md says migrations = 0001–0004; actual = 27 files through 0027.
3. CLAUDE.md says "16-service dev stack"; actual = 19 services incl. `autonomous` and `langfuse`.
4. Prior session observation "Docker binary inaccessible in harness" (obs 4832) is **wrong today**: Docker 29.5.3 + Compose v5.1.4 work from this harness.
5. "30+ adapters" claims: 51 files / 37 swarm-registered / 10 CLI-registered — three different numbers depending on what you count.
