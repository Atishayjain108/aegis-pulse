"""EvolveSettings — Phase 9 configuration (AEGIS_EVOLVE_* env vars)."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EvolveSettings(BaseSettings):
    """All Phase 9 configuration.  Override via AEGIS_EVOLVE_<NAME> env var."""

    model_config = SettingsConfigDict(env_prefix="AEGIS_EVOLVE_", extra="ignore")

    # -------------------------------------------------------------------------
    # Retraining schedule
    # -------------------------------------------------------------------------
    retrain_day: str = Field(default="sunday", description="Day of week to retrain (lowercase).")
    retrain_hour: int = Field(default=2, ge=0, le=23, description="UTC hour to start retraining.")
    min_outcomes_for_retrain: int = Field(
        default=100,
        ge=10,
        description="Minimum outcome samples required before retraining.",
    )
    outcomes_lookback_days: int = Field(
        default=30, ge=1, description="How many days of outcomes to include in training."
    )

    # -------------------------------------------------------------------------
    # Model promotion gate
    # -------------------------------------------------------------------------
    auc_improvement_threshold: float = Field(
        default=0.02,
        ge=0.0,
        le=1.0,
        description="Minimum AUC gain required to promote a challenger to champion.",
    )
    hpo_n_trials: int = Field(
        default=30, ge=1, description="Optuna trials per architecture per retrain run."
    )

    # -------------------------------------------------------------------------
    # Drift detection
    # -------------------------------------------------------------------------
    drift_threshold: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="KS-distance above which early retraining is triggered.",
    )
    performance_drop_threshold: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Precision drop fraction that triggers auto-rollback.",
    )
    drift_check_interval_s: int = Field(
        default=3600, ge=60, description="How often to run drift detection (seconds)."
    )

    # -------------------------------------------------------------------------
    # RL policy
    # -------------------------------------------------------------------------
    rl_learning_rate: float = Field(
        default=0.01, gt=0.0, le=1.0, description="Gradient step size for policy updates."
    )
    rl_persist_interval: int = Field(
        default=100, ge=1, description="Persist policy weights every N updates."
    )

    # -------------------------------------------------------------------------
    # Model storage
    # -------------------------------------------------------------------------
    model_bucket: str = Field(default="aegis-models", description="MinIO bucket for ONNX artifacts.")
    shadow_holdout_days: int = Field(
        default=7, ge=1, description="Days of holdout used for champion comparison."
    )

    # -------------------------------------------------------------------------
    # HTTP timeout
    # -------------------------------------------------------------------------
    api_timeout_s: float = Field(default=10.0, ge=1.0)
