# AEGIS PULSE — USER MANUAL (Plain English)

> For someone who has the code but has never operated it. No jargon assumed.
> Read top to bottom once; after that, use it as a lookup. Every command here is
> real and runs from the repo root: `/home/atishayjain/code/aegis-pulse`.

---

## PART A — WHAT IS AEGIS, IN ONE PAGE

### A.1 What it actually does

AEGIS is a **robot research analyst for finding products to buy low and sell high** (arbitrage). It does four things on a loop, by itself:

1. **Watches the internet** — reads Reddit, Hacker News, news sites, Amazon, Flipkart, etc., looking for what people are talking about and buying.
2. **Thinks about it** — a team of 10 AI "agents" score each trend: Is demand rising? Is it legal to sell? What's the profit margin across countries? Should we act?
3. **Makes a call** — it produces a verdict: `ENTER` (act on it), `HOLD` (wait), or `BLOCK` (don't touch).
4. **Checks if it was right** — later it looks at what actually happened and grades its own predictions, so it gets smarter over time.

That fourth step is the whole point. A normal scraper only does steps 1–3. AEGIS closes the loop and **learns**.

### A.2 The honest current state (important — don't skip)

Your own internal audit found this, and you should operate accordingly:

- AEGIS is **fully built** and runs end-to-end.
- But its predictions are **not yet proven accurate** — it hasn't watched enough real outcomes yet to trust its confidence numbers.
- So today, treat every AEGIS verdict as a **research suggestion, not a trading order.** It points you at interesting things to investigate; you make the final decision.
- It earns trust only by running continuously for weeks and grading itself on real results. **The single most valuable thing you can do is leave it running.**

### A.3 The "phases" (just so the words aren't scary)

The code is organized into numbered phases. You don't need to memorize them — here's the plain-English map:

| Phase | Plain meaning |
|-------|---------------|
| 0 | Scraping (reading websites) |
| 1 | Storage (saving what it read into a database) |
| 2 | The 10 AI agents that think |
| 3 | Prediction (the ML scoring) |
| 4 | Alerts (telling you when something matters) |
| 5 | Doing it at scale (the "swarm" — 30+ sources at once) |
| 6 | Capital execution (actually placing/sourcing orders — OFF by default) |
| 7 | Geography (price gaps between countries) |
| 8 | Compliance (is it legal/safe to sell?) |
| 9 | Self-improvement (retraining) |
| 10–15 | Plumbing: data lake, LLM routing, security, testing, monitoring, backups |

When you hear "Phase 4 killswitch" or "Phase 7 geo," it's just one of these boxes.

---

## PART B — THE PIECES (what's running on your computer)

When AEGIS is "up," it's not one program — it's a set of cooperating services (each runs in a Docker container). Here's what each one is, in human terms:

| Service | What it is, plainly | You open it at |
|---------|--------------------|----------------|
| **postgres** | The database — the long-term memory. All signals/verdicts live here. | (not a webpage) |
| **redis** | The short-term scratchpad / message bus between services. | (not a webpage) |
| **minio** | File storage (like an S3 bucket on your machine). | http://localhost:9003 |
| **flaresolverr** | A helper that gets past "are you a robot?" blocks on shopping sites. | (internal) |
| **predict** | The prediction server (Phase 3). | http://localhost:8100 |
| **execute-api** | The alerts server (Phase 4). | http://localhost:8200 |
| **execute-drain** | Sends out the alerts that were queued. | (internal) |
| **autonomous** | **The scheduler — the heartbeat.** Runs scrape/analyze/learn jobs on a timer. | (logs only) |
| **dashboard** | The control panel webpage you actually look at. | http://localhost:8300 |
| **ollama** | A local AI model so it works without paid API keys. | http://localhost:11434 |
| prometheus / grafana / jaeger / loki | Monitoring & charts (optional). | 9091 / 3001 / 16687 / 3100 |

**The two that matter most to you:** the **dashboard** (what you look at) and the **autonomous scheduler** (the engine that does work while you sleep). If the scheduler isn't running, AEGIS does nothing on its own.

---

## PART C — GETTING IT RUNNING (step by step, first time)

### C.1 One-time setup

```bash
# Install all the code dependencies (run once, or after pulling new code)
uv sync --all-packages --all-extras
```

### C.2 Start everything

```bash
# Start the full stack including the scheduler ("autonomous"), local AI, and monitoring
docker compose \
  --profile autonomous \
  --profile dashboard \
  --profile local-llm \
  up -d

# Set up the database tables (safe to run repeatedly)
uv run alembic upgrade head
```

> **What is a "profile"?** Docker only starts the basic services by default. Profiles
> are switches that turn on extra ones. `--profile autonomous` = turn on the scheduler.
> Without it, **nothing runs on a loop.** Always include it if you want hands-off operation.

### C.3 Run the dashboard (recommended way)

The supported way to run the dashboard is **on your computer directly**, not in Docker:

```bash

uv run aegis dashboard serve
# Now open http://localhost:8300 in your browser
```

### C.4 Confirm it's alive

```bash
docker compose ps                 # every row should say "healthy"
uv run aegis status               # plain-English roll-up of all services
docker compose logs --tail=40 autonomous   # see the scheduler's jobs ticking
```

If something isn't healthy, jump to **PART H — When things break.**

---

## PART D — TELLING AEGIS WHAT TO WATCH (topics)

This is your steering wheel. AEGIS scrapes whatever topics you give it.

### D.1 Where topics come from

The scheduler reads one setting: **`AEGIS_AUTONOMOUS_TOPICS`**, a comma-separated list, stored in the **`.env`** file in the repo root.

If you don't set it, it falls back to these built-in defaults (from the code):
`dropshipping, print on demand, trending gadgets, ecommerce arbitrage, viral products, amazon trending`.

> Note: each scrape cycle (every 15 min) only processes the **first 3 topics** in your
> list, to stay within rate limits. So put your most important topics first, or list a
> wider set knowing it rotates through them over time. (To scrape more per cycle, that
> number is editable in `src/aegis/scheduler/autonomous.py` — ask me and I'll change it.)

### D.2 How to set your topics (the exact steps)

1. Open the file `.env` in the repo root with any text editor.
2. Find a line starting with `AEGIS_AUTONOMOUS_TOPICS=` (if there isn't one, add it).
3. Set it like this (commas separate topics, no quotes needed around the whole thing but quotes are fine):

```bash
AEGIS_AUTONOMOUS_TOPICS=POD t-shirts,dropshipping gadgets,skincare,supplements,phone accessories,solar gadgets,pet products,LED lighting,home fitness,kitchen tools
```

4. Save the file.
5. Restart the scheduler so it picks up the change:

```bash
docker compose up -d --no-build autonomous
```

That's it. Within 15 minutes it'll start scraping your new topics.

> **Two `.env` gotchas (both easy to hit):**
> 1. **Keep it on ONE line.** The whole `AEGIS_AUTONOMOUS_TOPICS=...` value must be a
>    single physical line. If your editor wraps it onto a second line, the config
>    loader breaks with `python-dotenv could not parse statement`. Don't press Enter
>    inside the value.
> 2. **No commas *inside* a topic.** Commas separate topics, so a topic itself can't
>    contain one. `women's bottom wear` is fine (spaces and apostrophes are OK);
>    `shoes, sneakers` would be read as *two* topics. Spaces around commas are fine
>    (`a, b` = `a` and `b`), but cleaner to skip them.

### D.3 How to choose GOOD topics

AEGIS is built for **physical-product arbitrage**. A good topic passes this test:

> *"If demand for this spiked, could I source it from a supplier and sell it somewhere for a profit?"*

- ✅ **Good:** "phone accessories", "skincare", "POD hoodies", "pet gadgets", "solar lights", "supplements" — specific, sellable consumer products.
- ❌ **Weak:** "technology", "news", "AI", "finance" — too broad, nothing to source/sell, produces noise.

Start with 8–12 niches you'd actually be willing to sell. You can always change them.

### D.4 Testing a topic right now (no waiting for the scheduler)

```bash
uv run aegis topic "phone accessories"
# expands the keyword → scrapes 6+ sources → removes duplicates → runs AI → prints a verdict
```

This is the fastest way to "feel" what AEGIS does. Try it with a few topics.

---

## PART E — THE EVERYDAY COMMANDS (your toolbox)

### E.1 Scraping (gathering data)

```bash
uv run aegis daily                      # scrape the standard free sources + analyze (the "one button")
uv run aegis topic "bitcoin"            # full cycle for one topic
uv run aegis swarm run                  # fire ALL 30+ sources at once (the big harvest)
uv run aegis swarm run --dry-run        # same but don't save to DB (just see what it'd get)
uv run aegis scrape --source hacker-news --limit 50   # one specific source
```

### E.2 Checking your data

```bash
uv run aegis signals tail --limit 20    # show the 20 most recent things it captured
uv run aegis swarm agents               # health table: which sources are UP / EMPTY / DOWN
uv run aegis patterns                   # find emerging themes in recent signals
uv run aegis report daily               # yesterday's summary
```

### E.3 Analysis (the AI thinking)

```bash
uv run aegis analyze --limit 20         # run the 10-agent pipeline on recent signals
uv run aegis analyze --no-llm           # same but heuristics only (no AI keys needed, faster)
```

### E.4 Learning & trust (what makes it smart)

```bash
uv run aegis trust report               # how well-calibrated are its predictions? (ECE / Brier)
uv run aegis evolve status              # self-improvement status
uv run aegis memory audit               # what has it learned? (writes KNOWLEDGE_AUDIT.md)
```

### E.5 Stack control

```bash
uv run aegis up                         # start services
uv run aegis down                       # stop (keeps data)
uv run aegis status                     # health roll-up
uv run aegis doctor                     # diagnose config/connection problems
uv run aegis tail                       # watch logs live
```

---

## PART F — UNDERSTANDING THE ADAPTERS (the data sources)

An "adapter" is a small piece of code that reads one website. AEGIS has ~34 of them.

### F.1 The truth about "all adapters up"

**You cannot get 100% of adapters green, and that's normal — not a bug.** Four are permanently broken because the *websites themselves* changed or block bots:

| Dead adapter | Why | Fixable? |
|--------------|-----|----------|
| tiktok | Needs paid TikTok Ads login | No |
| pinterest | Returns "403 forbidden" | No |
| nitter | All public servers are dead | No |
| instagram | Bans bots aggressively | Not safely |

So the real target is: **"every *working* adapter runs when I open the dashboard, and the dead ones are clearly marked, not silently failing."** ~30 adapters work and run together in the swarm.

### F.2 Seeing live adapter health

```bash
uv run aegis swarm agents
```

This prints each adapter with a status:
- **UP** — working, returning data.
- **EMPTY** — working but found nothing this time (not a failure!).
- **DOWN** — actually failing; it gets put on a cooldown automatically.

### F.3 The working ones (no API keys needed)

reddit-rss, hacker-news, github-trending, amazon, google-news, bing-news, google-trends, devto, producthunt, npm-trends, medium, techcrunch, wired, bbc, reuters, ndtv-profit, mint, business-standard, economic-times, moneycontrol, yahoo-finance, investing-com, flipkart, meesho, myntra, indiamart, ajio, nykaa, snapdeal, amazon-in, nse-bse, screener-in.

---

## PART G — THE DASHBOARD (what you're looking at)

Open **http://localhost:8300**. The dashboard aggregates everything. The key things to look at:

- **System Health** — are all services up?
- **Data Health** — how many signals, from which platforms, are they fresh?
- **Signal Health / recent signals** — the actual things it's capturing right now.
- **Live feed** — new verdicts stream in as they happen.
- **Ops console** — you can run `aegis` commands right from the browser (it streams output live).

### G.1 The Swarm page — "Run Swarm Now" button

The **🐝 Swarm** page has a **▶ Run Swarm Now** button (top, "Harvest Control").
Click it to fire a full multi-source harvest on demand — it runs in the background,
the button shows "⏳ Running…", and the run-history panels auto-refresh when it
finishes. Use this when you want fresh data immediately instead of waiting for the
15-minute scheduler tick. The page also shows per-adapter health (UP/EMPTY/DOWN).

### G.2 The Products page — visual product cards with images

The **🛍️ Products** page shows a visual grid of real products AEGIS captured from
e-commerce sites (Flipkart, Meesho, Nykaa, Myntra, Amazon IN, Snapdeal) — each card
has the **product image, title, price, discount, rating, and source**, and links
straight to the listing. Filter by platform with the dropdown, or hit **⟳ Refresh**.

This is how AEGIS "shows you the goods" instead of just a list of text. If the grid
is empty, you simply haven't harvested e-commerce sources yet — go to the Swarm page
and click **Run Swarm Now** (or run `uv run aegis swarm run`), then come back.

> Images are captured automatically during scraping and stored alongside each signal —
> no extra setup. Note FlareSolverr must be running for the protected shopping sites
> (it starts with the default Docker stack).

> A note on confidence numbers shown: until the learning loop has run for weeks,
> treat confidence scores as **advisory**. The dashboard is designed to show sample
> size (N) so you know how much to trust each number.

---

## PART H — WHEN THINGS BREAK (troubleshooting)

### H.1 First moves for almost any problem

```bash
docker compose ps | grep -v healthy            # what's NOT healthy?
docker compose logs --tail=100 <service>       # read that service's errors
docker compose up -d --no-build <service>      # recreate it (picks up .env changes)
uv run aegis doctor                            # automated config/connection check
```

### H.2 Common specific problems

**"Nothing is happening / no new signals."**
→ The scheduler probably isn't running. Start it:
```bash
docker compose --profile autonomous up -d autonomous
docker compose logs --tail=40 autonomous       # confirm jobs are ticking
```

**"A shopping site (Flipkart/Myntra) returns nothing."**
→ flaresolverr (the anti-bot helper) may be down:
```bash
docker compose up -d flaresolverr
```

**"The CLI can't connect to the database."**
→ The database connection string in `.env` must point at port **5433**:
`AEGIS_PG_DSN=postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis`

**"No alerts are going out."**
→ The killswitch may be tripped (a safety brake). Check and release it:
```bash
uv run --package aegis-execute aegis-execute killswitch state
uv run --package aegis-execute aegis-execute killswitch arm --reason "all clear"
```

**Everything is confusing / I need a full diagnostic to share.**
```bash
uv run aegis support-bundle      # makes a diagnostic zip
```

---

## PART I — FREQUENTLY ASKED QUESTIONS

**Q: Does AEGIS spend my money or place real orders?**
A: No. The capital/execution part (Phase 6) defaults to **"advisory" mode** — it runs the analysis but does NOT place real orders. Live trading requires deliberately changing a setting. Out of the box, zero money is at risk.

**Q: Do I need to pay for AI / API keys?**
A: No. It runs on a local AI model (Ollama) and free RSS/HTML sources. Paid keys (Groq, OpenAI, Reddit, YouTube) are optional and only improve quality/quantity.

**Q: How long until AEGIS is "trustworthy"?**
A: It needs to make predictions and then watch real outcomes for them — your audit suggests at least ~200 forward-settled outcomes before any part earns real authority. In practice: leave the scheduler running for several weeks, then check `aegis trust report`.

**Q: How do I make it gather MORE data?**
A: Three levers: (1) add more topics in `AEGIS_AUTONOMOUS_TOPICS`, (2) run the full `aegis swarm run` regularly, (3) add new adapters (StackOverflow/Etsy/Google Shopping are planned). The rule: **only real data, never fake/synthetic rows.**

**Q: What's the difference between `aegis daily`, `aegis topic`, and `aegis swarm`?**
A: `daily` = a fixed set of standard free sources + analysis (the simple daily button). `topic "X"` = deep dive on one keyword across many sources. `swarm` = fire all 30+ sources at once (the biggest harvest).

**Q: Why are some adapters always "DOWN"? Did I break something?**
A: No — see Part F. Four sources are permanently dead because the websites block bots. This is expected.

**Q: Where is the data stored?**
A: In the Postgres database (long-term) and MinIO (files). It survives restarts. `docker compose down` keeps it; `docker compose down --volumes` **wipes it** — be careful with that one.

**Q: What is the "swarm"?**
A: A mode that runs 30+ data sources in parallel waves (so one slow site doesn't block the others), then merges and de-duplicates the results. It's how AEGIS gathers a lot, fast.

**Q: How do I stop everything?**
A: `uv run aegis down` (keeps your data). To also free up resources, that's enough.

**Q: Where do I see actual product pictures, not just text?**
A: The **🛍️ Products** page on the dashboard (Part G.2). It shows image cards for
products scraped from e-commerce sites, with price/discount/rating and a link to the
listing. If it's empty, run a swarm harvest first (Swarm page → **Run Swarm Now**).

**Q: How do I get fresh data right now without waiting?**
A: Dashboard → **🐝 Swarm** page → **▶ Run Swarm Now**, or run `uv run aegis swarm run`
in a terminal.

**Q: I want to understand the deep operational details.**
A: See `OPERATOR_MODE.md` in this repo — it's the advanced runbook with exact health-check commands, the full scheduler job table, and the build roadmap.

---

## PART J — YOUR FIRST WEEK (a simple plan)

**Day 1:**
1. `uv sync --all-packages --all-extras`
2. Set `AEGIS_AUTONOMOUS_TOPICS` in `.env` to ~10 sellable niches (Part D.3).
3. Start the stack with the `autonomous` profile (Part C).
4. `uv run aegis topic "phone accessories"` — watch it work end to end.

**Day 2–7 (each morning, ~5 min):**
```bash
docker compose ps | grep -v healthy                          # all green?
uv run aegis status                                          # roll-up
uv run aegis signals tail --limit 20                         # fresh data landing?
uv run aegis swarm agents                                    # adapter health
# Open http://localhost:8300 and skim the feed
```

**End of week 1:**
- `uv run aegis trust report` — see how its predictions are calibrating.
- Adjust your topic list based on what produced interesting signals.

**The golden rule:** keep the scheduler running. AEGIS gets smarter only while it's alive and watching real outcomes.

---

*This manual covers operation. For the architecture deep-dive see `CLAUDE.md`; for advanced ops see `OPERATOR_MODE.md`; for a plain-English architecture tour see `SYSTEM_TOUR.md`.*
