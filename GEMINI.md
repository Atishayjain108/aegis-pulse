# AEGIS Pulse — Gemini CLI Instructions

This file provides foundational mandates, architectural patterns, and development workflows for the AEGIS Pulse project. All AI agents must adhere to these instructions.

## System Overview

AEGIS Pulse is an autonomous market arbitrage intelligence engine. It scrapes signals from 35+ platforms, stores them in TimescaleDB, and runs a 10-node LangGraph multi-agent pipeline to score and prioritise arbitrage opportunities.

## Core Mandates

### 1. Heuristic-First Doctrine
- Every agent and module MUST produce a deterministic verdict from numeric/heuristic features.
- LLMs (if available) are for augmenting reasoning text ONLY. They MUST NOT be allowed to flip a verdict produced by heuristics.
- The system MUST remain functional and pass all core tests without LLM API keys.

### 2. Security & Multi-Tenancy
- **Row-Level Security (RLS):** All database tables use RLS. Every query MUST be preceded by `SET app.current_tenant = '<uuid>'`.
- **Tenant IDs:** The default development tenant ID is `00000000-0000-0000-0000-000000000001`.
- **Credentials:** Never log or commit API keys or secrets. Use `.env` files and `pydantic-settings`.

### 3. Resilience & Errors
- Use `aegis.core.resilience` (decorator-based) for scraper I/O.
- Use `aegis.predict.resilience` (functional `resilient_call`) for ML inference.
- All errors should be typed (e.g., `AEGIS-PREDICT-0001`) and logged with `structlog`.

### 4. Data Integrity
- **Pydantic v2:** Use frozen Pydantic v2 models for core data structures (`TrendCandidate`, `AgentDecision`, `GraphResult`).
- **Semantic Deduplication:** Use the two-layer dedup (token + sequence) in `aegis.scrape.dedup`.
- **Redis Stream Field:** The field name for Redis stream payloads is always `"body"`, containing a JSON-encoded string.

## Architecture

| Phase | Name | Key Components |
|-------|------|----------------|
| Phase 0 | Scrape | 35+ adapters, `topic.py`, `swarm.py`, `patterns.py` |
| Phase 1 | Persistence | TimescaleDB, Redis, MinIO |
| Phase 2 | Agents | 10-node LangGraph DAG, ChromaDB memory |
| Phase 3 | Predict | Predictive Apex (Hybrid ML), ONNX, RL policy |
| Phase 4 | Execute | Alert pipeline, SSE streaming, Killswitch |
| Phase 5 | Scale | SwarmOrchestrator, OLS velocity, PCA denoising |
| Phase 10| Data Lake | Parquet on MinIO, DuckDB query engine |
| Phase 11| LLM | LLMGateway, Circuit Breaker, Local fallback |

## Development Workflows

### Environment Setup
- **Dependencies:** Use `uv` for package management. Run `uv sync --all-packages --all-extras`.
- **Infrastructure:** Use Docker Compose for the dev stack: `docker compose up -d`.

### Running the System
- **Full Daily Run:** `uv run aegis daily`
- **Topic Analysis:** `uv run aegis topic "keyword"`
- **Scrape Only:** `uv run aegis scrape --source <name> --limit <N>`
- **Analyze Only:** `uv run aegis analyze --limit <N>`

### Testing
- **Unit Tests:** `uv run pytest tests/unit/`
- **Coverage:** Aim for ≥ 82% coverage. Use `uv run pytest tests/unit/ --cov=aegis`.
- **Integration Tests:** `uv run pytest tests/integration/predict/` (requires infra).

## Engineering Standards

- **Logging:** Use `structlog` exclusively in `aegis.agents.*` and `aegis.agents_phase3_glue.*`. Use keyword arguments for context.
- **Async:** Prefer `asyncio` for I/O bound tasks. Use `loop.run_in_executor` for synchronous libraries (like `pytrends`).
- **Database:** Access DB via `get_shared_pool()` in `aegis.db.pool`.
- **Conventions:**
    - Python 3.12+ features.
    - Strict type hinting (checked with `mypy`).
    - Linting with `ruff`.

## Common Gotchas
- **Reddit:** Use `Accept-Encoding: gzip` only and `http2=False`.
- **NSE:** Requires `Referer` headers and session priming.
- **Amazon:** Titles are approximate (slug-based).
- **LangGraph:** Teardown may leave Unix sockets open; ignore `ResourceWarning` in tests.
- **NaN Checks:** Use `v != v` for IEEE-754 NaN checks (suppress with `# noqa: PLR0124`).

For more detail, refer to `architecture.md`, `DEVELOPER_GUIDE.md`, and `SYSTEM_TOUR.md`.
