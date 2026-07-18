"""
Causal attribution layer.

Phase 3's predictive head answers "WILL this trend break out?". The
causal layer answers "WHY?" — which features are *causally* responsible
for the verdict, and what would happen under a counterfactual?

Doctrine: the causal layer is OPTIONAL augmentation. The Phase 3
PredictionBundle is fully usable without it. When DoWhy / EconML are
not installed, the deterministic attributor produces a feature-level
breakdown using the same rules as the heuristic predictor — so audit
trails always have an attribution column populated.
"""

from .attributor import (
    CausalAttribution,
    CausalAttributor,
    DeterministicAttributor,
    attribute,
)
from .counterfactual import (
    CounterfactualEngine,
    CounterfactualScenario,
    propose_counterfactuals,
)

__all__ = [
    "CausalAttribution",
    "CausalAttributor",
    "CounterfactualEngine",
    "CounterfactualScenario",
    "DeterministicAttributor",
    "attribute",
    "propose_counterfactuals",
]
