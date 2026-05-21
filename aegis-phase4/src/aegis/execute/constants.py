"""Named constants for Phase 4 Execution & Alert System.

Every magic number used inside `aegis.execute` is defined here with an
inline rationale comment. No module may inline a numeric literal that
represents a tunable threshold, timeout, or limit.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Priority bands (P0..P3)
#
# These mirror the Phase 2 priority semantics (final_priority in GraphResult)
# but Phase 4 may *escalate* (never demote) based on combined Phase 3 signals.
# ---------------------------------------------------------------------------
PRIORITY_P0_CRITICAL: Final[int] = 0  # immediate operator attention
PRIORITY_P1_HIGH: Final[int] = 1  # next-batch attention
PRIORITY_P2_NORMAL: Final[int] = 2  # standard alert
PRIORITY_P3_INFO: Final[int] = 3  # informational, no action expected
ALLOWED_PRIORITIES: Final[frozenset[int]] = frozenset({0, 1, 2, 3})

# ---------------------------------------------------------------------------
# Verdict classifications
#
# Phase 4 emits one of these. They are a superset of Phase 2 verdicts because
# we may surface "DEGRADED" when upstream signals are partial.
# ---------------------------------------------------------------------------
VERDICT_ENTER: Final[str] = "ENTER"
VERDICT_HOLD: Final[str] = "HOLD"
VERDICT_EXIT: Final[str] = "EXIT"
VERDICT_BLOCK: Final[str] = "BLOCK"
VERDICT_DEGRADED: Final[str] = "DEGRADED"
ALLOWED_VERDICTS: Final[frozenset[str]] = frozenset(
    {VERDICT_ENTER, VERDICT_HOLD, VERDICT_EXIT, VERDICT_BLOCK, VERDICT_DEGRADED}
)

# ---------------------------------------------------------------------------
# Composer thresholds (deterministic, heuristic-floor)
#
# Rationale: these thresholds determine the deterministic verdict from
# numeric features alone. LLM/ML can only multiply confidence downward — they
# cannot flip the verdict. Calibrated against Phase 2/3 distributions seen
# during integration testing (2026-05).
# ---------------------------------------------------------------------------
ENTER_MIN_SCORE: Final[float] = 0.60  # Phase 2 final_score floor for ENTER
ENTER_MIN_CONFIDENCE: Final[float] = 0.55  # combined confidence floor
EXIT_MIN_DECLINE_PROB: Final[float] = 0.60  # Phase 3 p_decline floor for EXIT
EXIT_MIN_SATURATION: Final[float] = 0.70  # Phase 3 p_saturation alternative
BLOCK_COMPLIANCE_VETO: Final[str] = "compliance_block"  # halt_reason value
BLOCK_REDTEAM_VETO: Final[str] = "redteam_block"

# ---------------------------------------------------------------------------
# Priority escalation thresholds (used by classifier)
#
# Rationale: if Phase 3 says p_breakout@24h >= 0.80 AND Phase 2 says ENTER,
# escalate to P0. Calibrated to keep P0 traffic < 5% of alerts.
# ---------------------------------------------------------------------------
P0_BREAKOUT_PROB_FLOOR: Final[float] = 0.80
P0_CONFIDENCE_FLOOR: Final[float] = 0.70
P1_BREAKOUT_PROB_FLOOR: Final[float] = 0.60
P1_CONFIDENCE_FLOOR: Final[float] = 0.55

# ---------------------------------------------------------------------------
# Risk gates
#
# Rationale: even with an ENTER verdict, an alert is blocked if expected
# margin is negative or confidence below floor. These match Phase 3's own
# heuristic margin gate (mean > $1.00 AND loss_probability <= 35%) but are
# applied independently as defence in depth.
# ---------------------------------------------------------------------------
MIN_EXPECTED_MARGIN_USD: Final[float] = 1.00
MAX_LOSS_PROBABILITY: Final[float] = 0.35
MIN_OVERALL_CONFIDENCE: Final[float] = 0.40  # below this → BLOCK + log

# ---------------------------------------------------------------------------
# Sizing (advisory only; fractional-Kelly)
#
# Rationale: matches Phase 3 RL policy default fraction (0.25×). Phase 4
# computes a recommended unit count given a capital budget but NEVER places
# any order.
# ---------------------------------------------------------------------------
KELLY_FRACTION_DEFAULT: Final[float] = 0.25
KELLY_CAPITAL_DEFAULT_USD: Final[float] = 1000.0
KELLY_MAX_POSITION_PCT: Final[float] = 0.10  # never recommend > 10% of capital
KELLY_FLOOR_UNITS: Final[int] = 0  # never recommend negative units

# ---------------------------------------------------------------------------
# Dedup + throttle
# ---------------------------------------------------------------------------
DEDUP_KEY_PREFIX: Final[str] = "aegis:execute:dedup:"
DEDUP_DEFAULT_TTL_S: Final[int] = 3600  # 1h: re-emit if same alert recurs
THROTTLE_KEY_PREFIX: Final[str] = "aegis:execute:throttle:"
THROTTLE_PER_CHANNEL_PER_MIN: Final[int] = 30  # spam guard

# ---------------------------------------------------------------------------
# Outbox + drainer
# ---------------------------------------------------------------------------
OUTBOX_DRAIN_INTERVAL_S: Final[float] = 1.0
OUTBOX_DRAIN_BATCH: Final[int] = 25
OUTBOX_MAX_ATTEMPTS: Final[int] = 5
OUTBOX_BACKOFF_BASE_S: Final[float] = 1.0
OUTBOX_BACKOFF_MAX_S: Final[float] = 60.0

# ---------------------------------------------------------------------------
# Notifier timeouts
# ---------------------------------------------------------------------------
NOTIFY_TIMEOUT_S: Final[float] = 5.0  # per-channel send budget
NOTIFY_USER_AGENT: Final[str] = "AEGIS-Pulse/0.4.0 (+execute)"

# ---------------------------------------------------------------------------
# Kill-switch
# ---------------------------------------------------------------------------
KILLSWITCH_KEY_DEFAULT: Final[str] = "aegis:execute:killswitch"
KILLSWITCH_STATE_TRIPPED: Final[str] = "TRIPPED"
KILLSWITCH_STATE_ARMED: Final[str] = "ARMED"

# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------
SSE_TOPIC_DEFAULT: Final[str] = "aegis.execute.events"
SSE_KEEPALIVE_INTERVAL_S: Final[float] = 15.0
SSE_MAX_QUEUE: Final[int] = 1024  # per-client backlog before drop

# ---------------------------------------------------------------------------
# Execution mode (v1 supports only advisory)
# ---------------------------------------------------------------------------
MODE_ADVISORY: Final[str] = "advisory"
ALLOWED_MODES: Final[frozenset[str]] = frozenset({MODE_ADVISORY})

# ---------------------------------------------------------------------------
# Schema version (alert envelope payload version)
# ---------------------------------------------------------------------------
ALERT_ENVELOPE_VERSION: Final[str] = "1.0"
