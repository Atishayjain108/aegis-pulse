# ADR 001 — LLM Provider Priority Order

**Date**: 2026-05-20
**Status**: Accepted
**Deciders**: AEGIS Engineering

---

## Context

Phase 11 integrates 7 LLM providers. We need a deterministic, principled
priority order so the system always selects the cheapest, most private,
most reliable provider without manual intervention.

## Decision

Provider priority (0 = highest):

| Priority | Provider | Rationale |
|----------|----------|-----------|
| 0 | Ollama (local) | Zero cost, zero latency, fully private, offline-capable |
| 1 | vLLM (local GPU) | Zero cost, 10× throughput, requires GPU hardware |
| 2 | Groq | Free tier generous (~14k req/day), 10× faster than OpenAI |
| 3 | OpenRouter | Free model pool, good variety, slightly less reliable |
| 4 | Gemini | Free tier strict (15 RPM), 1M token context is useful |
| 5 | Anthropic | Paid, high quality, used only for high-stakes decisions |
| 6 | OpenAI | Paid, last resort, highest cost |

## Consequences

**Positive**:
- System runs at $0/day by default (Ollama handles everything)
- Cloud providers serve as automatic fallback — no manual intervention needed
- Cost ceiling is enforced without configuration

**Negative**:
- Groq/OpenRouter free tier RPM limits may be hit during high-volume periods
- Provider health checks add ~60ms to the first request after cold start
- Circuit breaker trips during Ollama restarts briefly degrade quality

## Alternatives Considered

- **Round-robin across all providers**: Rejected — wastes free-tier quota on paid providers
- **Always use best-quality model**: Rejected — contradicts the zero-budget principle
- **User-configured priority**: Rejected — adds configuration complexity for no benefit in v1

## Review

Revisit when:
- A new free provider with > 50 RPM is available
- vLLM becomes the norm for local inference (may swap positions 0 and 1)
- Groq moves to a paid-only model
