# AEGIS-SCRAPE-0001: Adapter HTTP Error

**Code**: `AEGIS-SCRAPE-0001`  
**Phase**: Phase 0 — Scrape Layer  
**Severity**: WARNING (graceful degradation, other adapters continue)

## What happened

An HTTP request to a scrape source returned a non-200 status code (4xx or 5xx),
or timed out. The adapter is quarantined temporarily and will retry after cooldown.

## Common causes

| Status | Cause | Action |
|--------|-------|--------|
| 403 Forbidden | Bot detected / IP banned | Proxy rotation needed |
| 429 Too Many Requests | Rate limit hit | Backoff already applied |
| 503 Service Unavailable | Source temporarily down | Retry after 60s |
| Timeout | Slow source or network issue | Reduce timeout or skip source |

## Remediation

```bash
# Check adapter health
uv run aegis swarm agents

# Force a fresh scrape with a different proxy
uv run aegis scrape --source hacker-news --limit 10

# Check if the source is globally down
curl -I https://hacker.news/  # substitute source URL
```

---

# AEGIS-LLM-0001: All Providers Failed

**Code**: `AEGIS-LLM-0001`  
**Phase**: Phase 11 — LLM Orchestration  
**Severity**: WARNING (heuristic verdict used; pipeline continues)

## What happened

All providers in the fallback chain (Ollama → vLLM → Groq → OpenRouter → Gemini →
Anthropic → OpenAI) failed or had open circuit breakers. The LLM reasoning text
is empty, but the heuristic verdict is still valid.

## Remediation

```bash
# Check provider health
uv run aegis llm health

# Check circuit breaker states
uv run aegis llm health --json-out | jq '.providers[] | select(.circuit_open == true)'

# Force Ollama pull (if Ollama is the primary issue)
uv run aegis llm pull llama3.2:3b

# Reset circuit breakers (wait 60s or restart)
docker compose restart ollama
```

## Zero-API-key mode

The pipeline always produces a result even with this error. The heuristic floor
guarantees a verdict from numeric features alone. Set `AEGIS_DISABLE_OLLAMA=1`
to skip Ollama in tests.

---

# AEGIS-LLM-0003: Guardrail Block

**Code**: `AEGIS-LLM-0003`  
**Phase**: Phase 11 — LLM Orchestration  
**Severity**: INFO (expected for toxic/PII content)

## What happened

`GuardrailsValidator` blocked the LLM response due to:
- Length > 8192 characters
- PII detected (email, phone, SSN, Aadhaar, PAN)
- Toxic pattern matched

## Remediation

This is expected behaviour — the guardrail is working correctly.

If false-positives are frequent:
1. Check `AEGIS_GUARDRAIL_MAX_LEN` setting (default: 8192).
2. Review the toxic pattern list in `aegis.llm.guardrails.validator`.
3. File an issue if a legitimate response is being blocked.

---

# AEGIS-DATALAKE-0001: Bronze Ingest Failed

**Code**: `AEGIS-DATALAKE-0001`  
**Phase**: Phase 10 — Data Lake  
**Severity**: ERROR (batch skipped; previous batches intact)

## What happened

The Bronze layer ingester failed to write a batch to the object store (MinIO/S3).
The batch is NOT lost — it can be re-ingested from the source (PostgreSQL or Redis
stream) because ingestion is idempotent (SHA256 batch_id prevents duplicates).

## Remediation

```bash
# Re-run ingest (safe to repeat — idempotent)
uv run aegis datalake ingest-postgres-signals \
  --dsn postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis

# Check MinIO health
curl http://localhost:9002/minio/health/live

# Check disk space (MinIO container)
docker exec aegis-minio df -h /data
```

---

# AEGIS-EXECUTE-0001: Killswitch Tripped

**Code**: `AEGIS-EXECUTE-0001`  
**Phase**: Phase 4 — Execution & Alert  
**Severity**: CRITICAL (all outbound dispatch halted)

## What happened

The execution killswitch was tripped. All outbound notifications (Discord, Telegram,
ntfy, etc.) are suspended. The alert pipeline continues to run and queue alerts —
they accumulate in the outbox and will be delivered when the killswitch is armed.

## Remediation

```bash
# Check killswitch state
uv run --package aegis-execute aegis-execute killswitch state

# Arm the killswitch (resume dispatch)
uv run --package aegis-execute aegis-execute killswitch arm --reason "issue resolved"

# Check queued alerts
uv run --package aegis-execute aegis-execute tail \
  --tenant 00000000-0000-0000-0000-000000000001 --limit 50
```

## When to use the killswitch

- Runaway alert storm (many duplicate alerts)
- Testing the pipeline without sending real notifications
- Maintenance window
- Suspected data poisoning (manual review before dispatch)
