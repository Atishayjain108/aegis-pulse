# AEGIS-SCRAPE-0060 — Real-time pattern engine detection failed

## What it means

The PASS11 real-time pattern engine
(`src/aegis/scrape/pattern_engine.py`, invoked from `scrape_topic()`) raised
while attempting first-pass pattern recognition over the harvested signals.
This is a **non-fatal** condition: the harvest result is still returned with
its batch `patterns` intact; only the `realtime_patterns` enrichment is
skipped. The event is logged as `topic.realtime_pattern_failed`.

## Why it usually happens

* A signal object in the batch exposes a `title` / `platform` / `created_at`
  attribute of an unexpected type that the engine's coercion
  (`_as_dict`) could not normalise.
* The worker thread (`asyncio.to_thread`) was cancelled because the parent
  `scrape_topic()` coroutine was cancelled mid-harvest.
* An out-of-memory condition on an unusually large signal batch.

## Remediation

1. Inspect the `topic.realtime_pattern_failed` structlog event — it carries the
   underlying `error` string.
2. Confirm the signals feeding the harvest are `ProductSignal` instances or
   plain dicts with string `title` / `platform` fields.
3. Re-run the topic harvest: `uv run aegis topic "<topic>"`. Pattern detection
   is best-effort and retried on every harvest.

## If remediation fails

The batch clusterer (`detect_patterns`) is unaffected and still populates
`result.patterns`. Collect the failing topic string and the
`topic.realtime_pattern_failed` event, then open an issue — the engine should
never raise, so a reproducible failure indicates a coercion bug worth fixing.
