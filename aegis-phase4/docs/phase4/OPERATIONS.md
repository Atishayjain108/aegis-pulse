# Phase 4 Operations Manual

## Daily checks (≤2 min)

1. **API health** — `curl http://<host>:8200/healthz` → `{"status":"ok"}`.
2. **Readiness** — `curl http://<host>:8200/readyz`. Check the
   `killswitch` field (`ARMED` is normal; `TRIPPED` means dispatch is
   halted; `UNKNOWN` means the Redis backend is unreachable).
3. **Pending outbox depth** — read `pending_outbox` from `/snapshot`.
   Spikes > 100 sustained mean drainer cannot keep up or a channel is
   stuck.
4. **Killswitch state** — `aegis-execute killswitch state`.
5. **Recent verdict mix** — open the dashboard `/dashboard/`. A burst of
   `BLOCK` or `DEGRADED` typically signals upstream input drift.

## Operating the killswitch

The killswitch is a global **dispatch halt** — it leaves alerts in the
outbox (and stops new ones from being enqueued via the Pipeline). It is
designed for incident response, not steady-state filtering.

```bash
# Inspect
aegis-execute killswitch state

# Trip (reason is required and audited)
aegis-execute killswitch trip --reason "auditor flagged off-policy ENTERs at 14:02"

# Arm
aegis-execute killswitch arm --reason "root cause patched, smoke tests green"
```

The reason ends up in `killswitch_audit` (the only table without RLS),
keyed by actor (`bearer` or `anonymous` for the API; `cli` for the CLI).

## Common incidents

### 1. Outbox depth keeps growing

**Likely causes:**

- A notifier endpoint is down → the drainer retries with backoff but
  rows keep accumulating.
- Drainer process is not running.
- Killswitch is tripped.

**Diagnose:**

```bash
# Is the drainer running?
ps -ef | grep "aegis-execute drain"

# Is the killswitch tripped?
aegis-execute killswitch state

# What's failing? Tail structured logs for "drainer.send_failure".
journalctl -u aegis-execute-drain -f
```

**Fix:** restart the drainer; if a channel is permanently broken,
disable it by clearing the relevant env var and restarting (the
`ChannelRegistry` will then skip it).

### 2. Alert keeps re-firing for the same trend

The Phase 4 alert_id is content-addressable over
`(tenant, trend, decision_window, verdict, priority)`. Two distinct
windows or a verdict change produces a distinct alert by design.

If you want to suppress repeats more aggressively, expand `Deduper.ttl_s`
or move the deduper to Redis (it already supports it).

### 3. Killswitch stuck `TRIPPED` after Redis restart

The killswitch reads from Redis on every check. After a Redis restart
the key is gone; the switch is ARMED. If the application has cached
state (e.g. a stale state file), restart the application — Phase 4 does
not cache killswitch state in-process.

### 4. SSE clients see no events

- Confirm `EventBus.subscriber_count` via `/readyz` (`subscribers`
  field).
- Reverse proxies (NGINX, Cloudflare) must have streaming buffering
  disabled. The route already sets `X-Accel-Buffering: no` for NGINX.
- A slow client gets old events dropped silently (see
  `bus.subscriber_drop` logs). If this is happening at scale, scale the
  client side or use durable channels (Telegram, ntfy) instead of SSE.

### 5. HMAC verification fails on the webhook receiver

The signature is over the **exact request body** (JSON, sort_keys=True,
no spaces). Receivers must hash the raw bytes, not a re-serialised
version.

The signing header is `X-Aegis-Signature: <hex SHA-256>`.

Reference verifier (Python):

```python
import hmac, hashlib

def verify(key: str, body: bytes, signature_hex: str) -> bool:
    expected = hmac.new(key.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_hex)
```

## SLOs

| SLI | Target | How measured |
|---|---|---|
| End-to-end composition latency (p95) | < 50 ms | `execute.compose` log; not a metric yet |
| Outbox drain interval | 1 s (configurable) | `AEGIS_EXECUTE_DRAIN_INTERVAL_S` |
| Alert delivery latency (p95) | < 10 s | `alert_deliveries.latency_ms` |
| Drainer success rate (last 1h) | > 99% | `(SUCCESS / total) FROM alert_deliveries WHERE created_at > now()-'1h'` |
| Killswitch trip propagation | < 2 s | Drainer reads on every tick (default 1 s) |

## Capacity rules of thumb

- 1 drainer @ batch=25 every 1s → ~25 alerts/s ceiling per tenant
- Bump `DRAIN_BATCH` for higher throughput; bump `NOTIFY_TIMEOUT_S`
  cautiously (long timeouts compound when channels are slow).
- Each tenant should have its own drainer process for isolation.

## Reset procedures

### Hard-reset the outbox (DEV ONLY)

```sql
-- Inside the aegis DB:
TRUNCATE alert_outbox;
TRUNCATE alert_deliveries;
```

(`alerts` is the immutable history; do not truncate.)

### Replay a delivered alert through a channel

This is by design **not** built in. Treat `alerts` as the source of
truth and reach out to the relevant channel manually (e.g. forward the
alert to a Slack channel via copy-paste). Rationale: replays risk
duplicate trades downstream.
