"""ADP-6 — self-healing schema-drift tracker.

``schema_guard.validate_batch`` already detects per-batch validation drops, but
historically it only *logged* them: a feed whose response structure silently
changed would keep dropping signals on every run while still reporting SUCCESS.

This module closes the loop. It accumulates a per-platform exponentially-weighted
drop-rate and exposes :func:`SchemaDriftTracker.is_drifting`, which the swarm
consults after each run to quarantine (cool down) an adapter whose output shape
has drifted beyond a threshold — turning a silent corruption source into an
explicit, recoverable health state.

PASS2-2F adds the *recovery* half of the loop: a quarantined adapter that
starts producing clean batches again is automatically un-quarantined after
``_UNQUARANTINE_CLEAN_BATCHES`` consecutive clean batches drag its smoothed
drop-rate below ``_UNQUARANTINE_DROP_RATE``. Every quarantine carries a typed
:class:`QuarantineReason` so the dashboard can distinguish "schema broke" from
"no API key configured".

Pure in-process state (single event loop) → a module-level singleton is safe.
"""
from __future__ import annotations

from enum import Enum

import structlog

_log = structlog.get_logger("aegis.scrape.schema_drift")

# EWMA smoothing for the per-platform drop-rate. Higher = more reactive.
_EWMA_ALPHA = 0.4
# A platform is "drifting" once its smoothed drop-rate crosses this fraction…
_DRIFT_DROP_RATE = 0.5
# …but only after we have observed at least this many signals total (avoid
# quarantining on a single tiny noisy batch).
_MIN_OBSERVED = 20
# PASS2-2F recovery gate: lift quarantine once the smoothed drop-rate falls
# below this AND we have seen at least this many consecutive clean batches.
_UNQUARANTINE_DROP_RATE = 0.2
_UNQUARANTINE_CLEAN_BATCHES = 3


class QuarantineReason(str, Enum):
    """Why an adapter was quarantined — drives recovery + dashboard display."""

    SCHEMA_DRIFT = "schema_drift"
    ERROR_RATE = "error_rate"
    CREDENTIALS_MISSING = "credentials_missing"
    MANUAL = "manual"


class _PlatformDrift:
    __slots__ = (
        "drop_rate",
        "observed",
        "batches",
        "clean_batches",
        "quarantined",
        "quarantine_reason",
        "quarantine_message",
    )

    def __init__(self) -> None:
        self.drop_rate = 0.0
        self.observed = 0
        self.batches = 0
        self.clean_batches = 0
        self.quarantined = False
        self.quarantine_reason: QuarantineReason | None = None
        self.quarantine_message = ""


