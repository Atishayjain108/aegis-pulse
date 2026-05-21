# Phase 4 — Execution & Alert System

**Version**: 0.4.0
**Status**: Production-grade
**Last verified**: 2026-05-15

---

## 1. Purpose

Phase 4 is the **action layer** of AEGIS Pulse. It takes upstream verdicts from
Phase 2 (`GraphResult`) and Phase 3 (`InferenceResult`) and turns them into:

1. **Structured alerts** with priority + confidence + audit metadata.
2. **Multi-channel notifications** (ntfy, Telegram, Discord, webhook, log).
3. **Position-sized recommendations** using fractional-Kelly (read-only by default).
4. **Kill-switch-gated execution intents** (NEVER auto-executes by default).
5. **A read-only operator dashboard** (FastAPI + SSE) for monitoring + manual gating.

It is the first phase that touches the outside world. Every decision is logged,
signed, deduplicated, and replayable.

---

## 2. Doctrine (carried forward from Phases 2 & 3)

> **Heuristic first. Deterministic floor. LLM/ML augments but never flips.**

Phase 4 inherits and extends this:

- **Deterministic alert decisioning**: an alert is emitted from numeric features
  alone (verdict + score + confidence + halt_reason). LLM-generated narrative
  is appended as `summary_text` only — it cannot change the **fact** of the alert.
- **Idempotency**: every alert has a content-addressed `alert_id` (SHA-256 of
  `(tenant_id, trend_id, decision_window, verdict, priority)`). Retries are
  free of duplicate side-effects.
- **At-least-once delivery via outbox**: alerts persist to Postgres *before*
  notification is attempted. A separate worker drains the outbox. Crashes
  cannot lose alerts.
- **Graceful degradation**: with zero notification channels configured, alerts
  are persisted + logged + emitted on SSE. The system is fully functional.
- **Kill-switch is global, atomic, and observable**: a single Redis key
  (`aegis:execute:kill_switch`) halts all dispatch within < 100 ms. Trip-state
  is published on SSE so the dashboard updates immediately.
- **No auto-execution in v1**: `EXECUTION_MODE=advisory` is the only supported
  value. The execution-intent path emits structured intents to a separate
  outbox table for future human or Phase 6 consumption — but never calls a
  storefront, payment, or supplier API in this phase.

---

## 3. Inputs

### 3.1 Phase 2 → Phase 4
- `GraphResult` (Pydantic v2 frozen): `final_verdict`, `final_score`,
  `final_confidence`, `final_priority`, `halt_reason`, `decisions`,
  `blocked_by`, `trend_id`.
- Bridged via `aegis.execute.bridge.phase2.from_graph_result()`.

### 3.2 Phase 3 → Phase 4
- `InferenceResult` (from `aegis.predict.inference.InferenceRunner.run()`):
  contains `PredictionBundle` (multi-horizon `p_breakout`, `p_decline`,
  `p_saturation`, `expected_margin`, calibrated confidence) +
  `attribution` + `policy_action`.
- Bridged via `aegis.execute.bridge.phase3.from_inference_result()`.

### 3.3 Joint composition
The **Alert Composer** (`aegis.execute.policy.composer`) fuses both:
- Phase 2 supplies *narrative + governance* (compliance, hedge, redteam vetoes).
- Phase 3 supplies *quantitative* (forecasts, expected margin, conformal CIs).
- If Phase 3 is unavailable (no predictions for this trend), Phase 2 alone
  produces a `degraded=true` alert. The reverse is also supported.

---

## 4. Components

