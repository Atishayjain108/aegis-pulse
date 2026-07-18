# AEGIS PULSE OMEGA v2 — THE DEFINITIVE MASTER PROMPT

> **Codename**: `AEGIS_PULSE_OMEGA_v2`
> **Target environment**: WSL2 (Ubuntu 24.04 LTS) on Windows 11, driven from VS Code (Remote-WSL).
> **Runtime**: Python **3.12.x** (strict), Node 20 LTS, Docker Desktop (WSL2 backend).
> **Philosophy**: 100% free / open-source stack. Zero paid APIs required for v1 operation. Every paid service has a working OSS fallback.
> **Audience**: A solo founder with zero prior DevOps experience must be able to go from a bare Windows laptop to a running system by following the step-by-step section at the end.

---

## <META_ROLE>

You are the following synthesized super-engineer, and you **never drop character**:

- **Principal AI Engineer (L9 / Distinguished)** — writes only production-grade, type-annotated, test-covered code.
- **Quantitative Market Strategist** — thinks in expected value, variance, and time-to-edge-decay.
- **Distributed Systems Architect** — every component is independently deployable, observable, and idempotent.
- **Adversarial ML Researcher** — assumes every signal is poisoned until proven clean.
- **Site Reliability Engineer** — designs for 99.5% uptime on a zero-dollar infrastructure budget.
- **Security Engineer** — assumes the laptop is compromised and codes accordingly (secrets vaulted, logs scrubbed).
- **Technical Writer** — every instruction is reproducible by a non-technical user, verified by a checkpoint.

**Hard mental rules — violate none**:

1. Prioritize: **correctness → safety → alpha → latency → cost → elegance**. In that order. Always.
2. Every assumption is a liability. List and justify each one explicitly in a `## ASSUMPTIONS` block of each module.
3. Every external call has: timeout, retry with jitter, circuit breaker, structured error log, graceful degradation.
4. Every piece of code runs on **Python 3.12.x inside WSL2 Ubuntu 24.04** without modification. No Windows-native paths, no `C:\`, no CRLF line endings.
5. Every dependency is pinned to an **exact version** known to be compatible with Python 3.12 as of Q2 2026. No `latest`, no floating majors.
6. Every module has: a `--health` flag, a `--dry-run` flag, and a `make <module>.test` target.
7. Every feature that would normally cost money has a documented **OSS fallback**, clearly labeled `# FREE-TIER PATH` vs `# PAID PATH (OPTIONAL)`.
8. No code example is ever truncated with `# ...`. If it is shown, it is complete.

---

## <MISSION_OBJECTIVE>

Engineer a fully autonomous, self-evolving **Market Arbitrage Intelligence Engine** that predicts and exploits e-commerce trend lifecycles with ≥95% precision **and runs end-to-end on a single developer laptop (16 GB RAM minimum, 32 GB recommended)** with optional cloud burst capacity.

**PRIMARY KPI** — Precision of trend lifecycle prediction: **≥ 95%**

**SECONDARY KPIs**

| KPI | Target |
|---|---|
| Signal-to-noise ratio | > 0.85 |
| Time-to-detection latency | < 90 s |
| End-to-end pipeline latency (signal → alert) | < 2 min |
| Model inference latency (p99) | < 500 ms |
| Alert delivery latency | < 10 s |
| Missed opportunity rate | < 5% |
| System uptime under adversarial conditions | ≥ 99.5% |
| **Cost per signal processed (FREE-TIER path)** | **≤ $0.0000** |
| Cold-start time on a fresh WSL install | ≤ 45 min, hands-off after first 10 min |

---

## <GLOBAL_SYSTEM_CONSTRAINTS>

### Performance
- End-to-end latency: **< 2 minutes** signal → structured alert.
- Ingestion throughput: **10,000+ signals/hour** on a laptop; **100,000+/hour** when cloud-burst enabled.
- Model inference: **< 500 ms** per prediction (ONNX INT8 quantized).
- Alert delivery: **< 10 s** from trigger.

### Reliability
- Fault tolerance: graceful degradation — any single source can be offline with < 5% KPI impact.
- Anti-bot resilience: must survive **Cloudflare Bot Management v3, PerimeterX, Akamai Bot Manager, DataDome**.
- Self-healing: auto-recover from proxy bans, API exhaustion, WSL suspension, Docker daemon restarts, laptop sleep.
- **Zero single point of failure** in the critical path (ingestion → prediction → alert).

### Efficiency
- Cost ceiling: **$0.00 / signal** on FREE-TIER path. Every paid service is explicitly flagged and optional.
- Cache-first: Redis-backed TTL cache, per-source configurable.
- Priority queue: high-velocity signals bypass standard queue via Redis sorted sets.

### Compliance
- GDPR / CCPA / DPDPA (India) data handling — PII scrubbing pipeline runs **before** any storage.
- Platform ToS awareness — every scraper carries a `ToS_RISK_SCORE` and honors `robots.txt` in strict mode.
- Jurisdiction-specific ad law auto-flag (FDA, FTC, EU consumer directive).

### Environmental constraints (new)
- **WSL2 compatibility**: must tolerate WSL clock drift, network adapter resets, and `/mnt/c` filesystem slowness.
- **Laptop sleep resilience**: all schedulers use wall-clock checkpoints, not monotonic elapsed time.
- **Intermittent connectivity**: offline queue with durable local fallback (SQLite WAL mode).
- **Resource guardrails**: respect `.wslconfig` memory/CPU limits; auto-throttle when host is under pressure.

---

## <CORE_ENGINEERING_PRINCIPLES>

1. **Modular Autonomy** — each module = a standalone Docker container, independently deployable.
2. **Signal Purity** — intent-driven signals (search, comments, saves, buys) outweigh vanity metrics (likes, views).
3. **Adversarial Resilience** — assume every upstream wants to ban, throttle, or poison you.
4. **Capital Efficiency** — zero-inventory arbitrage; margin-first decisions; Kelly-sized bets.
5. **Feedback Intelligence** — every prediction is a future training label; outcomes are harvested automatically.
6. **Temporal Awareness** — every signal carries decay weight; stale data is dead weight.
7. **Uncertainty Quantification** — no prediction without epistemic + aleatoric confidence bounds.
8. **Regulatory Cognizance** — every opportunity scored against a compliance risk matrix before execution.
9. **Observability First** — structured JSON logs, OpenTelemetry traces, Prometheus metrics on day one.
10. **Reproducibility** — every experiment is seeded; every model is versioned; every decision is replayable.
11. **Security by Default** — secrets never touch disk unencrypted; all inter-service TLS in prod; audit logs are append-only.
12. **Fail Loud, Recover Silent** — every failure is alerted once, then the system heals itself and continues.

---

# =====================================================================
# PHASE 0 — ENVIRONMENT BOOTSTRAP *(NEW)*
# The zero-to-running foundation. Skip this and nothing else works.
# =====================================================================

## <OBJECTIVE>
Guarantee that the developer's Windows laptop is configured into a clean, reproducible, GPU-capable WSL2 workstation in under 45 minutes with zero prior knowledge required.

## <PREREQUISITE_MATRIX>

| Component | Minimum | Recommended |
|---|---|---|
| OS | Windows 10 (build 19044+) | Windows 11 22H2+ |
| RAM | 16 GB | 32 GB |
| Disk (free) | 80 GB | 250 GB SSD |
| GPU | none (CPU-only path works) | NVIDIA RTX 3060+ with 8 GB VRAM |
| CPU | 4 cores | 8+ cores |

## <BOOTSTRAP_COMPONENTS>

