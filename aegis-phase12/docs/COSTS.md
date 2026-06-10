# AEGIS Pulse — Cost Ledger (Phase 12)

**Last updated**: 2026-05-19  
**Target**: $0.00 / month on FREE-TIER path  
**Current monthly estimate**: $0.00

---

## Phase 12: Security & Secrets — Cost Breakdown

| Component | Provider | Tier | Monthly Cost | Notes |
|---|---|---|---|---|
| **HashiCorp Vault** | Self-hosted | Vault OSS | $0.00 | Single-node dev mode; Raft for prod |
| **SOPS + age** | Self-hosted | Open-source | $0.00 | Apache-2.0 + BSD licenses |
| **mkcert** | Self-hosted | Open-source | $0.00 | Local TLS only; prod uses Traefik ACME |
| **JWT auth** | In-process | Built-in (PyJWT) | $0.00 | No external auth service needed |
| **Rate limiting** | Redis | Reuses Phase 1 Redis | $0.00 | Redis already provisioned |
| **Audit log storage (local)** | Local disk | — | $0.00 | JSONL file on WSL filesystem |
| **Audit log archival** | MinIO | Reuses Phase 1 MinIO | $0.00 | Object-lock on self-hosted MinIO |
| **PII scrubbing (regex)** | In-process | Built-in | $0.00 | |
| **PII scrubbing (NER)** | In-process | spaCy OSS | $0.00 | en_core_web_sm model, CPU-only |
| **Secret scanning** | detect-secrets | Apache-2.0 | $0.00 | CLI tool, pre-commit hook |
| **Container scanning** | Trivy | Apache-2.0 | $0.00 | Self-run on CI |

**Phase 12 Total**: **$0.00 / month**

---

## Full Stack Cost Ledger

| Phase | Component | Provider | Monthly Cost |
|---|---|---|---|
| 0–1 | PostgreSQL + TimescaleDB | Self-hosted | $0.00 |
| 0–1 | Redis | Self-hosted | $0.00 |
| 0–1 | MinIO object store | Self-hosted | $0.00 |
| 0–1 | FlareSolverr | Self-hosted | $0.00 |
| 0 | Reddit RSS, HN, GitHub Trending | Public APIs | $0.00 |
| 0 | Google News, Bing News RSS | Free RSS | $0.00 |
| 0 | Google Trends (pytrends) | Unofficial free | $0.00 |
| 2 | LLM — Ollama (local) | Self-hosted | $0.00 |
| 2 | LLM burst — Groq | Free tier | $0.00 |
| 2 | LLM burst — OpenRouter | Free models | $0.00 |
| 2 | ChromaDB | Self-hosted | $0.00 |
| 3 | ML inference — ONNX/CPU | In-process | $0.00 |
| 4 | Alert delivery — ntfy | ntfy.sh free | $0.00 |
| 4 | Alert delivery — Telegram | Bot API free | $0.00 |
| 4 | Alert delivery — Discord | Webhook free | $0.00 |
| 12 | Vault OSS | Self-hosted | $0.00 |
| All | Prometheus + Grafana | Self-hosted | $0.00 |
| All | Jaeger tracing | Self-hosted | $0.00 |
| All | Compute | Laptop (WSL2) | $0.00 |

**Grand Total: $0.00 / month**

---

## Scale Triggers (when to pay)

| Trigger | Upgrade | Estimated Cost |
|---|---|---|
| LLM usage > 14k req/day (Groq free limit) | Groq Pro | ~$9/month |
| MinIO data > 500 GB on laptop | Backblaze B2 | ~$3/month per 100 GB |
| Need multi-region secrets | Vault Enterprise | Contact HashiCorp |
| Need SMS alerts | Twilio / MessageBird | ~$1 per 100 SMS |
| Signal volume > 100k/day | Hetzner VPS (2 vCPU/4GB) | ~€5/month |
| Need RBAC audit for enterprise | Vault Enterprise | Contact HashiCorp |

---

## Free Tier Limits Reference

| Service | Free Tier Limit | Renewal |
|---|---|---|
| Groq | ~14,400 req/day (llama-3.3-70b) | Daily |
| OpenRouter (free models) | Rate-limited per model | Per request |
| Google AI Studio (Gemini) | 15 RPM, 1M tokens/day | Daily |
| Backblaze B2 | 10 GB storage + 1 GB egress/day | Monthly |
| Telegram Bot API | No hard limit (fair use) | — |
| ntfy.sh | 250 msg/day on shared server | Daily |
| GitHub Actions | 2,000 min/month (free) | Monthly |

---

## Cost Monitoring

Run the cost report CLI to get current usage estimates:

```bash
uv run aegis security cost-report

# Output example:
# AEGIS Cost Report (2026-05-19)
# ─────────────────────────────
# Groq API calls today:     847 / 14,400 (5.9%)
# MinIO storage:            12.3 GB / unlimited (self-hosted)
# Vault secrets:            23 / unlimited (self-hosted)
# Redis memory:             256 MB / 12 GB configured
# ─────────────────────────────
# Estimated monthly cost:   $0.00
# Projected scale trigger:  None in next 30 days
```

---

## Cost Optimisation Notes

1. **Ollama first**: all LLM calls try local Ollama before cloud providers.
   Zero cost for the majority of workloads when GPU is available.

2. **Heuristic-first**: Phase 3 ML inference uses ONNX INT8 quantized models
   on CPU — no GPU cloud cost needed.

3. **Redis TTLs**: all cache keys have TTLs to prevent unbounded growth.
   Signal cache: 1h; rate-limit buckets: auto-expire; JWT revocation: token lifetime.

4. **Audit log rotation**: local JSONL rotated every 24h; only archives uploaded
   to MinIO — no external storage cost.

5. **Night mode**: `aegis down` stops all containers when not in use.
   Power saving on a laptop; no cloud cost impact on self-hosted stack.
