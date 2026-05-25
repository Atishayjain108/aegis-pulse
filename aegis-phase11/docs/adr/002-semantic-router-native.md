# ADR 002 — Semantic Router: Native Implementation vs OSS Library

**Date**: 2026-05-20
**Status**: Accepted

---

## Context

We need a semantic router to bypass LLMs for simple, deterministic queries.
Two options: use the `semantic-router` OSS library, or build natively.

## Decision

Build a native implementation in `aegis.llm.routing.semantic_router`.

## Rationale

| Factor | `semantic-router` lib | Native |
|--------|----------------------|--------|
| Dependencies | adds ~15 transitive deps | zero extra deps |
| Observability | limited hooks | full structlog + Prometheus |
| Embedding backend | opinionated | wired to our OllamaProvider |
| Test surface | black-box | fully unit-testable |
| Maintenance | upstream breaking changes | we control it |

The native implementation is ~200 lines and covers 100% of our use case.
The complexity cost is justified by the control gained.

## Consequences

We must maintain the cosine similarity and centroid computation code.
This is acceptable — the math is stable and well-understood.