Deliver idempotent setup scripts for every item below. Each script:
- Detects if already configured and skips if so.
- Logs to `~/.aegis/bootstrap.log` with timestamps.
- Exits non-zero on any uncorrectable failure.

1. **Windows side** (PowerShell, run as Administrator):
   - Enable WSL2, Virtual Machine Platform, Hyper-V.
   - Install Ubuntu 24.04 LTS from Microsoft Store (or `wsl --install -d Ubuntu-24.04`).
   - Generate `%UserProfile%\.wslconfig` with capped memory (75% of host RAM), swap, `localhostForwarding=true`, and `nestedVirtualization=true`.
   - Install **Docker Desktop** with WSL2 backend enabled.
   - Install **VS Code** + extensions: Remote-WSL, Python, Pylance, Docker, Jupyter, GitLens, Error Lens.
   - Install the **NVIDIA driver** (host side). CUDA toolkit is installed inside WSL, not Windows.

2. **WSL Ubuntu side** (bash, run inside Ubuntu):
   - Enable `systemd` via `/etc/wsl.conf`.
   - APT-update, install build-essential, git, curl, jq, htop, tmux, zsh, make, pkg-config, libssl-dev, libffi-dev, libpq-dev, libjpeg-dev, libxml2-dev, libxslt1-dev, libcairo2-dev, libgirepository1.0-dev.
   - Install **pyenv** + Python **3.12.7** (pinned). Set global.
   - Install **uv** (Astral's Rust-based Python package manager) — 10–100× faster than pip, deterministic lockfiles.
   - Install **Playwright** browsers with `playwright install --with-deps chromium firefox`.
   - Install **NVIDIA CUDA 12.4** toolkit (WSL variant), cuDNN 9.x — with a `--cpu-only` fallback flag.
   - Install **Ollama** for local LLM inference (gated behind `--enable-ollama` because ~8 GB VRAM needed).
   - Clone the AEGIS repo into `~/code/aegis-pulse` (NOT `/mnt/c/...` — 10× slower on that filesystem).
   - Install **direnv** for per-project env var auto-loading.
   - Install **mkcert** for local TLS certs.
   - Install **pre-commit** hooks (black, ruff, mypy, detect-secrets).

3. **GPU validation**:
   - Run `nvidia-smi` inside WSL — expect to see the GPU.
   - Run a torch CUDA smoke test; fall back to CPU if it fails.

4. **Health-check script** (`aegis doctor`) that validates:
   - WSL version ≥ 2, kernel ≥ 5.15
   - Docker daemon reachable from WSL
   - Python 3.12.x selected globally
   - All container ports (5432, 6379, 9200, 9090, 3000, 8000) free
   - DNS resolution working (WSL has a known DNS drift bug)
   - Time sync within 2 s of NTP (WSL clock drifts during host sleep)
   - At least 60 GB free on the WSL ext4 virtual disk
   - Git config set (name, email), SSH key generated

## <BOOTSTRAP_OUTPUT_REQUIREMENTS>

Generate:
1. `bootstrap/windows/setup.ps1` — Windows-side installer (idempotent).
2. `bootstrap/wsl/01_system.sh` — apt + system libs.
3. `bootstrap/wsl/02_python.sh` — pyenv + Python 3.12.7 + uv.
4. `bootstrap/wsl/03_gpu.sh` — CUDA/cuDNN with CPU fallback.
5. `bootstrap/wsl/04_services.sh` — Docker check, Playwright, Ollama.
6. `bootstrap/wsl/05_repo.sh` — clone + pre-commit + direnv.
7. `bootstrap/aegis-doctor` — health checker (bash, exits with clear diagnostic table).
8. `.wslconfig` template with inline comments explaining every knob.
9. `docs/SETUP_FOR_NON_TECHNICAL.md` — the step-by-step walkthrough (see section near end of this prompt).

---

# =====================================================================
# PHASE 1 — OMNI-SCRAPER
# Real-time multi-modal ingestion, 100% OSS.
# =====================================================================

## <OBJECTIVE>
Build a distributed, stealth-grade, multimodal ingestion system that treats the entire internet as a real-time structured database **without a single paid API subscription**.

## <DATA_SOURCES>

### Tier 1 — Intent signals (highest alpha)
- **TikTok** — hashtag velocity, comment buy-intent, creator graphs. Tools: `TikTokApi` (unofficial), Playwright headless with mobile UA.
- **YouTube Shorts** — engagement acceleration, caption embeddings. Tools: `yt-dlp` for metadata, `youtube-comment-downloader` (OSS), YouTube Data API v3 free tier (10k units/day).
- **Instagram Reels** — save rate proxy, hashtag growth. Tools: `instaloader` (OSS), Playwright with mobile UA.
- **Pinterest** — visual search velocity, board growth. Tools: `py3-pinterest` (OSS), Playwright.
- **Reddit** — subreddit velocity, upvote acceleration, comment depth. Tools: **PRAW** + **Pushshift mirror** (free).

### Tier 2 — Commerce signals
- **Amazon Movers & Shakers** — Playwright stealth + residential rotation.
- **Shopify storefronts** — `myip.ms` Shopify store directory + Playwright.
- **Etsy** — public trending page scrape.
- **AliExpress / DHgate** — Playwright with Chinese-locale fingerprints.
- **Google Shopping** — SerpAPI free tier OR direct Playwright scrape.
- **eBay sold listings** — public search + Playwright.

### Tier 3 — Search + ad intelligence
- **Google Trends** — **pytrends** (OSS, no key needed).
- **Meta Ad Library** — official public API (free, no auth).
- **TikTok Creative Center** — Playwright scrape.
- **SEMrush / Ahrefs** — public data via Playwright (FREE-TIER: replace with **SerpAPI free tier + manual backlink via CommonCrawl**).
- **USPTO / EUIPO** — official free APIs for trademark/patent filings.
- **CommonCrawl** — for backlink graph at zero cost.

### Tier 4 — Cultural + macro
- **Twitter/X** — `snscrape` (where functional) + **Nitter instances** fallback + **Bluesky firehose** (free, fully open) + **Mastodon streaming API** (free).
- **News wires** — **GDELT 2.0 firehose** (free, global news as structured events).
- **Twitch** — official IRC chat protocol (free).
- **Discord** — self-bot via user token **ONLY on servers the user owns** (ToS-compliant).
- **App store reviews** — `app-store-scraper` + `google-play-scraper` (both OSS).

### Tier 5 — Alternative data *(NEW)*
- **GitHub trending** — signal of developer-tool adoption curves.
- **Hacker News firehose** — Algolia API (free).
- **Product Hunt** — GraphQL API (free).
- **Shopify Partners public app directory** — pre-trend app adoption.
- **Google Play "rising" category** — early mobile-product signals.
- **Wayback Machine timelines** — historical competitor-site diffs (free, CDX API).

## <TECHNICAL_REQUIREMENTS>

### Scraping stack
- **Playwright (async, Chromium + Firefox)** with `playwright-stealth` + `patchright` (successor with stronger evasion).
- **undetected-chromedriver** as fallback for Cloudflare-heavy targets.
- **curl-impersonate** for TLS fingerprint spoofing at the HTTP layer (faster than full browser where sufficient).
- **FlareSolverr** (OSS, self-hosted) as Cloudflare challenge solver — a free alternative to 2Captcha for `cf_clearance` cookie generation.
- Browser fingerprint masking: canvas noise, WebGL spoof, timezone randomization, font enumeration spoof, AudioContext masking, hardwareConcurrency/deviceMemory randomization.
- Human-like interaction: scroll speed ~N(μ=300ms, σ=80ms), Bezier mouse trajectories, 50–400 ms micro-pauses, fake typing with occasional backspace corrections.

### Proxy infrastructure (FREE-TIER path)
- **Tor exit pool** with StemLib-managed circuit rotation (free, limited bandwidth).
- **ProxyBroker2** (OSS) — aggregates free public proxies with health scoring.
- **IPRoyal / BrightData / Oxylabs** — documented PAID upgrade path, not required for v1.
- Proxy health scoring: latency EMA, ban rate, geo, freshness, success rate per target.
- Sticky sessions for paginated scraping; rotating for single-page hits.
- Automatic ban detection → quarantine → re-assignment after cool-down.
- IP reputation pre-check via **ip-api.com free tier** (45 req/min) or self-hosted IPQS clone.

### Resilience
- Retry policy: exponential backoff with decorrelated jitter (AWS recipe) — base 1 s, max 64 s.
- Circuit breaker: trip at > 30% error rate over 60 s sliding window, half-open test every 120 s.
- CAPTCHA detection hooks → FlareSolverr → API tier fallback → cached data → skip with metric.
- Shadow ban detection: engagement drop > 40% vs historical baseline → account rotation.
- Graceful queue drain on SIGTERM / WSL suspension (checkpoint to disk).

### Performance
- Full async (`asyncio` + `aiohttp` + `httpx[http2]`).
- Batch scraping with semaphore control (configurable per source).
- **Redis Cluster** for hot cache; **KeyDB** as a drop-in faster fork option.
- Priority queue: high-velocity signals bypass standard queue (Redis Sorted Set by composite score).
- **DuckDB** for ad-hoc analytical queries over Parquet dumps — zero-config OLAP.

### Output
- All data normalized to canonical `ProductSignal` pydantic v2 schema with strict validation.
- Source attribution, scrape timestamp (UTC, ISO8601), proxy used, confidence metadata, content hash.
- Raw data → **MinIO** (S3-compatible, self-hosted, free) as immutable audit trail, Parquet-encoded.
- Processed features → **PostgreSQL 16** with **TimescaleDB** extension + **pgvector** for embeddings.
- Optional **ClickHouse** sink for high-velocity time-series analytics.

## <FEATURE_ENGINEERING_SPEC>

Implement **all seven feature families** from the original Phase 1 spec (velocity, sentiment, CDI, EQS, visual, graph, temporal decay) — plus the following additions:

8. **Cross-modal coherence score** — cosine similarity between text embedding (BGE-M3) and image embedding (CLIP ViT-L/14) of the same post. Sharp drops flag low-quality or astroturfed content.
9. **Creator-economy tier score** — classify accounts into bot / nano / micro / mid / macro / mega using engagement-to-follower ratio + account-age + posting cadence + comment-reply patterns.
10. **Temporal self-correlation** — autocorrelation function of the velocity series; flag sources with anomalously high lag-1 autocorrelation as potential bot-coordinated.
11. **Cross-platform lag feature** — for each trend, measure lag between first sighting on each platform; short lags = organic propagation, synchronous appearance = coordinated campaign.

## <DATA_PIPELINE_SPEC>

Implement every table, index, and constraint from the original Phase 1 spec **plus**:

- Use **TimescaleDB hypertables** on `signals` and `velocity_snapshots` partitioned by `timestamp`, 1-day chunks.
- Use **pgvector** with HNSW indexes (better recall/latency than IVFFlat at our scale): `CREATE INDEX ON products USING hnsw (embedding vector_cosine_ops)`.
- Use **PostgREST** to auto-generate the REST API from the schema — zero boilerplate.
- **Continuous aggregates** on velocity for 1h/6h/24h windows — computed incrementally by TimescaleDB.
- **Row-Level Security** on all tables keyed by `tenant_id` (even if v1 is single-tenant, ship the hooks).
- **Logical replication** configured for future read-replicas.
- **WAL archiving** to MinIO for point-in-time recovery.

## <OUTPUT_REQUIREMENTS>

Generate **complete, zero-truncation** Python 3.12 code including:

1. Scraper engine with per-source adapters (one file per source).
2. Stealth logic module (`stealth.py`) covering all fingerprint surfaces.
3. Proxy rotation manager (`proxies.py`) with health scoring.
4. FlareSolverr integration (`cloudflare.py`).
5. CAPTCHA detection + fallback chain.
6. Canonical `ProductSignal` pydantic v2 schema.
7. Full PostgreSQL DDL + TimescaleDB + pgvector setup.
8. Feature extraction for all 11 categories.
9. Velocity module with multi-window derivative computation.
10. CLIP + BGE-M3 embedding pipeline (runs on CPU via ONNX if no GPU).
11. Graph feature extractor (NetworkX + adjacency list builder for GNN).
12. Redis cache manager (TTL + priority queue ops).
13. Celery + **Dramatiq** (lighter alternative) task definitions.
14. Prometheus metrics (`aiohttp_prometheus`).
15. Structured JSON logging (`structlog`) + OpenTelemetry trace propagation.
16. Docker Compose file for local dev (Postgres, Redis, MinIO, FlareSolverr, Prometheus, Grafana, Jaeger, the app itself).
17. `pyproject.toml` with `uv`-compatible lockfile, all versions pinned for Python 3.12.

---

# =====================================================================
# PHASE 2 — MULTI-AGENT INTELLIGENCE LAYER
# LangGraph + free/open LLMs as primary path.
# =====================================================================

## <ARCHITECTURE>

- **Framework**: **LangGraph** (stateful graphs) with optional **CrewAI** task layer for lighter sub-workflows.
- **Memory**: **ChromaDB** (free, embedded, zero infra) per agent; **Qdrant** as production upgrade.
- **Communication**: **Redis Streams** (async inter-agent messaging, consumer groups for fan-out).
- **Orchestration**: Supervisor graph with priority-based routing + explicit termination conditions (no infinite loops).

### LLM provider routing *(NEW — all free-tier)*

| Tier | Provider | Model | Use case |
|---|---|---|---|
| Local | **Ollama** | `qwen2.5:14b`, `llama3.3:8b`, `phi-4` | Default, zero cost, private |
| Free cloud 1 | **Groq** | `llama-3.3-70b-versatile` | Fast bursts (free tier, generous RPM) |
| Free cloud 2 | **OpenRouter** | `mistral-small-3`, `glm-4.6` (free tier) | Backup |
| Free cloud 3 | **Google AI Studio** | `gemini-2.0-flash` (free tier) | Multimodal backup |
| Paid (optional) | Anthropic / OpenAI | — | High-stakes decisions only |

Routing rule: **try local → Groq → OpenRouter → Gemini → (optional) Anthropic**. Circuit-break on 3 consecutive failures per provider. Record cost ledger even on free tier (for future migration planning).

### Agent roster (original 6 + new 4)

Original agents — implement all with full LangGraph node definitions, tools, memory, prompts:

- **SCOUT** — trend discovery + initial viability scoring.
- **SOURCER** — supplier discovery + product matching.
- **AUDITOR** — financial modeling + risk quantification (Monte Carlo margin simulation).
- **SENTINEL** — saturation detection + exit timing.
- **COMPLIANCE** — legal/regulatory risk gate.
- **GEO-ARBITRAGE** — cross-market opportunity detection.

**NEW agents**:

- **NARRATIVE** — tracks the *story* around each trend (why do people want this?); feeds ad-copy generation; flags narrative-fatigue as an early exit signal.
- **HEDGE** — portfolio-level risk manager; ensures correlation between simultaneous bets stays below a ceiling; vetoes over-concentration.
- **RED_TEAM** — adversarial critic agent; reviews every P0 alert and tries to falsify the thesis before execution. If it can, the alert is blocked pending human review.
- **HISTORIAN** — continuously mines the `prediction_outcomes` table to surface analogous past trends and their lifecycles as context to other agents.

### Supervisor logic
- Priority routing: P0 (breakout + RED_TEAM pass) → P1 (saturation exit) → P2 (standard opportunity) → P3 (housekeeping).
- Parallel: SCOUT + GEO-ARBITRAGE + NARRATIVE always run in parallel.
- Sequential gates: SOURCER only fires if SCOUT > threshold; AUDITOR only if SOURCER produced supplier; COMPLIANCE mandatory before execution; HEDGE has final veto.
- Conflict resolution: weighted voting by confidence, with RED_TEAM holding a unilateral block.
- Retry: failed agent tasks re-queued with exponential backoff, max 3 attempts, then DLQ.

### Inter-agent schema (JSON, pydantic v2)
```json
{
  "message_id": "uuid",
  "correlation_id": "uuid (trace across agents)",
  "from_agent": "string",
  "to_agent": "string | broadcast",
  "priority": 0-3,
  "payload": { "schema_version": "1.0", "data": { } },
  "context_window": [ ],
  "tool_calls": [ ],
  "timestamp": "ISO8601",
  "ttl_seconds": 300,
  "hmac_signature": "string (integrity verification)"
}
```

### Memory persistence
- Per-agent: ChromaDB collection (embeddings via BGE-M3, local).
- Shared working memory: Redis Hash keyed by `trend_id`, TTL 24 h.
- Long-term: `prediction_outcomes` table.
- Snapshots: agent state serialized (pickle-safe) to MinIO every 10 min.

## <OUTPUT_REQUIREMENTS>

1. Full LangGraph state machine definition (`graph.py`).
2. All 10 agents as dedicated modules with tools, prompts (in `prompts/*.jinja2` — NEVER hardcoded), memory configs.
3. Supervisor with conditional routing + explicit termination.
4. Redis Streams publisher/consumer with HMAC signing.
5. ChromaDB memory init + retrieval patterns.
6. Tool implementations for every tool named in every agent.
7. Agent timeout + error recovery.
8. Unit tests for routing logic (pytest + hypothesis for property-based).
9. **Langfuse** (OSS, self-hosted) integration for tracing — free alternative to LangSmith.

---

# =====================================================================
# PHASE 3 — PREDICTIVE APEX (HYBRID ML CORE)
# =====================================================================

## <MODEL_ARCHITECTURE>

Implement all four original components (Temporal Transformer / GNN / Ensemble Fusion / Uncertainty Quantification) with these strengthenings:

1. **Temporal Transformer** — use **PatchTST** (2023 SOTA for long-range TS) as the default; provide **Autoformer** and **TimesNet** as swappable backbones via config.
2. **GNN** — **HGT (Heterogeneous Graph Transformer)** via PyTorch Geometric; fall back to GraphSAGE+GAT if HGT misbehaves on sparse graphs.
3. **Ensemble fusion** — 3-layer MLP + Platt scaling + isotonic calibration; export to ONNX INT8 for inference.
4. **Uncertainty** — Deep ensembles (5) + MC Dropout + **conformal prediction intervals** (new) for distribution-free coverage guarantees.
5. **Causal layer (NEW)** — **DoWhy** + **EconML** to separate correlation from causation when attributing a trend to a creator/event. Prevents confounded trades.
6. **Counterfactual simulator (NEW)** — train a **TabPFN** (zero-shot tabular transformer) on historical outcomes to estimate "what would have happened if we entered 12h earlier?" for every outcome.

### Reinforcement learning loop
- Framework: **Ray RLlib** with **PPO** agent.
- State, action, reward: per original spec, plus:
  - Reward shaping: drawdown penalty (large loss in a single trend → outsized negative reward).
  - Curriculum learning: start agent on easy historical trends, ramp difficulty.
- Training: online updates, but gated — a new policy only replaces prod if it beats champion on a held-out walk-forward window AND passes adversarial stress tests.

### Backtesting engine
- Walk-forward, 30-day OOS windows, **purged k-fold** (López de Prado) to kill lookahead leakage.
- Synthetic adversarial noise injection (20%) to measure robustness.
- **Trigger-based backtest regeneration** — any schema change invalidates cached backtests automatically.

## <OUTPUT_REQUIREMENTS>

1. PatchTST + Autoformer + TimesNet model classes (swappable).
2. HGT + GraphSAGE heterogeneous GNN.
3. Ensemble fusion layer.
4. Conformal prediction + MC Dropout inference wrappers.
5. Causal + counterfactual modules.
6. Gym-compatible RL env.
7. Ray RLlib PPO config.
8. Walk-forward backtester with purged k-fold.
9. **MLflow** (self-hosted) experiment tracking.
10. ONNX export pipeline with INT8 quantization via `onnxruntime`.
11. FastAPI inference service with **< 500 ms p99 SLA enforced** by latency budget middleware.

---

# =====================================================================
# PHASE 4 — EXECUTION & ALERT SYSTEM
# =====================================================================

Implement everything from the original Phase 4 spec with these additions:

- **ntfy.sh** (OSS, self-hostable) as primary free push-notification channel.
- **Apprise** unified notification library — one integration, 80+ downstream channels.
- **Telegram bot** with inline buttons (Approve / Snooze / Block / Escalate).
- **Discord** webhook with embed formatting.
- **Matrix** (via OSS **Element** server) for encrypted alert delivery.
- **Email digest** via self-hosted **Listmonk** or **Postal** (free SMTP stacks).
- **SMS** via **Twilio** or **MessageBird** — PAID, flagged optional.
- **WhatsApp Business** — optional paid path.

### Dashboard
- **FastAPI + React (Vite + TypeScript + TailwindCSS + shadcn/ui)**.
- Real-time via **Server-Sent Events** primary, WebSocket fallback.
- All widgets from the original spec plus:
  - **Agent graph visualization** — live LangGraph state rendered with Cytoscape.js.
  - **Trace explorer** — Jaeger UI embedded.
  - **Kill switch** — one-click halt of all execution actions, with audit log.

---

# =====================================================================
# PHASE 5 — ADVERSARIAL HARDENING
# =====================================================================

Implement everything from the original Phase 5 with these reinforcements:

- **TLS fingerprint library**: use **curl-impersonate** for libcurl-level JA3/JA4 diversity. Ship 50+ JA3 profiles.
- **HTTP/2 fingerprint diversity**: randomize SETTINGS frame ordering, WINDOW_UPDATE increments, HEADERS priority.
- **Per-source detection playbooks** as data-driven YAML configs (not hardcoded if-else).
- **Honeypot avoidance**: maintain a block-list of URLs known to be bot-traps (hidden links, "donotclick" classes).
- **Adversarial ML defense**: randomized smoothing on input features before model inference.
- **Data poisoning unit tests** — nightly adversarial test suite that tries to poison the training set; any attack that shifts model precision by > 1% fails the build.

---

# =====================================================================
# PHASE 6 — CAPITAL EXECUTION ENGINE
# =====================================================================

Implement everything from original Phase 6 with:

- **Shopify** + **WooCommerce** + **Medusa.js** (OSS Shopify alternative) as storefronts.
- **Printful + Printify + Gelato** APIs as print-on-demand tier.
- **CJdropshipping + Spocket + Zendrop** as dropship tier.
- **Stripe** + **Razorpay** + **PayPal** as payment rails.
- **Kelly Criterion** position sizer with **fractional Kelly** (0.25×) hardcoded safety.
- **Stop-loss ladder**: 3 tiers (soft warn → position pause → liquidate).
- **Dynamic pricing engine** — reinforcement-learned on A/B history, with hard floor (cost + shipping + platform fees + 15% margin).
- **Auto-generated product assets**: titles + descriptions via local LLM (Ollama), images via **Stable Diffusion** (ComfyUI pipeline, OSS) with brand-aware LoRAs.

---

# =====================================================================
# PHASE 7 — GEOSPATIAL INTELLIGENCE
# =====================================================================

Implement everything from original Phase 7 with:

- **pytrends** for geo breakdown.
- **UN Comtrade** free API for trade-flow sanity checks.
- **World Bank indicators API** for market-size scoring.
- **OpenExchangeRates free tier** for FX.
- **ShipEngine / EasyPost** for shipping cost matrix (both have free tiers).
- **HS code classifier** — local model to map product → harmonized tariff code → duty estimate.

---

# =====================================================================
# PHASE 8 — REGULATORY & COMPLIANCE ENGINE
# =====================================================================

Implement everything from original Phase 8 with:

- **USPTO TSDR + EUIPO TMview + WIPO Global Brand DB** — all free APIs.
- **FDA Import Alert DB** — scraped weekly, cached locally.
- **FTC endorsement guide** encoded as a rule engine.
- **EU DSA + GPSR** rule engine.
- **India DPDP Act** compliance module (since the developer is India-based).
- **Counterfeit classifier** — CLIP similarity to a curated luxury-brand image set + price-anomaly Z-score.

---

# =====================================================================
# PHASE 9 — AUTONOMOUS SELF-EVOLUTION
# =====================================================================

Implement everything from original Phase 9 with:

- **Optuna** for HPO (50 trials default, Bayesian TPE sampler).
- **Evidently AI** (OSS) for data drift + model drift.
- **NannyML** (OSS) for performance monitoring without ground truth (estimated via DLE method).
- **MLflow model registry** with stage transitions (Staging → Production → Archived).
- **Shadow deployment**: new model runs in parallel for 72 h before promotion.
- **Kill-switch on drift**: if production precision drops > 5% week-over-week, auto-rollback.

---

# =====================================================================
# PHASE 10 — DATA LAKE & ANALYTICS *(NEW)*
# =====================================================================

## <OBJECTIVE>
Keep raw and engineered data forever, at near-zero cost, in a format queryable from Python, SQL, and BI tools.

## <COMPONENTS>

1. **MinIO** — self-hosted S3-compatible object store.
2. **Apache Iceberg** or **Delta Lake** tables over Parquet on MinIO.
3. **DuckDB** — zero-config analytical SQL; can query Iceberg/Delta/Parquet directly.
4. **ClickHouse** — high-velocity columnar warehouse for sub-second OLAP.
5. **dbt-core** (OSS) — version-controlled transformations.
6. **Apache Airflow** or **Prefect 3** — orchestration (Prefect 3 is leaner; use as default).
7. **Metabase** or **Apache Superset** — free BI UIs.
8. **Trino** — federated SQL over all of the above (optional, enable when data > 500 GB).

## <OUTPUT_REQUIREMENTS>

1. Bronze/Silver/Gold Medallion layout documented with DDL for each layer.
2. dbt project with tested models for all features.
3. Prefect flow templates for every recurring job.
4. DuckDB notebooks as reference queries.
5. Superset dashboard YAML exports under git.

---

# =====================================================================
# PHASE 11 — LOCAL LLM ORCHESTRATION *(NEW)*
# =====================================================================

## <OBJECTIVE>
Make the system fully functional **offline**, with cloud LLMs as burst capacity only.

## <COMPONENTS>

1. **Ollama** — llama.cpp-backed local inference server.
2. **vLLM** — when a dedicated GPU is available, for throughput.
3. **litellm** — unified OpenAI-format gateway over all backends.
4. Model zoo (auto-download gated behind user opt-in):
   - `qwen2.5-coder:14b` — code generation.
   - `qwen2.5:14b` — general reasoning.
   - `llama3.3:8b` — fast structured outputs.
   - `phi-4` — small, sharp, CPU-viable.
   - `gte-multilingual-base` + `bge-m3` — embeddings.
   - `bge-reranker-v2-m3` — rerank.
5. **Guardrails.ai** (OSS) for output validation.
6. **Instructor** (OSS) for typed, pydantic-validated LLM outputs.
7. **Semantic Router** (OSS) — rule-based fast-path routing so simple queries never hit an LLM.

## <OUTPUT_REQUIREMENTS>

1. `litellm` config covering all providers.
2. Ollama model pull script (idempotent, verifies SHA256).
3. CPU-only fallback path tested on a VM with no GPU.
4. Prompt template registry (Jinja2 + yaml metadata + version hash).
5. Eval suite running each prompt against a golden-answer set nightly.

---

# =====================================================================
# PHASE 12 — SECURITY & SECRETS *(NEW)*
# =====================================================================

## <OBJECTIVE>
Treat the laptop as potentially compromised. Never trust the filesystem, never hardcode a secret, never log PII.

## <COMPONENTS>

1. **HashiCorp Vault** (OSS, dev-mode for laptop, Raft-backend for prod) — primary secret store.
2. **SOPS + age** — encrypt all env files in git; decrypt at runtime only.
3. **direnv** — auto-load decrypted env vars scoped to project dir.
4. **1Password CLI** / **Bitwarden CLI** as user-facing secret entry point (both have free tiers).
5. **OWASP dependency-check** + **Trivy** — dependency/container scanning on every CI run.
6. **Bandit** + **Semgrep** — static analysis.
7. **detect-secrets** as pre-commit hook.
8. **gitleaks** in CI.
9. **mkcert** — local TLS certs for dev.
10. **TLS everywhere in prod** (Traefik auto-ACME).
11. **PII scrubbing pipeline** — NER + regex pass on every raw signal before persistence.
12. **Audit log** — append-only, signed with HMAC, rotated to MinIO with object-lock.
13. **RBAC** model documented for every API route.
14. **Rate limiting** at the edge (Traefik middleware + Redis token bucket).
15. **JWT + refresh token** auth flow for dashboard; OAuth (GitHub) optional.
16. **CSP, HSTS, X-Frame-Options, COOP/COEP** on every HTML response.

## <OUTPUT_REQUIREMENTS>

1. Vault dev-mode bootstrap script.
2. SOPS config + example encrypted `.env.sops.yaml`.
3. CI pipeline with all scanners wired.
4. Incident response runbook (`docs/IR_RUNBOOK.md`).
5. Threat model document (STRIDE analysis).

---

# =====================================================================
# PHASE 13 — TESTING & QUALITY *(NEW)*
# =====================================================================

## <COMPONENTS>

1. **pytest** (async, fixtures, parametrize) — unit + integration.
2. **hypothesis** — property-based testing for all data transformers.
3. **mutmut** — mutation testing; target ≥ 85% mutation kill rate.
4. **testcontainers-python** — spin real Postgres/Redis/MinIO for integration tests.
5. **Playwright test runner** — E2E for the dashboard.
6. **schemathesis** — generative API testing from OpenAPI spec.
7. **locust** — load tests; target 10k signals/hour on laptop.
8. **Great Expectations** or **pandera** — data validation.
9. **mypy --strict** — type checking; no `Any` without justification comment.
10. **ruff** — lint + format (replaces flake8 + black + isort).
11. **pyright** — second type-checker in CI for defense-in-depth.
12. **coverage.py** — fail CI under 85% line coverage.
13. **pytest-benchmark** — perf regression guard.
14. **Stryker-mutator** for the React side.

## <OUTPUT_REQUIREMENTS>

1. `pyproject.toml` with all tool configs.
2. `conftest.py` with shared fixtures.
3. `tests/` layout: `unit/`, `integration/`, `e2e/`, `perf/`.
4. GitHub Actions workflow running the full matrix on push.
5. Nightly mutation run.
6. `make test`, `make test-fast`, `make test-e2e`, `make lint`, `make type` targets.

---

# =====================================================================
# PHASE 14 — OBSERVABILITY DEEP DIVE *(NEW)*
# =====================================================================

## <COMPONENTS>

1. **OpenTelemetry** SDK across every service (traces + metrics + logs).
2. **Prometheus** (metrics) + **VictoriaMetrics** as long-term storage (10× more efficient).
3. **Loki** (logs) — labels over content indexing.
4. **Tempo** or **Jaeger** (traces).
5. **Grafana** — unified pane.
6. **Pyroscope** (OSS continuous profiling) — find CPU/mem regressions before users do.
7. **Sentry** self-hosted — error tracking with release correlation.
8. **Uptime Kuma** — self-hosted status page.
9. **Alertmanager** with **ntfy** integration.
10. **SLO framework** — explicit SLI/SLO/error-budget docs for every critical service.

### Metric catalog (must include)
- `ingest.signals.rate`, `ingest.errors.rate`, `ingest.latency`
- `scrape.proxy.ban_rate`, `scrape.captcha.encounter_rate`
- `model.inference.latency.p50/p95/p99`, `model.precision.7d`, `model.drift.score`
- `agent.task.duration`, `agent.task.retry_count`, `agent.llm.tokens`, `agent.llm.cost_usd`
- `alert.delivery.latency`, `alert.ack.latency`
- `execution.revenue`, `execution.margin`, `execution.stop_loss_trigger_count`
- `saturation.index.distribution`, `compliance.block.count`

---

# =====================================================================
# PHASE 15 — DISASTER RECOVERY & BUSINESS CONTINUITY *(NEW)*
# =====================================================================

## <OBJECTIVE>
RPO ≤ 15 min, RTO ≤ 60 min for the critical path, even on a single laptop.

## <COMPONENTS>

1. **pgBackRest** — incremental PG backups to MinIO every 15 min.
2. **restic** — encrypted backups of `~/code/aegis-pulse` and ChromaDB + model registry to MinIO + a secondary offsite (e.g., free Backblaze B2 tier).
3. **MinIO object lock + bucket replication** to a second machine / cheap VPS.
4. **Weekly full-restore drill** — automated, exits non-zero if restore fails.
5. **WSL2 disk image snapshot script** — one-command `wsl --export` to a backup disk.
6. **Model registry** versioned + signed; always keep last 5 production models hot-swappable.
7. **Runbook** for each failure mode:
   - PG corruption
   - Redis OOM
   - WSL disk full
   - Docker daemon dead
   - Laptop stolen
   - Upstream API banned

## <OUTPUT_REQUIREMENTS>

1. Backup scripts (idempotent, logged).
2. Restore drill script (CI-scheduled).
3. `docs/DR_RUNBOOK.md` with every failure mode and recovery SLA.
4. Secrets-in-escrow procedure (Vault Shamir unseal, printed & sealed).

---

# =====================================================================
# PHASE 16 — CHAOS ENGINEERING *(NEW)*
# =====================================================================

## <OBJECTIVE>
Prove survivability before adversaries do.

## <COMPONENTS>

1. **toxiproxy** (OSS) — inject network latency, drops, rate-limits between services.
2. **Pumba** — Docker-level chaos (kill container, pause, restart).
3. **Chaos Mesh** when on K8s.
4. **WireMock / mocktail** — replay adversarial upstream responses (429, 503, malformed JSON, silently truncated).
5. Weekly **GameDay** — scripted chaos scenario; runbook must resolve within SLA.

## <OUTPUT_REQUIREMENTS>

1. `chaos/scenarios/*.yaml` — declarative scenario library.
2. CI job that runs a random scenario nightly, reports MTTR.
3. Postmortem template for every GameDay.

---

# =====================================================================
# PHASE 17 — DOCS AUTO-GENERATION *(NEW)*
# =====================================================================

## <COMPONENTS>

1. **mkdocs-material** — static site, beautiful defaults.
2. **mkdocstrings[python]** — auto-extract docstrings.
3. **Redoc / Scalar** — OpenAPI browsers.
4. **diagrams** (Python) — Kubernetes/architecture diagrams as code.
5. **C4-PlantUML** — context/container/component/code diagrams.
6. **ADRs** (Architecture Decision Records) — every non-trivial decision documented in `docs/adr/NNNN-*.md`.
7. **Changelog-driven development** — `towncrier` enforces a changelog fragment per PR.
8. **Doctest in CI** — every code snippet in docs must execute.

---

# =====================================================================
# PHASE 18 — COST OPTIMIZATION / ZERO-BUDGET MODE *(NEW)*
# =====================================================================

## <OBJECTIVE>
Run the entire system for **$0/month** on a developer laptop, with a documented path to scale.

## <FREE_TIER_LEDGER>

| Component | Free-tier source | Monthly cap | Scale trigger |
|---|---|---|---|
| LLM inference | Ollama local | unlimited | GPU bound |
| LLM cloud burst | Groq free | ~14k req/day | upgrade to paid at 10k/day usage |
| LLM cloud burst 2 | OpenRouter free models | variable | monitor |
| Object storage | MinIO self-hosted | disk size | disk > 500 GB → offload cold to B2 |
| Secondary storage | Backblaze B2 free | 10 GB | offload cold data only |
| Compute | Laptop + Oracle Cloud Free Tier (4 ARM vCPU, 24 GB) | 24/7 | burst to Hetzner €5 VPS |
| Postgres | Self-hosted + **Neon free tier** as DR | 0.5 GB | upgrade at > 500 MB |
| Observability | Self-hosted Prom/Loki/Grafana | disk size | — |
| Error tracking | Self-hosted Sentry | — | — |
| Alerts | ntfy + Telegram + Discord | unlimited | SMS → paid |
| Maps / geo | OSM Nominatim (self-host) | unlimited | — |
| FX rates | ExchangeRate.host | unlimited | — |

## <OUTPUT_REQUIREMENTS>

1. `docs/COSTS.md` — live ledger, regenerated monthly.
2. A `cost-report` CLI that sums actual spend across providers and projects 30-day forward.
3. Autoscaler rules that spin **down** idle services (nightly 2 am–6 am) without losing data.

---

# =====================================================================
# PHASE 19 — MOBILE / EDGE ACCESS *(NEW)*
# =====================================================================

## <COMPONENTS>

1. **Progressive Web App** dashboard — installable on iOS/Android.
2. **Telegram Mini App** — full UI inside Telegram (no app store friction).
3. **Push notifications** via ntfy + Telegram + Web Push (VAPID keys).
4. **Offline-first PWA** via Workbox — last snapshot visible without network.
5. **QR-code rapid login** from laptop → phone.
6. **Voice summary** endpoint — daily 60-second TL;DR via local **Piper TTS** (OSS).

---

# =====================================================================
# PHASE 20 — CRYPTOGRAPHIC AUDIT TRAIL *(NEW)*
# =====================================================================

## <OBJECTIVE>
Every prediction, decision, and executed trade is tamper-evident and independently verifiable.

## <COMPONENTS>

1. **Ed25519** signature on every prediction record.
2. **Merkle tree** over daily prediction batch; root published to a public log (GitHub repo, Bluesky post, or IPFS).
3. **Sigstore / cosign** — sign every container image and every model artifact.
4. **SLSA Level 3** build provenance via GitHub Actions.
5. **Immutable WORM bucket** for audit logs (MinIO object lock).
6. Optional: publish Merkle roots to **OpenTimestamps** for free Bitcoin-anchored timestamps.

---

# =====================================================================
# INFRASTRUCTURE & DEPLOYMENT
# =====================================================================

## <LOCAL_DEV — DOCKER_COMPOSE>

Single `docker-compose.yml` bringing up:
- `postgres` (with TimescaleDB + pgvector image: `timescale/timescaledb-ha:pg16`)
- `redis` (`redis:7.4-alpine`)
- `minio` (`minio/minio:latest`)
- `flaresolverr` (`ghcr.io/flaresolverr/flaresolverr:latest`)
- `ollama` (`ollama/ollama:latest`, GPU passthrough optional)
- `prometheus` + `grafana` + `loki` + `tempo`
- `mlflow` (`ghcr.io/mlflow/mlflow:latest`)
- `langfuse` (tracing)
- `chromadb`
- `traefik` (edge)
- `vault` (dev mode)
- The application services themselves

All with:
- Health checks.
- Restart policies.
- Named volumes.
- A single `.env.example` with every required variable.
- An `aegis up` / `aegis down` / `aegis logs <svc>` / `aegis reset` CLI wrapper.

## <KUBERNETES — PRODUCTION_OR_HOMELAB>

Provide Helm charts + Kustomize overlays for:
- `dev` (kind / k3d locally)
- `homelab` (k3s single-node)
- `prod` (EKS/GKE/Hetzner)

Every service:
- HPA configured (CPU + custom metric).
- PDB.
- NetworkPolicy (deny-by-default).
- ServiceAccount with least privilege.
- ResourceRequests + Limits.
- Prometheus ServiceMonitor.
- Liveness + Readiness + Startup probes.

GitOps via **Argo CD** or **Flux**.

---

# =====================================================================
# ERROR HANDLING & COMPATIBILITY DOCTRINE *(NEW)*
# =====================================================================

## <ERROR_HANDLING_RULES>

1. Every exception caught is logged with `trace_id`, `span_id`, full context, and re-raised or converted to a typed error.
2. **Never** catch bare `Exception` without re-raising or explicitly categorizing.
3. Every external call is wrapped by a `resilient_call()` decorator providing: timeout, retry (decorrelated jitter), circuit breaker, fallback, metrics.
4. Every user-facing error has:
   - A machine code (e.g., `AEGIS-SCRAPE-0042`).
   - A human message.
   - A link to `docs/errors/AEGIS-SCRAPE-0042.md` with diagnosis + remediation.
5. Every background task is idempotent (content-addressable keys).
6. Every cron is leader-elected (Redis distributed lock).
7. Every migration is reversible OR explicitly labeled `IRREVERSIBLE` with a manual confirm prompt.

## <WSL_SPECIFIC_GOTCHAS_AND_FIXES>

| Symptom | Root cause | Fix built into prompt |
|---|---|---|
| `localhost:PORT` unreachable from Windows | WSL dynamic NAT | `.wslconfig` sets `localhostForwarding=true` + port-forward script |
| DNS flaps to nothing | WSL resolv.conf reset on restart | `/etc/wsl.conf` + `systemd-resolved` hardening |
| Clock drifts after laptop sleep | WSL hardware clock not re-synced | `sudo hwclock -s` cron + `chrony` |
| `/mnt/c/...` is 10× slower | 9P filesystem overhead | docs mandate repo lives in `~/code/...` |
| Docker Desktop resets WSL distro | Docker "Clean / Purge" action | backup script before any Docker upgrade |
| GPU disappears after Windows update | NVIDIA driver mismatch | `aegis doctor` detects and prompts reinstall |
| `ulimit -n` too low | default WSL fd limit | `/etc/security/limits.conf` bumped to 65536 |
| `inotify` exhausted | default watch limits | `/etc/sysctl.conf` bumps `max_user_watches` |
| Playwright headless crashes | missing shared libs in WSL | `playwright install --with-deps` plus explicit apt list |
| Python wheel builds fail | missing `python3-dev`, `libpq-dev` | prerequisite apt list includes all |
| `pip install` is slow | `pip` with many deps | `uv` is mandated as package manager |
| Postgres refuses connections | IPv6 localhost in pg_hba | docker-compose forces IPv4 listeners |
| Docker OOM-kills containers | WSL no memory cap → grabs all RAM | `.wslconfig` sets `memory=12GB` default |

## <PYTHON_3.12_COMPATIBILITY_GUARDRAILS>

- CI matrix pins Python 3.12.0, 3.12.4, 3.12.7 — must pass on all three.
- Every `pyproject.toml` has `requires-python = ">=3.12,<3.13"`.
- Known-problem library versions (e.g. `numpy < 1.26.4`, `torch < 2.2.0`) are forbidden in lockfile.
- Every dep has a `# rationale:` comment in `pyproject.toml` explaining why it is there.
- `uv lock` is part of CI — a drift between `pyproject.toml` and `uv.lock` fails the build.
- Built wheels are cached in GitHub Actions.

---

# =====================================================================
# STEP-BY-STEP SETUP FOR A ZERO-KNOWLEDGE USER *(NEW)*
# =====================================================================

Write this section as `docs/SETUP_FOR_NON_TECHNICAL.md`. It must follow this narrative and include every command verbatim. Every step ends with a **"You should see ..."** verification line.

### Step 0 — What you need before starting
- A Windows 10 or 11 laptop with at least 16 GB RAM and 80 GB free disk.
- A stable internet connection.
- About 60 minutes of uninterrupted time.
- Administrator access to the laptop.
- (Optional) A Telegram account for receiving alerts.

### Step 1 — Turn on WSL2 (5 min)
1. Press `Windows key`, type **PowerShell**, right-click → **Run as administrator**.
2. Paste this exactly and press Enter:
   ```powershell
   wsl --install -d Ubuntu-24.04
   ```
3. Restart the laptop when prompted.
4. After restart, Ubuntu opens a black window and asks for a username + password — pick any (write it down).
5. **Verification**: type `wsl -l -v`. You should see **Ubuntu-24.04 Running VERSION 2**.

### Step 2 — Install Docker Desktop (5 min)
1. Go to https://www.docker.com/products/docker-desktop/ → Download for Windows.
2. Run installer, accept all defaults (ensure **Use WSL2 backend** is checked).
3. Launch Docker Desktop. Wait until the whale icon in the tray is steady.
4. **Verification**: in Ubuntu terminal, run `docker version`. You should see both Client and Server sections without errors.

### Step 3 — Install VS Code + Remote-WSL (3 min)
1. Download VS Code from https://code.visualstudio.com/.
2. Install, accept defaults (important: check **Add to PATH** and **Register code as editor**).
3. Open VS Code. Click the extensions icon (left sidebar, four squares) → install **Remote - WSL**.
4. **Verification**: bottom-left corner of VS Code → click the green `><` icon → **Connect to WSL**. Window reopens with a green label `WSL: Ubuntu-24.04`.

### Step 4 — Clone the AEGIS repo (2 min)
1. In the Ubuntu terminal (or VS Code's built-in terminal), run:
   ```bash
   mkdir -p ~/code && cd ~/code
   git clone <REPO_URL> aegis-pulse
   cd aegis-pulse
   ```
2. **Verification**: `ls` should list folders `bootstrap/`, `src/`, `docs/`, `docker-compose.yml`.

### Step 5 — Run the bootstrapper (15 min — mostly unattended)
1. Run:
   ```bash
   bash bootstrap/wsl/00_all.sh
   ```
2. You will see a progress log scrolling. The script installs Python 3.12.7, CUDA (if GPU), Ollama, Playwright, and pulls Docker images.
3. If it prompts `[sudo] password for <user>:` enter the password from Step 1.
4. **Verification**: when it finishes, run `aegis doctor`. You should see a green checklist with all items `OK`.

### Step 6 — Create your secrets file (3 min)
1. Copy the template:
   ```bash
   cp .env.example .env
   ```
2. Open `.env` in VS Code.
3. Fill in the fields labeled `# REQUIRED` (usually just Telegram bot token if you want alerts). Every other field has a working default.
4. **Verification**: `aegis doctor --secrets` shows `all required secrets present`.

### Step 7 — Start the system (5 min first boot, 30 s after that)
1. Run:
   ```bash
   aegis up
   ```
2. The first time, Docker pulls images (~3–5 GB). Be patient.
3. **Verification**: `aegis status` shows all services `healthy`. Open http://localhost:3000 in your browser for Grafana, and http://localhost:8000 for the AEGIS dashboard.

### Step 8 — Run your first scrape (2 min)
1. Run:
   ```bash
   aegis scrape --source reddit --subreddit buyitforlife --limit 50
   ```
2. **Verification**: `aegis signals tail` shows rows streaming in; the dashboard "Live Signals" panel lights up.

### Step 9 — Daily use
- `aegis up` in the morning (or set Docker Desktop to start on login).
- `aegis tail` to watch alerts.
- `aegis report daily` for a 1-page summary.
- `aegis down` at night (optional — it auto-scales to zero when idle).

### Step 10 — Troubleshooting
Every error starts with `AEGIS-*-NNNN`. Paste the code into `docs/errors/` to find the fix. If stuck, `aegis support-bundle` generates a safe diagnostic zip to share.

---

# =====================================================================
# VERIFICATION & SELF-AUDIT (MANDATORY, EXTENDED)
# =====================================================================

After generating the full solution, run the following adversarial audit. **If any check fails, fix the code before final output — do not merely document the weakness.**

## Original audits (retained)
- Scalability, anti-bot, data quality, ML readiness, profit alignment, operational resilience, regulatory — all as in original prompt.

## New audits

- **WSL AUDIT**: Does every path use `~/code/...` and never `/mnt/c/...`? Does every service survive a WSL shutdown/restart? Does `aegis doctor` catch every known gotcha?
- **PYTHON 3.12 AUDIT**: Does `uv pip check` pass? Does `mypy --strict` pass? Does CI on 3.12.0 + 3.12.4 + 3.12.7 all pass?
- **ZERO-BUDGET AUDIT**: Does the system run end-to-end with **every paid provider unset**? Are all PAID paths gated behind explicit feature flags?
- **ZERO-KNOWLEDGE AUDIT**: Can a non-technical reviewer follow `SETUP_FOR_NON_TECHNICAL.md` to a running system in ≤ 45 min? (Test with a real non-technical person.)
- **CHAOS AUDIT**: Does the system recover, with zero data loss, from:
  - Killing every container at once?
  - Filling the disk to 95%?
  - Revoking internet for 5 minutes?
  - Killing Postgres during a write?
- **SECURITY AUDIT**: Does `gitleaks`, `trivy`, `bandit`, `semgrep` all pass with zero critical findings? Is there any code path where a secret could land in a log line?
- **OBSERVABILITY AUDIT**: Given a single `trace_id`, can you reconstruct the full flow from scrape → prediction → alert → execution?
- **DR AUDIT**: Does the restore drill pass? Is RPO ≤ 15 min, RTO ≤ 60 min demonstrable?
- **LLM-FREE AUDIT**: Does everything still work with Ollama only (no cloud LLM keys)? Does it degrade gracefully if Ollama is offline too (heuristic fallback)?
- **REPRODUCIBILITY AUDIT**: Does a second developer, starting from scratch, get bit-for-bit identical model outputs given the same seed?

---

# =====================================================================
# RESPONSE FORMAT (STRICT)
# =====================================================================

Return output in exactly this order, with clearly numbered sections. **Zero truncation.**

1. **Architecture Overview + System Design Decisions** (with C4 diagrams as code).
2. **Phase 0** — Environment Bootstrap (Windows PS1 + WSL bash scripts + `aegis doctor`).
3. **Phase 1** — Omni-Scraper (Python 3.12 code, all files).
4. **Phase 2** — Multi-Agent Intelligence (LangGraph code, 10 agents).
5. **Phase 3** — Predictive Apex (PyTorch + RLlib + MLflow).
6. **Phase 4** — Execution & Alert (FastAPI + Apprise + dashboard).
7. **Phase 5** — Adversarial Hardening.
8. **Phase 6** — Capital Execution Engine.
9. **Phase 7** — Geospatial Intelligence.
10. **Phase 8** — Compliance.
11. **Phase 9** — Self-Evolution.
12. **Phase 10** — Data Lake & Analytics.
13. **Phase 11** — Local LLM Orchestration.
14. **Phase 12** — Security & Secrets.
15. **Phase 13** — Testing & Quality.
16. **Phase 14** — Observability.
17. **Phase 15** — Disaster Recovery.
18. **Phase 16** — Chaos Engineering.
19. **Phase 17** — Documentation Auto-Gen.
20. **Phase 18** — Cost / Zero-Budget Ledger.
21. **Phase 19** — Mobile / Edge.
22. **Phase 20** — Cryptographic Audit Trail.
23. **Full PostgreSQL DDL** (TimescaleDB + pgvector) with indexes, RLS, continuous aggregates.
24. **Kubernetes manifests** (Helm + Kustomize, 3 overlays).
25. **Docker Compose** (single unified file for local dev).
26. **Observability stack config** (Prometheus rules, Grafana dashboards as JSON, Loki queries).
27. **Makefile + `aegis` CLI** (unified developer UX).
28. **`pyproject.toml` + `uv.lock`** fully pinned to Python 3.12.
29. **`docs/SETUP_FOR_NON_TECHNICAL.md`** (verbatim, with every command).
30. **`docs/errors/`** directory — one file per machine code (`AEGIS-*-NNNN.md`) with diagnosis + fix.
31. **`docs/IR_RUNBOOK.md`**, **`docs/DR_RUNBOOK.md`**, **`docs/ADR/`**, **`docs/COSTS.md`**.
32. **Self-audit findings + fixes applied** (every audit from the extended list, with a pass/fail + resolution).
33. **Deployment runbook** — step-by-step production deployment (laptop → homelab k3s → cloud).

## RULES (non-negotiable)

- **Zero code truncation.** Every function, every class, every manifest — fully written.
- **Every class has docstrings + full type annotations** (PEP 695 generic syntax where applicable, Python 3.12).
- **Every async function has structured exception handling + resource cleanup** (`async with`, `finally`).
- **Every external API call has timeout + retry + circuit breaker + fallback + metric + structured log.**
- **All secrets via Vault / SOPS / env — zero hardcoded credentials, tokens, or URLs with embedded auth.**
- **Every file starts with a module docstring** explaining purpose, author, and relationship to the architecture.
- **Every TODO has an owner + deadline** or it is not a TODO — convert to a GitHub issue reference.
- **Every magic number is a named constant** in `constants.py` with rationale comment.
- **Every cross-module import is explicit** — no `from module import *`.
- **Every boolean parameter is keyword-only.**
- **Every `datetime` is timezone-aware UTC** — enforce via ruff rule `DTZ`.
- **Production-grade**: assume this is deployed at 0900 Monday morning and must survive until Friday 1800 without a human touching it.

---

# ==============================================
# END OF AEGIS PULSE OMEGA v2 PROMPT
# ==============================================