"""Deterministic policy layer for Phase 4.

This package holds the pure functions that turn a ``ComposerInput`` into
an ``Alert`` and decide whether that alert is allowed to leave the
process. There is no I/O here — every function in this layer is unit-
testable with no infrastructure.

Public surface:

* ``compose`` — fuse Phase 2 + Phase 3 inputs into a single ``Alert``.
  Verdict precedence: ``BLOCK > EXIT > ENTER > DEGRADED > HOLD``.
* ``classify_priority`` — map numeric features to ``P0..P3`` (lower =
  more urgent). Never demotes an upstream priority.
* ``Deduper`` — content-addressable dedup over Redis with a local
  fallback. Safe to call on every alert; a duplicate emit is a no-op.
* ``Throttle`` — per-(tenant, channel) per-minute rate-limit (Redis or
  local).
"""

from __future__ import annotations

from aegis.execute.policy.classifier import classify_priority
from aegis.execute.policy.composer import compose
from aegis.execute.policy.deduper import Deduper
from aegis.execute.policy.throttle import Throttle

__all__ = [
    "Deduper",
    "Throttle",
    "classify_priority",
    "compose",
]
