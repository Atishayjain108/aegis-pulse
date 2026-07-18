# RECOVERY PROTOCOL — canonical in-repo copy

First materialized 2026-07-17. Until this date the protocol existed only in
the operator's work-order channel; it is committed here so that amendments
are diffs, not memories. Amendments are applied in place with the original
text preserved via strikethrough, and logged in the AMENDMENT LOG.

Companion evidence: the 2026-07-15 total-system audit (chat transcript,
findings cited as §N throughout) and STAGE gate reports.

---

## OPERATING RULES (unchanged, abridged)

1. No new features. Deletion and reconnection only.
2. Every claim backed by a runnable command and readable output.
3. Verification = live DB query or live container probe. Fixture-injected
   unit tests are what produced the §3.5 failure (three safety mechanisms
   written, tested, documented as done — inert in production).
4. Destructive ops: COUNT first, quarantine copy, before/after printed.
   Nothing is hard-deleted.
5. Stop and report at every GATE. A failed gate is information.
6. Strict stage order. STAGE 1 blocks everything.
7. Zero paid APIs. Zero new dependencies.
8. Invariants: `datetime.now(UTC)`; Pydantic frozen/`extra="forbid"`;
   stream field `"body"`; `aegis:phase2:graph_results` maxlen=10000;
   sentinel before compliance; RLS via `SET app.current_tenant`.

Banned vocabulary in all reports: "✅ Green", "N tests pass",
"coverage X%", phase-status tables.

---

## STAGE INDEX

| Stage | Content | Status (2026-07-17) |
|---|---|---|
| 1.1 | Resurrect ingestion; image-level import gate | GATE PASSED — commit `004045f`, tag `stage-1.1-import-gate`; 667 rows/cycle after 13 days of zero |
| A′ (inserted) | Scheduler proves itself unprompted | GATE PASSED — 13 unprompted hour-buckets 2026-07-16 09:00–21:00; boot→rows in 6 min on 2026-07-17 |
| B (inserted) | Close the dependency-drift class (lockfile-driven images) | GATE PASSED — see AMENDMENT LOG context |
| 1.2 | Make death loud (silence layers a–d + stale-analyze refusal) | GATE D PASSED 2026-07-17 — commit `132dfbf`; refusal, SQL healthcheck, zero-yield ERROR all demonstrated live |
| 1.3 | Quarantine poisoned labels; settlement liveness precondition | GATE PASSED 2026-07-17 (with finding DEBT-5) — 633 flagged FALSE + quarantine snapshot; migration 0028; precondition live in settler + both backfills; 11 consumer queries gated; void/settle discrimination demonstrated |
| 1.4 | `set_shared_pool()` at composition roots; fail loud when missing | GATE PASSED 2026-07-18 — roots wired at analyze CLI + scheduler startup (Phase 4 already injects `pool=` explicitly everywhere — documented deviation, no third root needed); 3 consumers ERROR outside test env; live proof: `predictions` 0→2 and stream `confidence_basis: "calibrated"` (raw 0.7338 → calibrated 0.4675). First real map load also exposed+fixed a JSONB/str crash in `Calibrator.from_json` that fixtures never hit |
| 1.5 | Clock fix: features bucket on `ts`, not `scraped_at` | GATE PASSED 2026-07-18 — `ts AS captured_at` (scraped_at still selected); on clock-skewed rows (7,135/7,271 diverge >1h) the same 40 rows: harvest-clock crammed all 40 into one bucket (v1h +3.71 batch-spike artifact) vs event-clock distributed (last-bucket count 0, v24h +2.77); 103 cells differ. Fresh-content rows legitimately show no diff (clocks agree there) |
| 1.6 | Unreachable EXIT action: fix or delete + reachability assertion | GATE PASSED 2026-07-18 — EXIT ceiling 0.65→0.60 (max p_decline = 0.85×0.75 = 0.6375; action-distribution change stated: high-confidence DECLINING/SATURATED can now EXIT); `action_reachability()` + startup assertion in InferenceRunner; table: all 5 actions reachable |
| 1.7 | One real notifier (ntfy); blackout alert before any verdict | GATE PASSED 2026-07-18 — scheduler `_notify_ops` wired to zero-yield + stale-refusal (blackout message FIRST); Phase 4 NtfyNotifier filtered to P0/ENTER only; one operator var `AEGIS_NTFY_TOPIC` powers both. Live: blackout 200 from inside container, ENTER/P0 200, HOLD/P2 skipped-filtered, both stored at ntfy.sh. Demo caught+fixed an ASCII-header crash on first live send. Operator action: set `AEGIS_NTFY_TOPIC` in `.env` + subscribe on phone |
| 2 | Let it run 30 days; ≥500 clean claims; weekly numbers only | BLOCKED on Stage 1 |
| 3 | Delete/graveyard (parallel with Stage 2) | Kill list AMENDED — see below |
| 4 | Only if GATE BETA survives | Do not plan |

