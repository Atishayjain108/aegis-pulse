# AEGIS Pulse — Phase 4 (Execution & Alert System)

Version `0.4.0` · Python 3.12 · 155 tests · 85% line coverage

This is the **Phase 4** standalone distribution for AEGIS Pulse. It
converts the deterministic verdicts produced by Phase 2 (multi-agent
LangGraph) and the probabilistic forecasts produced by Phase 3
(Predictive Apex ML core) into durable, deduplicated, multi-channel
alerts with an HTTP/SSE API and an operator dashboard.

```
aegis-phase4/
├── README.md                          ← you are here
├── pyproject.toml                     ← installable: pip install -e ".[serve,db,redis,prometheus,dev]"
├── src/aegis/
│   ├── __init__.py                    ← namespace root
│   └── execute/                       ← Phase 4 package
│       ├── api/                       ← FastAPI app + 5 route modules
│       ├── bridge/                    ← Phase 2 + Phase 3 → ComposerInput
│       ├── bus/                       ← in-process SSE pub-sub
│       ├── cli/                       ← Typer `aegis-execute` console script
│       ├── dashboard/static/          ← single-file HTML dashboard
│       ├── killswitch/                ← fail-closed Redis switch
│       ├── notifiers/                 ← log / ntfy / telegram / discord / webhook
│       ├── outbox/                    ← writer + at-least-once drainer
│       ├── pipeline.py                ← single entry point
│       ├── policy/                    ← composer / classifier / deduper / throttle
│       ├── risk/                      ← gate chain (margin, loss-prob, confidence)
│       ├── schemas/                   ← Alert / Envelope / Intent / Delivery
│       ├── sizing/                    ← fractional-Kelly advisor
│       ├── store/                     ← AlertRepository (asyncpg)
│       ├── utils/                     ← hashing / hmac / backoff / time
│       └── workers/                   ← drain_worker / intake_worker
├── db/migrations/0003_execute.sql     ← Postgres + TimescaleDB schema
├── tests/
│   ├── unit/execute/                  ← 16 unit test modules
│   └── integration/execute/           ← 7 integration test modules + fakes
└── docs/
    ├── phase4/                        ← ARCHITECTURE / README / INTEGRATION / OPERATIONS
    └── errors/                        ← one Markdown per AEGIS-EXEC-NNNN code
```

## 30-second tour

```bash
# Tests (no infrastructure needed — fakes mimic Postgres / Redis)
PYTHONPATH=src python -m pytest tests/ -p no:cacheprovider

# Synthetic alert without any infrastructure
PYTHONPATH=src python -m aegis.execute.cli.main compose-demo \
  --verdict ENTER --score 0.85 --confidence 0.78 --p-breakout 0.90

# Serve the HTTP + SSE + dashboard
PYTHONPATH=src python -m aegis.execute.cli.main serve --port 8200
```

The dashboard is at `http://127.0.0.1:8200/dashboard/`. Set the tenant
UUID once — it persists in `localStorage`.

## What makes Phase 4 different

- **Heuristic-first doctrine**, same as the rest of AEGIS. Verdicts are
  deterministic functions of numeric features. The probabilistic and
  LLM-augmented inputs from Phase 2 and Phase 3 can only reduce
  confidence or add detail — they cannot flip a verdict.
- **Content-addressable alert IDs** — `sha256(tenant + trend + window +
  verdict + priority)` → identical input produces an identical alert ID,
  which becomes the idempotency key in the outbox.
- **At-least-once delivery via the outbox pattern.** A row exists in
  `alert_outbox` for every alert until each notifier has either
  succeeded or exhausted retries.
- **Fail-closed kill switch.** If the Redis backend is unreachable, the
  switch returns TRIPPED — alerts are never dispatched on uncertain
  state.
- **Structural typing of upstream contracts.** Phase 4 imports nothing
  from `aegis.agents` or `aegis.predict`. The bridges are
  `@runtime_checkable Protocol`s, so a Phase 4-only checkout builds,
  lints, and tests cleanly.

## Read next

- `docs/phase4/ARCHITECTURE.md` — design + doctrine + data flow
- `docs/phase4/INTEGRATION.md` — how to wire from Phase 2 / Phase 3
- `docs/phase4/OPERATIONS.md` — daily checks + incident runbook
- `docs/errors/README.md` — error code index

## Verification

```
$ PYTHONPATH=src python -m pytest tests/ --cov=aegis.execute -q
...155 passed in 3.98s
TOTAL    2266    349    85%
```
