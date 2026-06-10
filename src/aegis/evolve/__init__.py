"""
AEGIS Pulse — Phase 9: Autonomous Self-Evolution.

Continuous retraining, hyperparameter optimisation, drift detection and
online RL policy learning.

  - Every settled trade outcome is a ground truth label.
  - Models are retrained weekly (or early-triggered by drift).
  - Challengers are promoted only if they beat the champion by ≥2% AUC.
  - The RL pricing policy updates online from daily outcome signals.
"""

from __future__ import annotations

from aegis.evolve.config import EvolveSettings
from aegis.evolve.drift import DriftDetector
from aegis.evolve.outcomes import OutcomeRecorder
from aegis.evolve.retrain import RetrainingPipeline
from aegis.evolve.rl_policy import OnlinePricingPolicy
from aegis.evolve.schemas import (
    DriftSnapshot,
    EvolveStatus,
    ModelCandidate,
    PolicyState,
    RetrainRun,
    TradeOutcome,
)

__version__ = "9.0.0"

__all__ = [
    "EvolveSettings",
    "DriftDetector",
    "OutcomeRecorder",
    "RetrainingPipeline",
    "OnlinePricingPolicy",
    "DriftSnapshot",
    "EvolveStatus",
    "ModelCandidate",
    "PolicyState",
    "RetrainRun",
    "TradeOutcome",
]
