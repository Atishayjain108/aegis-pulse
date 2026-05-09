# AEGIS Pulse

> Autonomous market-arbitrage intelligence engine.
> Ingests signals from 30+ social, commerce, and search platforms;
> derives velocity and trend features; routes actionable opportunities
> to downstream agents.

---

## Status

| Phase | Description | Status |
| --- | --- | --- |
| 0 | Environment bootstrap (Windows + WSL) | ✅ Complete |
| 1 | Foundation (schemas, persistence, cache, observability, first adapters) | ✅ Complete |
| 2 | Multi-agent intelligence (LangGraph) | ⏳ Planned |
| 3 | Predictive apex (PatchTST + GNN + RL) | ⏳ Planned |
| 4 | Execution & alert system | ⏳ Planned |
| 5+ | Hardening, compliance, self-evolution, DR | ⏳ Planned |

---

## Quickstart

If you have not yet set up the environment, **start with the
non-technical setup guide** at [`docs/SETUP_FOR_NON_TECHNICAL.md`](docs/SETUP_FOR_NON_TECHNICAL.md).

Once the environment is ready:

```bash
# 1. Boot the local stack (Postgres, Redis, MinIO, Prometheus, Grafana, …)
aegis up

# 2. Apply migrations
aegis migrate

# 3. Run a Reddit scrape against /r/buyitforlife
aegis scrape --source reddit --subreddit buyitforlife --limit 50


# 4. Watch signals stream in
aegis signals tail
```

For the full command reference and dashboard tour, see
[`docs/USER_GUIDE.md`](docs/USER_GUIDE.md).

---

## Project layout

```
.
├── bootstrap/               # Phase 0 — Windows + WSL setup scripts
├── db/migrations/           # Raw .sql migrations (applied by alembic env)
├── alembic/                 # Alembic harness (Phase A: SQL, Phase B: Python)
├── src/aegis/
│   ├── __init__.py
│   ├── constants.py         # Project-wide named constants
│   ├── config.py            # pydantic-settings singleton
│   ├── schemas/             # ProductSignal, Author, enums
│   ├── core/                # logging, metrics, resilience
│   ├── db/                  # PgPool + ingest helpers
│   ├── cache/               # RedisCache + PriorityQueue
│   ├── scrape/              # Source adapters
│   │   ├── base.py
│   │   ├── cloudflare.py    # FlareSolverr client
│   │   ├── proxies.py       # ProxyPool
│   │   ├── stealth.py       # Browser-fingerprint evasion
│   │   └── sources/
│   │       ├── reddit.py
│   │       ├── tiktok.py
│   │       ├── youtube.py
│   │       ├── instagram.py
│   │       ├── pinterest.py
│   │       ├── amazon.py
│   │       ├── google_trends.py
│   │       └── hacker_news.py
│   └── cli/                 # `aegis` Click app
├── tests/
│   ├── unit/                # No network, no Docker
│   └── integration/         # testcontainers (gated on Docker availability)
└── docs/                    # User-facing documentation
```

---

## Development

```bash
# Install with dev extras
uv sync --all-extras

# Lint + format
uv run ruff check . && uv run ruff format .

# Type check (both checkers)
uv run mypy src
uv run pyright

# Unit tests (no Docker required)
uv run pytest tests/unit -m "not integration"

# Full suite incl. integration (requires Docker)
uv run pytest --cov=aegis --cov-fail-under=85
```

---

## Philosophy

Six rules drive every design choice:

1. **Correctness > safety > alpha > latency > cost > elegance.** In that order.
2. Every external call has timeout + retry + circuit breaker + fallback.
3. Every datetime is timezone-aware UTC. No exceptions.
4. Every secret is loaded at runtime from env or Vault. Never hardcoded.
5. Every signal carries a content hash so dedup works across re-scrapes.
6. The free-tier path is the default. Paid services are flagged optional.

---

## License

Proprietary. All rights reserved. © 2026.
