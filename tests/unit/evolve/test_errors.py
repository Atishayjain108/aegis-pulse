"""Unit tests for Phase 9 error hierarchy."""

from __future__ import annotations

import pytest

from aegis.evolve.errors import (
    AegisEvolveError,
    DriftDetectedError,
    EvolveGeneralError,
    HPOFailedError,
    InsufficientOutcomesError,
    ModelSaveError,
    OutcomeRecordError,
    PerformanceDropError,
    PolicyPersistError,
    PromotionFailedError,
    RollbackTriggeredError,
    TrainingFailedError,
)


class TestAegisEvolveError:
    def test_base_message(self) -> None:
        e = AegisEvolveError("something broke")
        assert str(e) == "something broke"
        assert e.code == "AEGIS-EVOLVE-0000"

    def test_custom_code(self) -> None:
        e = AegisEvolveError("msg", code="AEGIS-EVOLVE-0099")
        assert e.code == "AEGIS-EVOLVE-0099"

    def test_is_exception(self) -> None:
        with pytest.raises(AegisEvolveError):
            raise AegisEvolveError("test")


class TestSpecificErrors:
    def test_insufficient_outcomes(self) -> None:
        e = InsufficientOutcomesError("need more data")
        assert e.code == "AEGIS-EVOLVE-0001"
        assert isinstance(e, AegisEvolveError)

    def test_training_failed(self) -> None:
        e = TrainingFailedError("model failed")
        assert e.code == "AEGIS-EVOLVE-0002"

    def test_performance_drop(self) -> None:
        e = PerformanceDropError("precision fell")
        assert e.code == "AEGIS-EVOLVE-0003"

    def test_promotion_failed(self) -> None:
        e = PromotionFailedError("db write failed")
        assert e.code == "AEGIS-EVOLVE-0004"

    def test_model_save(self) -> None:
        e = ModelSaveError("minio error")
        assert e.code == "AEGIS-EVOLVE-0005"

    def test_drift_detected(self) -> None:
        e = DriftDetectedError("ks > threshold")
        assert e.code == "AEGIS-EVOLVE-0006"

    def test_rollback_triggered(self) -> None:
        e = RollbackTriggeredError("auto-rollback")
        assert e.code == "AEGIS-EVOLVE-0007"

    def test_outcome_record(self) -> None:
        e = OutcomeRecordError("insert failed")
        assert e.code == "AEGIS-EVOLVE-0008"

    def test_hpo_failed(self) -> None:
        e = HPOFailedError("optuna crashed")
        assert e.code == "AEGIS-EVOLVE-0009"

    def test_policy_persist(self) -> None:
        e = PolicyPersistError("write error")
        assert e.code == "AEGIS-EVOLVE-0010"

    def test_general_error(self) -> None:
        e = EvolveGeneralError("unclassified")
        assert e.code == "AEGIS-EVOLVE-0099"

    def test_all_are_catchable_as_base(self) -> None:
        errors = [
            InsufficientOutcomesError, TrainingFailedError, PerformanceDropError,
            PromotionFailedError, ModelSaveError, DriftDetectedError,
            RollbackTriggeredError, OutcomeRecordError, HPOFailedError,
            PolicyPersistError, EvolveGeneralError,
        ]
        for cls in errors:
            with pytest.raises(AegisEvolveError):
                raise cls("test message")