```
src/aegis/execute/
  __init__.py          — package root, version 0.4.0
  config.py            — ExecuteSettings (extends aegis.config.Settings)
  constants.py         — magic numbers + named constants
  errors.py            — AEGIS-EXEC-0001..0030 typed error codes
  schemas/             — Pydantic v2 frozen models
    alert.py           — Alert, AlertEnvelope, AlertOutboxRow
    intent.py          — ExecutionIntent (read-only, for Phase 6)
    notification.py    — NotificationResult, ChannelConfig
    dashboard.py       — DashboardSnapshot, MetricCard, SSEEvent
  policy/              — deterministic decisioning
    composer.py        — fuse GraphResult + InferenceResult → Alert
    classifier.py      — priority classifier (P0..P3)
    deduper.py         — content-hash idempotency
    throttle.py        — rate-limit per (tenant, channel)
  sizing/              — fractional-Kelly position sizing (advisory)
    kelly.py           — KellyAdvisor (read-only sizing, never executes)
  risk/                — risk gates
    gates.py           — RiskGate chain (margin, confidence, blocklists)
  killswitch/          — global emergency halt
    switch.py          — KillSwitch (Redis-backed, fail-open=False)
  outbox/              — at-least-once delivery
    writer.py          — persist alerts pre-dispatch
    drainer.py         — async worker draining outbox → notifiers
  store/               — Postgres persistence
    repository.py      — AlertRepository (asyncpg)
    migrations/        — 0003_execute.sql
  notifiers/           — pluggable notification channels
    base.py            — Notifier ABC + ChannelRegistry
    log.py             — LogNotifier (always available, default)
    ntfy.py            — NtfyNotifier (free push)
    telegram.py        — TelegramNotifier
    discord.py         — DiscordNotifier (webhook)
    webhook.py         — GenericWebhookNotifier (HMAC-signed)
  workers/             — long-running async runners
    drain_worker.py    — outbox drainer
    intake_worker.py   — consumes Phase 2/3 results from Redis stream
  api/                 — FastAPI app (read-only operator UI)
    app.py             — FastAPI factory
    routes/
      alerts.py        — GET /alerts, GET /alerts/{id}, POST /alerts/{id}/ack
      health.py        — /healthz, /readyz, /metrics
      stream.py        — /stream (Server-Sent Events for live updates)
      killswitch.py    — GET/POST /killswitch (operator gate)
    middleware.py      — request ID, timing, structured log
    auth.py            — optional HMAC-bearer middleware
  dashboard/           — minimal vanilla-JS dashboard (no React build)
    static/index.html  — single-file SSE dashboard
  cli/                 — Typer subcommands (`aegis execute …`)
    main.py            — `serve`, `drain`, `replay`, `tail`, `killswitch`
  bridge/              — Phase 2 + Phase 3 adapters
    phase2.py          — GraphResult → Alert input
    phase3.py          — InferenceResult → Alert input
  utils/
    hashing.py         — content-addressable IDs
    hmac_signer.py     — HMAC-SHA256 helpers
    time.py            — UTC clock + monotonic safety
    backoff.py         — decorrelated jitter retry
```

---

## 5. Data flow

```
[Phase 2 runner.py]   [Phase 3 InferenceRunner]
       │                       │
       └────────┬──────────────┘
                ▼
   ┌────────────────────────────┐
   │  policy.composer           │  (deterministic merge)
   │  → Alert object            │
   └────────────────────────────┘
                ▼
   ┌────────────────────────────┐
   │  risk.gates (chain)        │  (margin / confidence / blocklist / killswitch)
   │  → Alert | Blocked         │
   └────────────────────────────┘
                ▼
   ┌────────────────────────────┐
   │  policy.deduper            │  (content-hash idempotency in Redis)
   │  → emit? skip?             │
   └────────────────────────────┘
                ▼
   ┌────────────────────────────┐
   │  outbox.writer             │  (Postgres INSERT … ON CONFLICT)
   └────────────────────────────┘
                ▼
   ┌────────────────────────────┐
   │  outbox.drainer (worker)   │  (async; selects pending; fan-out)
   └────────────────────────────┘
        ▼          ▼         ▼
   [ntfy]    [telegram]  [webhook]  …
                ▼
   ┌────────────────────────────┐
   │  api.routes.stream (SSE)   │  → live dashboard
   └────────────────────────────┘
```

---

## 6. Database schema additions

See `db/migrations/0003_execute.sql`. New tables:

- `alerts` (TimescaleDB hypertable on `created_at`, 1-day chunks)
- `alert_outbox` (delivery queue + status)
- `alert_deliveries` (per-channel attempt log, immutable)
- `execution_intents` (read-only Phase 6 handoff)
- `killswitch_audit` (every toggle, who/when/why)

All tables enforce **RLS keyed on `app.current_tenant`** to match Phases 1–3.

---

## 7. Configuration

`ExecuteSettings` (extends `aegis.config.Settings`):

