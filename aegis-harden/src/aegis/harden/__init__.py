"""
AEGIS Pulse — Phase 5: Adversarial Hardening.

This package layers defensive controls over Phase 0-4:
  * TLS (JA3/JA4) + HTTP/2 fingerprint diversity for the scraper (Phase 1).
  * Data-driven per-source playbooks (YAML) that govern delays, retries,
    proxy posture, and stealth knobs for each upstream.
  * Honeypot avoidance — URL/DOM hygiene before any click.
  * Randomized-smoothing wrapper around Phase 3 inference for adversarial
    robustness on numeric feature vectors.
  * Data-poisoning detectors (label-flip, feature-shift, gradient anomaly)
    for the Phase 3 training pipeline.

Doctrine — same as Phase 2 / Phase 3:
  * Every module produces a deterministic verdict from numeric features.
  * Optional heavy deps (sklearn, prometheus) degrade gracefully when absent.
  * Verdicts are reproducible and auditable; no hidden global state.
"""

from aegis.harden.constants import (
    DEFAULT_HARDEN_PROFILE,
    PHASE5_VERSION,
)

__all__ = ["DEFAULT_HARDEN_PROFILE", "PHASE5_VERSION"]
