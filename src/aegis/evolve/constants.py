"""Phase 9 constants — error codes, thresholds, schedules."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Error codes AEGIS-EVOLVE-0001..0099
# ---------------------------------------------------------------------------
ERR_INSUFFICIENT_OUTCOMES = "AEGIS-EVOLVE-0001"
ERR_TRAINING_FAILED = "AEGIS-EVOLVE-0002"
ERR_PERFORMANCE_DROP = "AEGIS-EVOLVE-0003"
ERR_PROMOTION_FAILED = "AEGIS-EVOLVE-0004"
ERR_MODEL_SAVE_FAILED = "AEGIS-EVOLVE-0005"
ERR_DRIFT_DETECTED = "AEGIS-EVOLVE-0006"
ERR_ROLLBACK_TRIGGERED = "AEGIS-EVOLVE-0007"
ERR_OUTCOME_RECORD_FAILED = "AEGIS-EVOLVE-0008"
ERR_HPO_FAILED = "AEGIS-EVOLVE-0009"
ERR_POLICY_PERSIST_FAILED = "AEGIS-EVOLVE-0010"
ERR_RETRAIN_GENERAL = "AEGIS-EVOLVE-0099"

# ---------------------------------------------------------------------------
# Supported model architectures for retraining
# ---------------------------------------------------------------------------
SUPPORTED_ARCHITECTURES: tuple[str, ...] = ("patchts", "autoformer", "heuristic")

# ---------------------------------------------------------------------------
# Resolution statuses
# ---------------------------------------------------------------------------
RESOLUTION_STATUSES: frozenset[str] = frozenset(
    {"successful", "partial_refund", "full_refund", "dispute", "pending"}
)

# ---------------------------------------------------------------------------
# RL policy weight labels
# ---------------------------------------------------------------------------
POLICY_WEIGHT_LABELS: tuple[str, ...] = (
    "cost_based",
    "demand_based",
    "inventory_based",
    "competitor_based",
)

# ---------------------------------------------------------------------------
# Feature dimension (must match Phase 3 FEATURE_DIM)
# ---------------------------------------------------------------------------
EVOLVE_FEATURE_DIM: int = 20

# ---------------------------------------------------------------------------
# Default champion AUC when no champion exists (conservative)
# ---------------------------------------------------------------------------
DEFAULT_CHAMPION_AUC: float = 0.5

# ---------------------------------------------------------------------------
# Minimum precision considered "valid" for rollback comparison
# ---------------------------------------------------------------------------
MIN_VALID_PRECISION: float = 0.01

# ---------------------------------------------------------------------------
# Retrain trigger sources
# ---------------------------------------------------------------------------
TRIGGER_SCHEDULER = "scheduler"
TRIGGER_DRIFT = "drift"
TRIGGER_MANUAL = "manual"

# ---------------------------------------------------------------------------
# Retrain run statuses
# ---------------------------------------------------------------------------
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_NO_IMPROVEMENT = "no_improvement"
