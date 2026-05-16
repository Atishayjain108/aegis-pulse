"""
Models for the Predictive Apex.

Module organisation:
    base.py            — abstract Predictor interface every model implements
    heuristic.py       — deterministic baselines (always available)
    patchtst.py        — PatchTST temporal transformer (optional torch)
    autoformer.py      — Autoformer (optional torch)
    timesnet.py        — TimesNet (optional torch)
    hgt.py             — Heterogeneous Graph Transformer (optional torch_geometric)
    fusion.py          — 3-layer MLP that combines temporal + relational logits
    uncertainty.py     — MC-dropout, deep-ensemble, conformal wrappers
    factory.py         — name → class registry, single load entry point

Doctrine:
    Every neural class wraps its forward pass in resilient_call() and
    falls back to the heuristic on any failure. Models must remain
    pickleable and round-trip through ONNX export (no Python control
    flow inside the forward).
"""

from .base import Predictor
from .factory import load_model
from .heuristic import (
    HeuristicRelationalPredictor,
    HeuristicTemporalPredictor,
    heuristic_predict,
)

__all__ = [
    "Predictor",
    "HeuristicTemporalPredictor",
    "HeuristicRelationalPredictor",
    "heuristic_predict",
    "load_model",
]