| Env var | Default | Purpose |
|---|---|---|
| `AEGIS_EXECUTE_MODE` | `advisory` | `advisory` (only mode supported in v1) |
| `AEGIS_EXECUTE_API_HOST` | `127.0.0.1` | FastAPI bind host |
| `AEGIS_EXECUTE_API_PORT` | `8200` | FastAPI bind port |
| `AEGIS_EXECUTE_DRAIN_INTERVAL_S` | `1.0` | outbox poll interval |
| `AEGIS_EXECUTE_DRAIN_BATCH` | `25` | rows per drain cycle |
| `AEGIS_EXECUTE_KILLSWITCH_KEY` | `aegis:execute:kill_switch` | Redis key |
| `AEGIS_EXECUTE_DEDUP_TTL_S` | `3600` | content-hash TTL |
| `AEGIS_EXECUTE_NOTIFY_TIMEOUT_S` | `5.0` | per-channel timeout |
| `AEGIS_EXECUTE_NOTIFY_MAX_ATTEMPTS` | `5` | retry ceiling |
| `AEGIS_EXECUTE_HMAC_KEY` | *(required for webhook)* | shared signing secret |
| `AEGIS_EXECUTE_NTFY_TOPIC` | `(empty → channel disabled)` | ntfy topic |
| `AEGIS_EXECUTE_NTFY_BASE_URL` | `https://ntfy.sh` | ntfy server |
| `AEGIS_EXECUTE_TELEGRAM_TOKEN` | *(empty → disabled)* | bot token |
| `AEGIS_EXECUTE_TELEGRAM_CHAT_ID` | *(empty → disabled)* | chat ID |
| `AEGIS_EXECUTE_DISCORD_WEBHOOK_URL` | *(empty → disabled)* | webhook URL |
| `AEGIS_EXECUTE_GENERIC_WEBHOOK_URL` | *(empty → disabled)* | webhook URL |

**Channel auto-discovery rule**: if a channel's required env vars are unset,
the channel is silently disabled (logged once at startup, never errors).
The system is fully functional with zero channels configured (LogNotifier
is always-on).

---

## 8. Error model

Typed error codes `AEGIS-EXEC-0001..0030`. Each has:
- A code (`AEGIS-EXEC-0007`).
- A human message.
- A `retryable: bool` flag.
- A `docs_path` (`docs/errors/AEGIS-EXEC-0007.md`).

Every external call uses `resilient_call()` (functional wrapper from
`aegis.predict.resilience`, reused — no duplication).

---

## 9. Observability

- **Prometheus metrics** (namespace `aegis_execute_*`):
  - `alerts_total{verdict, priority}`
  - `alerts_blocked_total{reason}`
  - `outbox_pending_gauge`
  - `delivery_attempts_total{channel, status}`
  - `delivery_latency_seconds{channel}` (histogram)
  - `killswitch_state` (gauge: 0 or 1)
  - `dedup_hits_total`
- **Structured logs** (structlog JSON): every alert carries `trend_id`,
  `alert_id`, `correlation_id` (propagated from upstream).
- **SSE topic `aegis.execute.events`** broadcasts every state change for the
  dashboard.

---

## 10. Testing

- **Unit**: each module independently tested with mocks.
- **Integration**: composer + risk + outbox + drainer + a stub notifier
  exercised end-to-end against a real Redis + Postgres (testcontainers).
- **Property tests**: dedupe + idempotency + ordering invariants.
- **Failure injection**: notifier raises → outbox row stays pending → retry
  succeeds → row marked delivered. Proven by test.
- **Coverage floor**: 85% line (Phase 4 stricter than 78% global).

---

## 11. Integration contracts (verified)

| Producer | Consumer | Contract |
|---|---|---|
| `aegis.agents.runner.run_trend()` | `aegis.execute.bridge.phase2` | `GraphResult` Pydantic v2 frozen |
| `aegis.predict.inference.InferenceRunner.run()` | `aegis.execute.bridge.phase3` | `InferenceResult` |
| `aegis.execute.workers.drain_worker` | `aegis.execute.notifiers.*` | `Notifier.send(alert)` |
| `aegis.execute.api.routes.stream` | dashboard | SSE event stream |
| `aegis.execute.cli` | operator | Typer-based CLI |

All shared types live in `aegis.execute.schemas.*` — never imported from
Phases 2/3 directly; bridges adapt them. This prevents cyclic imports.

---

## 12. What Phase 4 explicitly does NOT do

- Place orders on any storefront. (Phase 6.)
- Call payment APIs. (Phase 6.)
- Modify supplier configurations. (Phase 6.)
- Trade securities. Ever.
- Send marketing emails or customer-facing messages.
- Decide *whether* to enter a position (Phase 3 RL policy advises;
  Phase 4 surfaces the advice).

Phase 4's only side effects: write to Postgres, publish to Redis stream,
send notification messages to operator channels, expose a read-only HTTP API.