class SchemaDriftTracker:
    """Tracks per-platform validation drop-rate and flags structural drift."""

    def __init__(
        self,
        *,
        drift_drop_rate: float = _DRIFT_DROP_RATE,
        min_observed: int = _MIN_OBSERVED,
        alpha: float = _EWMA_ALPHA,
    ) -> None:
        self._drift_drop_rate = drift_drop_rate
        self._min_observed = min_observed
        self._alpha = alpha
        self._state: dict[str, _PlatformDrift] = {}

    def record_batch(self, platform: str, total: int, valid: int) -> float:
        """Fold one validated batch into the platform's drop-rate.

        Returns the platform's updated smoothed drop-rate. A ``total`` of 0 is
        ignored (an empty fetch carries no schema information).
        """
        if total <= 0:
            return self._state.get(platform, _PlatformDrift()).drop_rate

        dropped = max(0, total - valid)
        batch_rate = dropped / total

        st = self._state.setdefault(platform, _PlatformDrift())
        # Seed the EWMA with the first observation so a single clean batch
        # doesn't sit at 0 forever, then smooth subsequent ones.
        st.drop_rate = batch_rate if st.batches == 0 else (
            self._alpha * batch_rate + (1 - self._alpha) * st.drop_rate
        )
        st.observed += total
        st.batches += 1
        # PASS2-2F: track consecutive clean batches for quarantine recovery.
        # validate_batch() routes every swarm batch through here, so this is
        # the canonical recovery path; a dirty batch resets the counter.
        if batch_rate < _UNQUARANTINE_DROP_RATE:
            st.clean_batches += 1
            if st.quarantined and self.should_unquarantine(platform):
                self._lift_quarantine(platform)
        else:
            st.clean_batches = 0

        if st.drop_rate >= self._drift_drop_rate and st.observed >= self._min_observed:
            _log.warning(
                "schema_drift.detected",
                platform=platform,
                drop_rate=round(st.drop_rate, 3),
                observed=st.observed,
                batches=st.batches,
            )
        return st.drop_rate

    def record_clean_batch(self, platform: str, batch_size: int) -> None:
        """Fold one fully-clean batch (drop rate ≈ 0) into the platform state.

        PASS2-2F: this is the recovery signal. Each clean batch decays the
        EWMA toward zero and bumps the consecutive-clean counter; once
        :meth:`should_unquarantine` flips True the quarantine is lifted.
        """
        if batch_size <= 0:
            return
        st = self._state.setdefault(platform, _PlatformDrift())
        st.drop_rate = 0.0 if st.batches == 0 else (1 - self._alpha) * st.drop_rate
        st.observed += batch_size
        st.batches += 1
        st.clean_batches += 1
        if st.quarantined and self.should_unquarantine(platform):
            self._lift_quarantine(platform)

    def should_unquarantine(self, platform: str) -> bool:
        """True when the platform has recovered enough to lift quarantine."""
        st = self._state.get(platform)
        if st is None:
            return False
        return (
            st.drop_rate < _UNQUARANTINE_DROP_RATE
            and st.clean_batches >= _UNQUARANTINE_CLEAN_BATCHES
        )

    def quarantine(
        self,
        platform: str,
        reason: QuarantineReason,
        message: str = "",
    ) -> None:
        """Explicitly quarantine a platform with a typed reason."""
        st = self._state.setdefault(platform, _PlatformDrift())
        st.quarantined = True
        st.quarantine_reason = reason
        st.quarantine_message = message
        st.clean_batches = 0
        _log.warning(
            "schema_drift.quarantined",
            platform=platform,
            reason=reason.value,
            message=message,
        )

    def is_quarantined(self, platform: str) -> bool:
        st = self._state.get(platform)
        return bool(st and st.quarantined)

    def quarantine_reason(self, platform: str) -> QuarantineReason | None:
        st = self._state.get(platform)
        return st.quarantine_reason if st else None

    def _lift_quarantine(self, platform: str) -> None:
        st = self._state.get(platform)
        if st is None or not st.quarantined:
            return
        prior_reason = st.quarantine_reason
        st.quarantined = False
        st.quarantine_reason = None
        st.quarantine_message = ""
        _log.info(
            "schema_drift.quarantine_lifted",
            platform=platform,
            prior_reason=prior_reason.value if prior_reason else None,
            drop_rate=round(st.drop_rate, 3),
            clean_batches=st.clean_batches,
        )

    def is_drifting(self, platform: str) -> bool:
        st = self._state.get(platform)
        if st is None:
            return False
        return st.drop_rate >= self._drift_drop_rate and st.observed >= self._min_observed

    def drop_rate(self, platform: str) -> float:
        st = self._state.get(platform)
        return st.drop_rate if st is not None else 0.0

    def reset(self, platform: str | None = None) -> None:
        """Clear drift state for one platform (e.g. after a fix) or all."""
        if platform is None:
            self._state.clear()
        else:
            self._state.pop(platform, None)


_TRACKER = SchemaDriftTracker()


def get_drift_tracker() -> SchemaDriftTracker:
    """Return the process-wide schema-drift tracker singleton."""
    return _TRACKER


def reset_drift_tracker() -> None:
    """Reset the singleton (test/maintenance hook)."""
    _TRACKER.reset()


__all__ = [
    "QuarantineReason",
    "SchemaDriftTracker",
    "get_drift_tracker",
    "reset_drift_tracker",
]