---

## STAGE 3 KILL LIST (as amended 2026-07-17)

| Target | Basis | Action |
|---|---|---|
| ~~"~20 zero-lifetime-row adapters — §2.1 — registered >30d, 0 rows ever"~~ | ~~audit §2.1 census~~ | **STRUCK — see Amendment 1. The zero-row criterion is VOID.** |
| Re-derived adapter kill list | 7-day rule below | Quarantine flag + weekly probe only after the window completes |
| datalake/ Gold/Silver + Prefect (6,501 LOC) | §2.4 — zero readers outside `datalake/` itself | Mothball; do not extend |
| mentor/ (3,400 LOC) | §3.1 — changes no decision | Graveyard |
| Neural predictors (PatchTST/Autoformer/TimesNet/HGT, ~1,300 LOC) | nothing to train on; floor unvalidated | Graveyard until a label exists |
| hour/day sin/cos feature dims (4 of 24) | §1.1 — clock encodings, pure noise at current density | Remove from FEATURE_DIM; fix docs (which still say 20) in the same commit |

Graveyard mechanics: nothing hard-deleted; moves to `_graveyard/` with a
one-line reason; reversal is one command; reversal point is tag
`stage-1.1-import-gate`. GATE 3 per deletion: one full analyze cycle
before and after, same verdict, or revert.

Do not build, ever, under this protocol: multi-tenant scale-out; more
notification channels beyond one; LiteLLM proxy work; any new phase,
agent, or memory system.

---

## AMENDMENT LOG

### Amendment 1 — 2026-07-17 — the zero-row kill criterion is VOID

STRUCK from STAGE 3: "~20 zero-lifetime-row adapters — §2.1 — registered
>30d, 0 rows ever."

Reason, verbatim from the closure order:

> The zero-row criterion is VOID. Those adapters never loaded — 14 of them
> have been unimportable in the autonomous container since it was first
> built, predating the July 2 remediation entirely. They were never blocked
> by websites and were never fairly tested. The 2026-07-16 post-fix cycle
> already shows previously-"dead" adapters landing rows (youtube_rss 20,
> snapdeal 10, myntra 9, flipkart 20, amazon_in 15).

Replaced with:

> Re-derive the kill list after 7 consecutive days of a working image AND a
> scheduler proven to run unprompted (GATE A′). Any adapter with zero rows
> across 7 days of successful, importable, actually-executed, unprompted
> cycles is a legitimate deletion candidate. Nothing before that date is
> evidence of anything.

7-day window start: **2026-07-17** (first day with a lockfile-driven image,
a passing in-image import walk, and GATE A′ passed). Earliest legitimate
kill-list derivation: **2026-07-24**, from
`SELECT platform, COUNT(*) FROM signals WHERE scraped_at >= '2026-07-17' GROUP BY 1`
joined against the adapter registry.

### Note — the §2.1 census is suspect end to end

The audit's §2.1 census — and every conclusion drawn from it about which
platforms "block" AEGIS — is now suspect end to end. The 403/IP-ceiling
narrative for India commerce adapters may be partly or wholly an import
failure wearing a network failure's clothes. Do not act on that narrative
until the 7-day window produces real per-adapter data.

---

## OPEN DEBTS

