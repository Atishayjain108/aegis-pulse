"""
Reinforcement-learning execution layer.

A trained Phase 3 prediction tells us the *probability* that a trend
will break out. It does NOT directly tell us:

  * how much capital to allocate;
  * when to enter or exit;
  * how to size against a portfolio of correlated positions.

The RL layer answers those questions. It treats each (trend,
prediction) tuple as state, and learns a policy that maps state →
{ENTER, HOLD, EXIT, SCALE_UP, SCALE_DOWN} actions plus a continuous
size in [0, 1] (fraction of allowed capital).

Doctrine compliance: when Ray RLlib / gymnasium are not installed, the
layer degrades to a deterministic fractional-Kelly heuristic. The
heuristic is the floor; RL is the augmentation.
"""

from .env import (
    ArbitrageEnv,
    EnvConfig,
    PortfolioState,
)
from .policy import (
    HeuristicPolicy,
    KellyAction,
    PolicyDecision,
    decide,
)

__all__ = [
    "ArbitrageEnv",
    "EnvConfig",
    "PortfolioState",
    "HeuristicPolicy",
    "KellyAction",
    "PolicyDecision",
    "decide",
]
