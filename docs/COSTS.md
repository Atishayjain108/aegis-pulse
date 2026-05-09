# AEGIS Pulse — Cost Breakdown

> **TL;DR**: Phase 1 runs at **$0/month** on a developer laptop.
> Every paid service has a working free / OSS alternative. This
> document is the live ledger of what's free, where the free tier
> ends, and the cheapest credible upgrade when you outgrow it.

---

## 1. The zero-budget ledger

| Function | Free-tier path | Cap | When to upgrade |
| --- | --- | --- | --- |
| LLM inference | Ollama (local) — `qwen2.5:14b`, `llama3.3:8b` | Bound by GPU/CPU | Add Groq free when you need >50 req/min |
| LLM cloud burst (1) | Groq free tier — `llama-3.3-70b-versatile` | ~14k req/day | Move to paid Groq at sustained 10k+/day |
| LLM cloud burst (2) | OpenRouter free models | Variable | Per-model rate limits |
| LLM multimodal | Google AI Studio — `gemini-2.0-flash` | 1.5k req/day | Vertex AI paid |
| Object storage (primary) | MinIO self-hosted | Disk size | Offload cold tier when disk > 500 GB |
| Object storage (DR) | Backblaze B2 free | 10 GB | $5/TB/month after that |
| Compute (always-on) | Laptop + Oracle Cloud Free Tier (4 ARM vCPU, 24 GB) | Indefinite | Hetzner CPX21 €5/mo |
| Postgres (primary) | Self-hosted TimescaleDB in Docker | Disk | — |
| Postgres (DR) | Neon free tier | 0.5 GB | $19/mo at 10 GB |
| Redis | Self-hosted in Docker | RAM | — |
| Observability | Self-hosted Prometheus + Grafana + Loki | Disk | — |
| Tracing | Jaeger all-in-one (in-memory) | RAM | Tempo at scale |
| Error tracking | Sentry self-hosted | Disk | — |
| Alerts (push) | ntfy + Telegram + Discord webhooks | Unlimited | — |
| Alerts (SMS) | — | — | Twilio paid only |
| Maps / geo | OpenStreetMap Nominatim (self-host) | Unlimited | — |
| FX rates | exchangerate.host (free) | Unlimited | OpenExchangeRates paid for SLA |
| Search trends | pytrends (Google Trends) | Variable rate-limit | SerpAPI paid |
| Reddit data | PRAW (free) | 60 req/min | — |
| YouTube data | Data API v3 free | 10 000 units/day | YouTube paid analytics |
| TikTok data | Creative Center scrape | Per IP | TikTok Research API (gated, free for academia) |
| Hacker News | Algolia HN API | Unlimited | — |
| Cloudflare bypass | FlareSolverr (self-host) | Bound by host | 2Captcha paid for hard cases |
| Container registry | GitHub Container Registry free | 500 MB / public unlimited | $0.25/GB beyond |
| CI | GitHub Actions free | 2 000 min/mo | $0.008/min beyond |
| Domain | — | — | Cloudflare Registrar at-cost |
| TLS | mkcert (local) + Let's Encrypt (prod) | Unlimited | — |

**Phase 1 total cost on the documented free-tier path: $0.00 / month.**

---

## 2. The Oracle Cloud Free Tier — your always-on free server

Oracle Cloud's "Always Free" tier is the only cloud free tier
generous enough to run AEGIS Pulse 24/7 without a paid escape valve.
You get **forever-free** (not trial — actually forever):

- **4 ARM-based Ampere A1 vCPUs** (or 2 AMD x86 if you prefer)
- **24 GB RAM**
- **200 GB block storage**
- **10 TB outbound bandwidth / month**
- 2 ARM machines or 1 split into smaller pieces — your choice

That single ARM VM can comfortably host the entire Phase 1 stack
(Postgres, Redis, MinIO, Prometheus, Grafana, the scraper workers)
with headroom to spare.

### Provisioning walkthrough

1. **Sign up** at <https://www.oracle.com/cloud/free/>.
   You will be asked for a credit card for identity verification only —
   you cannot be charged on the Always-Free tier.
2. After signup, in the OCI console: **Compute → Instances → Create Instance**.
3. **Image & shape**: pick **Canonical Ubuntu 22.04** for ARM, then
   change shape to **Ampere A1.Flex** with 4 OCPU and 24 GB RAM.
   *(If "out of host capacity" — try a different region: Mumbai, Hyderabad,
   Frankfurt, and São Paulo are usually available. London and US East are not.)*
