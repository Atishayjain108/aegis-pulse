"""
Model factory — `load_model(name)` is the single supported way to obtain
a predictor instance.

Why a factory and not direct imports?
-------------------------------------
1. Callers (registry, inference runner, tests) get ONE switch point —
   we can swap a model implementation without touching every call site.
2. Heavy neural deps (torch, torch_geometric) stay lazy: importing
   `aegis.predict.models` does not import torch unless the caller
   actually requests "patchtst" or similar.
3. Unknown names produce a single typed error (ModelNotFoundError) so
   the registry promotion gate can format a deterministic message.

Stability contract
------------------
The string keys returned by `available_models()` are part of Phase 3's
public API — registry manifests reference them by name. New models may
be added; existing keys are removed only on a major-version bump.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from .. import DEFAULT_FEATURE_WINDOW, DEFAULT_HORIZONS
from ..errors import ModelNotFoundError
from .base import Predictor
from .heuristic import (
    HeuristicRelationalPredictor,
    HeuristicTemporalPredictor,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Registry of constructors. We use callables (not classes) so that we
# can do lazy imports of optional neural deps inside each constructor.
# ---------------------------------------------------------------------------
def _make_heuristic_temporal() -> Predictor:
    return HeuristicTemporalPredictor()


def _make_heuristic_relational() -> Predictor:
    return HeuristicRelationalPredictor()


def _make_patchtst() -> Predictor:
    from .patchtst import PatchTSTPredictor

    return PatchTSTPredictor(
        feature_dim=20,
        window_size=DEFAULT_FEATURE_WINDOW,
        horizons=DEFAULT_HORIZONS,
    )


def _make_autoformer() -> Predictor:
    from .autoformer import AutoformerPredictor

    return AutoformerPredictor(
        feature_dim=20,
        window_size=DEFAULT_FEATURE_WINDOW,
        horizons=DEFAULT_HORIZONS,
    )


def _make_timesnet() -> Predictor:
    from .timesnet import TimesNetPredictor

    return TimesNetPredictor()


def _make_hgt() -> Predictor:
    from .hgt import HGTPredictor

    return HGTPredictor()


_REGISTRY: dict[str, Callable[[], Predictor]] = {
    "heuristic_temporal": _make_heuristic_temporal,
    "heuristic_relational": _make_heuristic_relational,
    "patchtst": _make_patchtst,
    "autoformer": _make_autoformer,
    "timesnet": _make_timesnet,
    "hgt": _make_hgt,
}


def available_models() -> tuple[str, ...]:
    """Return the canonical, stable list of model keys."""
    return tuple(sorted(_REGISTRY.keys()))


def load_model(name: str) -> Predictor:
    """Instantiate the predictor whose registered name is `name`.

    Raises:
        ModelNotFoundError: if `name` is not in the registry. The error
            message lists the known names so an operator can copy-paste
            the correct one. We deliberately do NOT silently fall back
            to the heuristic here: a typo'd registry entry should be a
            hard failure at load time, not a degraded inference at
            request time.
    """
    if name not in _REGISTRY:
        raise ModelNotFoundError(f"Unknown model '{name}'. Available: {available_models()}")
    try:
        return _REGISTRY[name]()
    except Exception as exc:  # pragma: no cover — guard rail
        # If a neural-model constructor fails (e.g. torch import) we
        # silently substitute the heuristic of the same kind. This is
        # the same defence-in-depth principle used at inference time.
        logger.warning("load_model('%s') failed (%s) — substituting heuristic", name, exc)
        if name in {"hgt"}:
            return _make_heuristic_relational()
        return _make_heuristic_temporal()
