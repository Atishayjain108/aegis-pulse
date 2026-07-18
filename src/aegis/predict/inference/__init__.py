"""
Inference orchestration: combines temporal + relational predictors,
fuses them, applies uncertainty + causal layers, returns a complete
PredictionBundle ready for Phase 4 consumption.

Public surface:
    InferenceConfig         — frozen runtime config
    InferenceRunner         — main orchestrator
    predict_for_trend()     — convenience function
    AuditRecord, make_audit — sidecar audit doc
"""

from .audit import AuditRecord, make_audit
from .runner import (
    InferenceConfig,
    InferenceResult,
    InferenceRunner,
    predict_for_trend,
)

__all__ = [
    "AuditRecord",
    "InferenceConfig",
    "InferenceResult",
    "InferenceRunner",
    "make_audit",
    "predict_for_trend",
]