4. **Networking**: take the defaults; the wizard auto-creates a VCN.
   Save the public IP address printed at the end.
5. **SSH key**: paste your `~/.ssh/id_ed25519.pub` from the AEGIS dev WSL.
6. **Open ports**: in the Security List for the new VCN, add ingress
   rules for TCP 22 (your IP only), TCP 80, TCP 443.
   Keep 5432, 6379, 9000-9001, 9090, 3000, 16686 **closed** to the
   internet — tunnel them via SSH instead.
7. **Connect**:
   ```bash
   ssh ubuntu@<public-ip>
   ```
8. **Bootstrap** (the same Phase 0 scripts work — just run the WSL
   subset, since this is already Linux):
   ```bash
   sudo apt update && sudo apt install -y git
   git clone <your-repo> aegis-pulse
   cd aegis-pulse
   bash bootstrap/wsl/01_system.sh
   bash bootstrap/wsl/02_python.sh
   bash bootstrap/wsl/04_services.sh   # Docker; skip the Windows pieces
   ```
9. **Run**: `aegis up && aegis migrate && aegis scrape --source hacker-news`.
10. **Tunnel the dashboards from your laptop**:
    ```bash
    ssh -L 3000:localhost:3000 -L 8000:localhost:8000 -L 16686:localhost:16686 ubuntu@<ip>
    ```
    Then open `http://localhost:3000` in your laptop browser as usual.

### Operational tips for ARM VMs

- Almost all our images (Postgres, Redis, MinIO, Prometheus, Grafana,
  Jaeger, FlareSolverr) ship `linux/arm64` variants. The compose file
  resolves these automatically.
- Playwright on ARM: `playwright install --with-deps chromium` is
  supported. Firefox builds are also published.
- **Backup**: schedule `pgBackRest` to an off-host location — even
  Backblaze B2's free 10 GB is enough for the metadata-only signal
  index.

---

## 3. Hetzner Cloud — the cheapest credible upgrade

When the Oracle free instance becomes a bottleneck (typically once
ingest exceeds ~30 000 signals/hour), the cheapest credible upgrade is
Hetzner Cloud:

| Plan | vCPU | RAM | Disk | Price |
| --- | --- | --- | --- | --- |
| CX22 | 2 | 4 GB | 40 GB | ~€3.79/mo |
| CX32 | 4 | 8 GB | 80 GB | ~€6.49/mo |
| CPX31 | 4 AMD | 8 GB | 160 GB | ~€10.59/mo |
| CCX13 (dedicated) | 2 | 8 GB | 80 GB | ~€14.86/mo |

Same `aegis` workflow applies — the bootstrap scripts run unchanged.

---

## 4. The escalation ladder

The day comes when the free-tier path can't keep up.  In ascending order
of cost-per-month, the recommended upgrade ladder is:

1. **Move from laptop to Oracle Free Tier ARM VM.** $0/mo, frees the laptop.
2. **Add a Hetzner CX32.** ~€6.49/mo, runs the heavy scrapers off-VM.
3. **Add Backblaze B2 paid tier** for cold audit data. $0.005/GB.
4. **Move LLM bursts to paid Groq** when sustained > 10k req/day. ~$0.05/M tokens.
5. **Move Postgres to Neon paid** when DB > 5 GB. $19/mo for 10 GB.
6. **Add a managed CDN** when serving the dashboard externally.
   Cloudflare's free tier is fine until ~5M requests/mo.
7. **Add a managed observability stack** (Grafana Cloud free → paid)
   when you no longer want to babysit Prometheus retention.

Every step is reversible. Most upgrades involve nothing more than a
DSN change in `.env`.

---

## 5. Tracking spend

- `aegis report daily` includes a one-line cost ledger when you
  configure paid providers in `.env`.
- A future `cost-report` subcommand (Phase 18 in the master spec) will
  reconcile actuals across providers and project 30 days forward.
- Until then: every paid provider's web console has a billing page —
  bookmark them, set a hard spending alert at 50 % and 80 % of your
  budget, and revisit monthly.

The discipline that keeps this project at $0/mo is unglamorous:
**every paid feature is gated behind an explicit env var.** If the env
var is missing, the system falls back to the free path. There is no
"oh I forgot to turn this off" risk.
