"""
Model registry: durable storage for trained model artefacts plus a
promotion ladder (staging → shadow → production → archived).

Why we re-implement instead of just using MLflow
------------------------------------------------
MLflow is the *training-time* tracker — it owns runs, params, metrics.
This registry is the *runtime* contract: the inference service reads
manifests from here, verifies sha256, and refuses to load anything that
hasn't passed the promotion gate. Decoupling the two means:

* the runtime has zero MLflow dependency;
* the registry can be backed by a filesystem, S3, MinIO, or a database
  without affecting MLflow tracking;
* promotion gates are enforced *here*, in code paths the inference
  service actually executes — not buried in a CI job.

Doctrine compliance: a registry miss returns the heuristic — never a
silent stale model. Hash mismatches raise hard errors.
"""

from .promotion import (
    PromotionDecision,
    PromotionGate,
    evaluate_promotion,
    evaluate_rollback,
)
from .store import ModelStore

__all__ = [
    "ModelStore",
    "PromotionDecision",
    "PromotionGate",
    "evaluate_promotion",
    "evaluate_rollback",
]
