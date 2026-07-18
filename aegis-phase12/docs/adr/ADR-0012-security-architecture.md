# ADR-0012: Phase 12 Security Architecture

**Status**: Accepted  
**Date**: 2026-05-19  
**Author**: AEGIS Engineering

---

## Context

AEGIS Pulse processes sensitive market intelligence and executes trades.
The Phase 12 security layer must protect:
- Secrets (DB passwords, LLM API keys, HMAC keys)
- PII embedded in scraped signals
- Inter-service message integrity
- Prediction audit integrity
- Dashboard access control

The platform runs on a solo developer laptop (WSL2) with zero infrastructure budget.

---

## Decision 1: HashiCorp Vault OSS (not cloud secret managers)

**Chosen**: Vault OSS in dev-mode on the developer machine; Raft backend for production.

**Rejected alternatives**:
- AWS Secrets Manager — requires paid AWS account; violates $0 constraint
- GCP Secret Manager — same problem
- `.env` files — no access control, no audit, no rotation
- Python `keyring` — no inter-service sharing

**Rationale**: Vault OSS is Apache-2.0 licensed, runs in dev-mode with zero config,
and provides KV v2 (versioning, soft-delete) + Transit encryption + audit log.
The `SecretsManager` fallback chain (Vault → SOPS → env) ensures the system
works even when Vault is unavailable in CI.

---

## Decision 2: SOPS + age (not GPG)

**Chosen**: SOPS with age encryption backend.

**Rejected**: SOPS with GPG — requires GPG daemon, keyring management, complex key distribution.

**Rationale**: `age` is a modern, simple encryption tool (Go, no daemon, no config files).
A single `age-keygen` command produces the key pair. `sops + age` encrypts
`.env.sops.yaml` files that can be committed to git safely.

---

## Decision 3: Heuristic-first PII scrubbing (regex + optional NER)

**Chosen**: Two-pass scrubbing (regex patterns first, spaCy NER second, optional).

**Rejected**: NER-only — too slow for high-throughput ingestion (10k signals/hour).
**Rejected**: Regex-only — misses proper names not in pattern dictionaries.

**Rationale**: The regex pass catches structured PII (email, phone, IP, credit card)
with <1ms per signal. The spaCy NER pass catches unstructured names but is
optional and gracefully degrades when spaCy is not installed. This matches
the "heuristic-first" doctrine of the overall system.

---

## Decision 4: Append-only HMAC-chained audit log

**Chosen**: Local JSONL file with HMAC-SHA256 per entry + chain hash, archived to MinIO WORM.

**Rejected**: Database-backed audit log — DB can be modified by a compromised DB admin.
**Rejected**: No audit log — required for GDPR/DPDP compliance.

**Rationale**: The chain-hash structure (each entry includes SHA-256 of the previous entry)
makes tampering detectable. The HMAC per entry prevents forgery of individual records.
MinIO object-lock (WORM) prevents deletion of archived batches.

---

## Decision 5: HS256 JWT (not RS256) for Phase 12 v1

**Chosen**: HMAC-SHA256 (HS256) symmetric JWT signing.

**Rejected immediately for v2+**: RS256 (asymmetric) — required for multi-service token
verification without sharing the signing secret.

**Rationale**: In Phase 12 v1, all services run in the same Docker network and share
the HMAC key via Vault. HS256 is simpler and faster. The `JWTManager` API accepts
a `jwt_algorithm` parameter so switching to RS256 is a config change, not a code change.

**Migration path**: When Phase 12 services are deployed to separate clusters,
switch to RS256 with public-key distribution via Vault PKI engine.

---

## Decision 6: Redis token-bucket for rate limiting (not nginx/traefik)

**Chosen**: Application-level Redis token-bucket via Lua script.

**Rejected**: nginx/Traefik rate-limit modules — not available in the current dev stack.

**Rationale**: The Lua script executes atomically in Redis, providing exact token-bucket
semantics without race conditions. The middleware degrades gracefully (fail-open)
when Redis is unavailable. Per-route overrides are supported via `request.state`.

---

## Consequences

- Every service import of `aegis.security` must not import from Phases 0–4
  (one-way dependency: security is the innermost layer).
- `SecretsManager` must be initialised before any Phase 0–4 service starts.
- The `AuditLogger` must be started and stopped as a context manager to ensure
  the MinIO upload queue drains cleanly on shutdown.
- All new FastAPI endpoints must be wrapped with `enforcer.require_permission()`.
- All new scrape adapters must call `secure_scrape_signal()` before DB writes.

---

## Review schedule

This ADR is reviewed when:
- A new AEGIS service is added (check RBAC permission matrix)
- Vault is upgraded (check API compatibility)
- A new PII data category is identified in scraped signals (add regex pattern)
- Production deployment is initiated (switch to RS256, HSM unseal)
