"""
Typed errors for the Predictive Apex.

Project rule: every user-facing error has a machine code
(`AEGIS-PREDICT-NNNN`), a human message, and a documented
remediation page under `docs/errors/`. The machine code is
the stable identity of the error — wording can drift, the
code cannot.

Author: AEGIS Pulse core team
"""

from __future__ import annotations


class AegisPredictError(Exception):
    """Base for all predict-layer errors. Never raise this directly."""

    code: str = "AEGIS-PREDICT-0000"
    docs_url: str = "docs/errors/AEGIS-PREDICT-0000.md"

    def __init__(self, message: str, *, context: dict | None = None) -> None:
        self.message = message
        self.context = context or {}
        super().__init__(f"[{self.code}] {message}")


# ---------------------------------------------------------------------------
# Schema / validation
# ---------------------------------------------------------------------------
class FeatureSchemaError(AegisPredictError):
    """Feature window violates the active FEATURE_SCHEMA_VERSION."""

    code = "AEGIS-PREDICT-1001"
    docs_url = "docs/errors/AEGIS-PREDICT-1001.md"


class FeatureValueError(AegisPredictError):
    """Feature window contains NaN, Inf, or out-of-range values."""

    code = "AEGIS-PREDICT-1002"
    docs_url = "docs/errors/AEGIS-PREDICT-1002.md"


# ---------------------------------------------------------------------------
# Model loading / inference
# ---------------------------------------------------------------------------
class ModelLoadError(AegisPredictError):
    """Failed to load a model — weights missing, corrupt, or wrong shape."""

    code = "AEGIS-PREDICT-2001"
    docs_url = "docs/errors/AEGIS-PREDICT-2001.md"


class ModelInferenceError(AegisPredictError):
    """Forward pass produced NaN/Inf or wrong-shaped output."""

    code = "AEGIS-PREDICT-2002"
    docs_url = "docs/errors/AEGIS-PREDICT-2002.md"


class InferenceTimeoutError(AegisPredictError):
    """Forward pass exceeded the latency budget."""

    code = "AEGIS-PREDICT-2003"
    docs_url = "docs/errors/AEGIS-PREDICT-2003.md"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
class ModelNotFoundError(AegisPredictError):
    """No model with the given id exists in the registry."""

    code = "AEGIS-PREDICT-3001"
    docs_url = "docs/errors/AEGIS-PREDICT-3001.md"


class ModelHashMismatchError(AegisPredictError):
    """Loaded weights' sha256 does not match registry manifest."""

    code = "AEGIS-PREDICT-3002"
    docs_url = "docs/errors/AEGIS-PREDICT-3002.md"


class PromotionGateError(AegisPredictError):
    """Candidate failed the promotion gate (precision/F1/latency)."""

    code = "AEGIS-PREDICT-3003"
    docs_url = "docs/errors/AEGIS-PREDICT-3003.md"


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------
class LookaheadError(AegisPredictError):
    """Backtest fold has train_end > test_start. Lookahead leak."""

    code = "AEGIS-PREDICT-4001"
    docs_url = "docs/errors/AEGIS-PREDICT-4001.md"


class InsufficientDataError(AegisPredictError):
    """Not enough samples to run a backtest fold."""

    code = "AEGIS-PREDICT-4002"
    docs_url = "docs/errors/AEGIS-PREDICT-4002.md"


# ---------------------------------------------------------------------------
# Serving
# ---------------------------------------------------------------------------
class ServingError(AegisPredictError):
    """HTTP serving layer cannot start (missing fastapi, port bind, …)."""

    code = "AEGIS-PREDICT-5001"
    docs_url = "docs/errors/AEGIS-PREDICT-5001.md"


__all__ = [
    "AegisPredictError",
    "FeatureSchemaError",
    "FeatureValueError",
    "ModelLoadError",
    "ModelInferenceError",
    "InferenceTimeoutError",
    "ModelNotFoundError",
    "ModelHashMismatchError",
    "PromotionGateError",
    "LookaheadError",
    "InsufficientDataError",
    "ServingError",
]
