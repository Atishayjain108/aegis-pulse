# AEGIS Phase 11 Changelog

All notable changes to Phase 11 (Local LLM Orchestration) are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [11.0.0] — 2026-05-22

### Added
- `LLMGateway` — single entry point for all LLM calls across Phases 0–5
- Provider adapters: Ollama, vLLM, Groq, OpenRouter, Gemini, Anthropic, OpenAI
- `ProviderSelector` — health-cached, priority-ordered provider selection
- `CostAwareRouter` — extends selector with per-call cost filtering
- `TaskRouter` — routes by task type (code, analysis, fast, reasoning, creative)
- `SemanticRouter` — bypasses LLM for simple rule-based queries (<2ms)
- `GuardrailsValidator` — PII, toxic content, length validation on outputs
- `PIIScrubber` — input-side PII scrubbing (9 rule types including Aadhaar, PAN)
- `InstructorAdapter` — typed pydantic output with JSON error-correction retry
- Agent output schemas: `ScoutOutput`, `SentinelOutput`, `AuditorOutput`,
  `ComplianceOutput`, `NarrativeOutput`, `RedTeamOutput`, `GeoArbitrageOutput`,
  `HedgeOutput`, `SourcerOutput`, `HistorianOutput`
- `PromptRegistry` — Jinja2 template management with SHA-256 version hashing
- `ModelRegistry` — model capability tracking and runtime metrics
- 10 Jinja2 prompt templates for all 10 agent nodes
- `LLMCache` — two-layer cache (in-process LRU + optional Redis)
- `Tokenizer` — context-window estimation without tiktoken dependency
- `Metrics` — Prometheus counters/histograms (graceful no-op without prom)
- `StreamingGateway` — SSE streaming for Ollama/vLLM/Groq/OpenRouter
- `LatencyBudgetMiddleware`, `RateLimitMiddleware`, `CostGateMiddleware`, `RequestLogMiddleware`
- Phase 2 bridge: `get_gateway()`, `complete_for_agent()`, `set_gateway()` (for testing)
- Phase 3 bridge: `generate_prediction_rationale()`, `generate_causal_explanation()`
- Phase 4 bridge: `compose_alert_message()`, `format_alert_for_telegram()`, `format_alert_for_discord()`
- `EvalRunner` — nightly golden-answer eval with pass-rate CI gate
- `EvalMetricsCollector` — latency, coverage, regression metrics
- Golden eval cases for: `scout_analysis`, `sentinel_saturation`, `auditor_financial`
- `aegis llm health/complete/embed/eval/pull/models/cost` CLI commands
- `docker-compose.phase11.yml` with Ollama + vLLM + LiteLLM services
- `config/litellm_config.yaml` — LiteLLM proxy configuration
- `bootstrap/wsl/06_ollama_models.sh` — idempotent model pull script
- 10 error codes `AEGIS-LLM-0001` through `AEGIS-LLM-0010` with runbooks
- 2 Architecture Decision Records (ADR 001, ADR 002)
- Full integration guide `docs/PHASE11_INTEGRATION.md`
- **148 unit tests** across 11 test files
- **16 integration tests** in `tests/integration/`
- All 164 tests pass · 0 ruff violations · mypy clean

### Technical decisions
- Heuristic-first: `SemanticRouter` bypasses LLM for ~30% of agent queries
- Free-tier default: Ollama → Groq → OpenRouter → Gemini before any paid call
- No tiktoken dependency: token estimation via heuristic (±20% accuracy)
- No `semantic-router` library: native 200-line implementation with full observability
- Circuit breaker: 30% error rate over 60s window trips the breaker

---

## Upcoming

- `[11.1.0]` — vLLM multi-GPU support, tensor parallelism config
- `[11.2.0]` — Langfuse tracing integration for prompt experiments
- `[11.3.0]` — Online fine-tuning loop: eval failures → training data