| ID | Debt | Owner | Closes when |
|---|---|---|---|
| DEBT-1 | ~~`autonomous-image-imports` CI job has never executed on a GitHub runner.~~ **CLOSED 2026-07-17.** Green: run 29557944440 job 87814055718 (success, PR #1). Red: run 29558104544 job 87814527006 (failure, deliberately broken import, module named in log; falsify PR #2 closed, branch deleted). Context: GitHub had **zero** registered workflows before PR #1 — the July "CI lane" never existed as far as GitHub was concerned. | — | closed |
| DEBT-3 | **CLOSED 2026-07-18.** `integration` workflow **completed success** on a GitHub runner (run 29631379371, commit 95f3d72) — its first green in the workflow's existence — after: the 0011 fresh-replay fix (DEBT-6), missing `lake`/`db_pool` fixtures created (the suite referenced fixtures that never existed anywhere), pgbackrest tests gated on binary+writable-repo, and the 20-dim drift-test relic fixed to `EVOLVE_FEATURE_DIM`. `ci` workflow also green (run 29631379360). | — | closed |

## PR DISPOSITIONS

**PR #1 — HELD OPEN, deliberately.** Not merged: Stage 1 is mid-flight and each pushed block re-runs the full CI lane on the same PR, which is exactly the feedback we want. Merge decision belongs to the operator at a stage boundary (earliest sensible point: after GATE ALPHA / all of Stage 1). Until merged, `main` has no CI workflows registered — branch pushes to this PR are the only thing exercising them.

## UNKNOWNS (open observations — a later green run does NOT retroactively resolve these)

| ID | Unknown | Status |
|---|---|---|
| UNK-1 | Why `aegis-prometheus` originally sat `Exited(127)` on 2026-07-15/16. It did not recur across multiple recreations on 2026-07-17. Cause unestablished; "it stopped happening" is not a resolution. | OPEN |
| UNK-2 | Why the pre-Block-B autonomous container produced zero docker-log output while demonstrably running jobs. The silent variable removed was the `uv run` wrapper (PYTHONUNBUFFERED was set in-image in both eras) — the precise sink inside that wrapper was never traced. | OPEN |
| DEBT-4 | Single-file bind mounts pin the old inode: editing the host file + reload silently no-ops (host md5 ≠ container md5, observed live on `alerts.yml` 2026-07-17 — "200 OK, changed nothing"). **Blast radius: 5 single-file binds in docker-compose.yml** — `prometheus.yml`, `alerts.yml` (prometheus), `loki.yaml` (loki), `config.yaml` (promtail), `litellm_config.yaml` (litellm). Any habit-skipped `--force-recreate` reproduces the lie on any of them. **Audit/convert these before STAGE 4; do not fix now.** | next config edit / STAGE 4 | mounts switched to directory mounts, or the recreate step is baked into the runbook |
| DEBT-5 | **DISPOSITION (c) RECORDED 2026-07-18 — tiered thresholds.** Alarm freshness stays 2h (`AEGIS_INGEST_HEALTH_MAX_AGE_H`, healthcheck); settlement label validity is now `AEGIS_SETTLE_MAX_GAP_H` = **12h** (tolerates a nightly laptop sleep; still voids the multi-day-blackout class). Historical flags re-derived **per-row** under 12h — no more blanket TRUE: clean pool = **106 rows** (91 correct / 15 incorrect; rise n=101 @ 88.1%, fall n=5 @ 40%). ⚠ Selection-bias caveat: windows that stayed continuous are precisely the intensely-scraped periods, which mechanically favors correct "rise" claims — the surviving pool is NOT an unbiased skill measurement; Stage 2's forward claims under the live precondition remain the only honest measurement. Reversible by env. Original finding kept below for the record. | closed (operator may revisit threshold) | — |
| DEBT-5 (original finding) | **Historical label contamination is total under the protocol's own standard.** Retroactive gap-audit (2026-07-17): **0 of 973** pre-death settled windows meet the 2h-continuity definition of "scraper alive" — avg max in-window gap 46.9h of a 72h window, 842/973 have >24h gaps, worst = fully empty window. The 53.0% rise-accuracy baseline was measured through windows averaging 47 blind hours. Flags left TRUE per the Stage-1.3 order (documented choice); operator must decide: (a) accept history at this documented lower standard, (b) void it (clean pool → 0, calibration map unfittable until new claims accrue), or (c) tier the threshold. **Forward consequence: on a laptop that sleeps >2h, EVERY 72h claim voids** — observed live (the first control claim voided on a real 06:30→12:02 sleep gap). Stage 2's ≥500 clean claims require 24/7 uptime, a raised threshold, or shorter horizons. This decision gates Stage 2 and is the operator's. | operator | a written decision on (a)/(b)/(c) recorded here |
| DEBT-6 | **The live database is not reproducible from the migration chain.** Fresh replay (integration CI, first-ever from-zero run) died at 0011: `0001_init.sql` ships a conflicting `prediction_outcomes` shape that production does not have — production's copy was dropped out-of-band, and the June observation record shows `aegis_sql_revisions` listing only 0001–0005 while policies from 0011/0015/0016/0017 were live. The 0011 fresh-replay collision is FIXED (documented `DROP TABLE IF EXISTS` in 0011; full chain 0001→0028 verified replaying clean against ephemeral timescaledb-ha:pg16, fresh shape == live shape). The bookkeeping divergence on the live DB remains un-reconciled. | operator / next migration | `aegis_sql_revisions` on the live DB reconciled with reality, or formally documented as advisory-only |
| DEBT-2 | `INSTALL_OPTIONAL_ML=1` path still uses `uv pip install` (torch/onnx from a special index, intentionally outside uv.lock). Dormant (default 0), but it is the one remaining non-lockfile install in the image. | next person to enable ML in images | that path is lock-driven or explicitly waived in writing |
