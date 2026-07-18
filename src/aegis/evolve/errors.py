"""
aegis.evolve.errors
===================

Typed error hierarchy for Phase 9 Autonomous Self-Evolution.

Error codes: AEGIS-EVOLVE-0001 .. AEGIS-EVOLVE-0099
"""

from __future__ import annotations


class AegisEvolveError(Exception):
    """Base class for all evolve subsystem errors."""

    code: str = "AEGIS-EVOLVE-0000"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code


class InsufficientOutcomesError(AegisEvolveError):
    """Too few outcome samples to trigger retraining."""

    code = "AEGIS-EVOLVE-0001"


class TrainingFailedError(AegisEvolveError):
    """Model training run failed (all architectures)."""

    code = "AEGIS-EVOLVE-0002"


class PerformanceDropError(AegisEvolveError):
    """Model precision has dropped beyond the rollback threshold."""

    code = "AEGIS-EVOLVE-0003"


class PromotionFailedError(AegisEvolveError):
    """Champion promotion DB write failed."""

    code = "AEGIS-EVOLVE-0004"


class ModelSaveError(AegisEvolveError):
    """ONNX model artifact write to MinIO failed."""

    code = "AEGIS-EVOLVE-0005"


class DriftDetectedError(AegisEvolveError):
    """Data drift exceeds threshold — early retraining triggered."""

    code = "AEGIS-EVOLVE-0006"


class RollbackTriggeredError(AegisEvolveError):
    """Performance drop triggered auto-rollback to previous champion."""

    code = "AEGIS-EVOLVE-0007"


class OutcomeRecordError(AegisEvolveError):
    """Failed to persist a trade outcome to the database."""

    code = "AEGIS-EVOLVE-0008"


class HPOFailedError(AegisEvolveError):
    """Optuna hyperparameter search failed."""

    code = "AEGIS-EVOLVE-0009"


class PolicyPersistError(AegisEvolveError):
    """RL policy weights could not be persisted to the database."""

    code = "AEGIS-EVOLVE-0010"


class EvolveGeneralError(AegisEvolveError):
    """Unclassified evolve pipeline error."""

    code = "AEGIS-EVOLVE-0099"
