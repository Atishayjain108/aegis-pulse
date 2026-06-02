"""Internal utilities for Phase 4. Keep this surface small and pure.

Everything here is:

* **deterministic** — same input ⇒ same output, no I/O, no env reads
  (except the ``time`` module's injectable clock, which is precisely
  *what makes* tests deterministic);
* **side-effect-free** — no network, no disk, no log emissions; and
* **fast** — these helpers are called on the hot path.

Public surface:

* Hashing — ``compute_alert_id``, ``compute_intent_id``,
  ``compute_dedup_hash`` (all SHA-256, hex-encoded, deterministic).
* HMAC — ``sign_payload`` / ``verify_payload`` for outbound webhooks.
* Time — ``utc_now`` / ``set_clock`` / ``reset_clock`` /
  ``Clock`` (injectable clock for tests).
* Backoff — ``next_backoff_seconds`` (decorrelated jitter, AWS recipe).
"""

from __future__ import annotations

from aegis.execute.utils.backoff import next_backoff_seconds
from aegis.execute.utils.hashing import (
    ALERT_ID_HEX_LEN,
    INTENT_ID_HEX_LEN,
    compute_alert_id,
    compute_dedup_hash,
    compute_intent_id,
)
from aegis.execute.utils.hmac_signer import sign_payload, verify_payload
from aegis.execute.utils.time import Clock, reset_clock, set_clock, utc_now

__all__ = [
    "ALERT_ID_HEX_LEN",
    "INTENT_ID_HEX_LEN",
    "Clock",
    "compute_alert_id",
    "compute_dedup_hash",
    "compute_intent_id",
    "next_backoff_seconds",
    "reset_clock",
    "set_clock",
    "sign_payload",
    "utc_now",
    "verify_payload",
]
