# AEGIS Pulse — Phase 4: Execution & Alert System

Version: **0.4.0**

Phase 4 turns the deterministic verdicts produced by Phase 2 (multi-agent
graph) and the probabilistic forecasts produced by Phase 3 (predictive ML
core) into **durable, deduplicated, multi-channel alerts** with an
HTTP/SSE API and a live operator dashboard.

It enforces the same doctrine as the rest of AEGIS:

> **Heuristic-first; LLM as augmentation.** The verdict is computed
> deterministically from numeric features. Probabilistic models may
> reduce confidence or add detail — they **cannot flip a verdict**.

## What it ships

| Layer | Component | Purpose |
|---|---|---|
| Policy | `policy.composer` | Phase 2 + Phase 3 → single `Alert` |
| Policy | `policy.classifier` | Priority P0..P3 from features |
| Policy | `policy.deduper` | Content-addressable dedup over Redis (or local) |
| Risk | `risk.gates` | Margin floor, loss-prob ceiling, confidence floor |
| Sizing | `sizing.kelly` | Fractional-Kelly (0.25×) capital advisor |
| Persistence | `store.repository` | Idempotent `alerts` + `alert_outbox` + deliveries |
| Outbox | `outbox.writer` / `outbox.drainer` | At-least-once delivery, retries with jitter |
| Safety | `killswitch.switch` | Fail-closed Redis-backed global halt |
| Channels | `notifiers.*` | log, ntfy, telegram, discord, generic webhook |
| Wire-in | `bridge.phase2` / `bridge.phase3` | Duck-typed structural Protocols |
| Workers | `workers.drain_worker` / `workers.intake_worker` | Long-lived async loops |
| API | `api.app` | FastAPI: REST + SSE + dashboard mount |
| CLI | `cli.main` | `aegis-execute` Typer console script |

## Quickstart

```bash
# Inside a Phase 4-only checkout
pip install -e ".[serve,db,redis,prometheus,dev]"

# One-shot synthetic alert (no infra)
aegis-execute compose-demo --verdict ENTER --score 0.8 --confidence 0.7

# Serve the HTTP/SSE/dashboard
aegis-execute serve --host 127.0.0.1 --port 8200

# Drain the outbox (in another terminal)
aegis-execute drain --tenant <uuid> \
  --pg-dsn postgres://aegis:dev@localhost:5433/aegis \
  --redis-url redis://localhost:6380/0

# Killswitch ops
aegis-execute killswitch state
aegis-execute killswitch trip --reason "incident-117"
aegis-execute killswitch arm  --reason "all clear"
```

Dashboard: open `http://127.0.0.1:8200/dashboard/`. Set the tenant UUID
once; the page persists it in `localStorage`.

## Configuration

All settings come from environment variables under the `AEGIS_EXECUTE_`
prefix, plus the global `AEGIS_PG_DSN` / `AEGIS_REDIS_URL` shared with
Phase 1. See `INTEGRATION.md` for the wiring map.

| Var | Default | Purpose |
|---|---|---|
| `AEGIS_EXECUTE_MODE` | `advisory` | `advisory` only in v1 (no real execution) |
| `AEGIS_EXECUTE_API_HOST` | `127.0.0.1` | Bind host |
| `AEGIS_EXECUTE_API_PORT` | `8200` | Bind port |
| `AEGIS_EXECUTE_DRAIN_INTERVAL_S` | `1.0` | Outbox poll interval |
| `AEGIS_EXECUTE_DRAIN_BATCH` | `25` | Outbox batch size |
| `AEGIS_EXECUTE_NOTIFY_MAX_ATTEMPTS` | `5` | Per-channel retry ceiling |
| `AEGIS_EXECUTE_NOTIFY_TIMEOUT_S` | `8.0` | Per-call HTTP timeout |
| `AEGIS_EXECUTE_KILLSWITCH_KEY` | `aegis:execute:killswitch` | Redis key |
| `AEGIS_EXECUTE_HMAC_KEY` | *(empty)* | Generic-webhook HMAC; required to enable |
| `AEGIS_EXECUTE_NTFY_BASE_URL` | `https://ntfy.sh` | ntfy server |
| `AEGIS_EXECUTE_NTFY_TOPIC` | *(empty)* | Set to enable ntfy channel |
| `AEGIS_EXECUTE_TELEGRAM_TOKEN` | *(empty)* | Bot token |
| `AEGIS_EXECUTE_TELEGRAM_CHAT_ID` | *(empty)* | Chat ID |
| `AEGIS_EXECUTE_DISCORD_WEBHOOK_URL` | *(empty)* | Webhook URL |
| `AEGIS_EXECUTE_GENERIC_WEBHOOK_URL` | *(empty)* | Generic POST target |
| `AEGIS_EXECUTE_API_BEARER_TOKEN` | *(empty)* | Required bearer; empty = open |

Channels with empty config auto-disable — the system always boots, and
the `log` notifier is the always-available baseline.

## Running the tests

```bash
PYTHONPATH=src python -m pytest tests/ --cov=aegis.execute
```

155 tests, 85% line coverage, ~4 s on a laptop. Unit tests need zero
infrastructure; integration tests use an in-memory `FakeRepository` that
mirrors the asyncpg surface.

## Architecture detail

See `ARCHITECTURE.md` for the full design rationale, doctrine, data
flow, and integration contracts.
