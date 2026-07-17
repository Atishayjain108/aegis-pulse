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
| 1.2 | Make death loud (silence layers a–d + stale-analyze refusal) | NOT STARTED |
| 1.3 | Quarantine poisoned labels; settlement liveness precondition | NOT STARTED — do not touch until 1.2 gates pass |
| 1.4 | `set_shared_pool()` at composition roots; fail loud when missing | NOT STARTED |
| 1.5 | Clock fix: features bucket on `ts`, not `scraped_at` | NOT STARTED |
| 1.6 | Unreachable EXIT action: fix or delete + reachability assertion | NOT STARTED |
| 1.7 | One real notifier (ntfy); blackout alert before any verdict | NOT STARTED |
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
| DEBT-3 | `lint-and-test` and `integration` CI jobs: `integration` failed on PR #1 (needs live infra runners lack); `lint-and-test` outcome unverified at report time. The import-gate job is the only CI job proven green. | operator | both jobs green or their requirements documented as runner-incompatible |
| DEBT-4 | Prometheus single-file bind mounts pin the old inode: editing `config/prometheus/alerts.yml` on the host + `/-/reload` silently no-ops (host md5 ≠ container md5, observed 2026-07-17). Any config-file edit requires `--force-recreate` of the container. Same class as the image-venv divergence. | next config edit | mounts switched to directory mounts, or the recreate step is baked into the runbook |
| DEBT-2 | `INSTALL_OPTIONAL_ML=1` path still uses `uv pip install` (torch/onnx from a special index, intentionally outside uv.lock). Dormant (default 0), but it is the one remaining non-lockfile install in the image. | next person to enable ML in images | that path is lock-driven or explicitly waived in writing |
