"""
Training utilities.

Phase 3 training is *opt-in*. The system runs end-to-end on the
heuristic predictors with no training. Training only happens when:

  * a torch-capable host is available;
  * historical signal data + outcome labels are persisted;
  * the operator runs `aegis train ...`.

This package provides:
  * Dataset    — adapts the Phase 1 signals table into model-ready
                 (FeatureWindow, label) tuples.
  * Trainer    — minimal training loop (PyTorch). MLflow is optional.
  * ONNXExport — convert a trained net to ONNX INT8 for serving.
"""

from .dataset import (
    Dataset,
    LabelledSample,
    SignalDataset,
    build_dataset,
)
from .onnx_export import export_to_onnx
from .trainer import (
    TrainerConfig,
    TrainingResult,
    train_temporal,
)

__all__ = [
    "Dataset",
    "LabelledSample",
    "SignalDataset",
    "build_dataset",
    "TrainingResult",
    "TrainerConfig",
    "train_temporal",
    "export_to_onnx",
]
