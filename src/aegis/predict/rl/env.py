"""
Gymnasium-compatible RL environment for the executor.

Doctrine: this file imports gymnasium *lazily*. Without gym installed
the env cannot be instantiated, but every other Phase 3 module continues
to work — including the heuristic execution policy.

State (observation, 16-D float vector):
   0:  p_breakout
   1:  p_peak
   2:  p_decline
   3:  velocity_mean_log
   4:  velocity_log_sigma
   5:  confidence
   6:  conformal_p10
   7:  conformal_p90
   8:  position_size_current
   9:  position_drawdown_current
   10: portfolio_total_size
   11: portfolio_correlation
   12: hours_since_signal_first_seen
   13: stage_one_hot[BREAKOUT]      (others derivable from class probs)
   14: cash_remaining
   15: timestep_normalised  ∈ [0,1]

Action (continuous, 2-D):
   a[0]:  signed size adjustment in [-1, +1]   (>0 enter/scale up; <0 scale down/exit)
   a[1]:  conviction multiplier in [0, 1]      (gates the size adjust)

Reward shaping:
   r = realised_pnl - λ_dd * max(0, drawdown_increase)
       - λ_action * (1 if action_changed else 0)
       - λ_correlation * portfolio_correlation_violation

This is a textbook risk-aware shaping. The drawdown penalty makes the
agent *visibly* preferred to lock in gains rather than chase peaks —
which empirically improves Sharpe at the cost of mean returns.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any, ClassVar

logger = logging.getLogger(__name__)

try:  # pragma: no cover
    import gymnasium as gym
    import numpy as np
    from gymnasium import spaces

    _HAS_GYM = True
except ImportError:  # pragma: no cover
    gym = None  # type: ignore[assignment]
    spaces = None  # type: ignore[assignment]
    np = None  # type: ignore[assignment]
    _HAS_GYM = False


@dataclass(frozen=True, slots=True)
class EnvConfig:
    """Tunable knobs for the env. All have safe defaults."""

    horizon_steps: int = 96  # 4 days hourly
    initial_cash: float = 10_000.0
    max_concurrent_positions: int = 5
    fee_rate: float = 0.005  # 50 bps round-trip
    drawdown_penalty: float = 1.5  # λ_dd
    action_change_penalty: float = 0.001
    correlation_penalty: float = 0.10
    seed: int = 1234


@dataclass
class PortfolioState:
    """Mutable state held by the env between steps."""

    cash: float
    open_positions: dict[str, dict[str, float]] = field(default_factory=dict)
    realised_pnl: float = 0.0
    peak_equity: float = 0.0
    last_action_size: float = 0.0
    timestep: int = 0

    def equity(self) -> float:
        unreal = sum(
            p["size"] * p.get("price_now", 1.0) - p["size"] * p.get("entry_price", 1.0)
            for p in self.open_positions.values()
        )
        return self.cash + self.realised_pnl + unreal

    def drawdown(self) -> float:
        eq = self.equity()
        if self.peak_equity == 0:
            self.peak_equity = eq
        self.peak_equity = max(self.peak_equity, eq)
        if self.peak_equity == 0:
            return 0.0
        return (eq - self.peak_equity) / self.peak_equity


def _make_env_class() -> Any:  # pragma: no cover — exercised in gym envs only
    """Build the actual gym.Env class. Lazy so missing gym → no crash."""
    if not _HAS_GYM:
        raise RuntimeError("gymnasium not installed — cannot build ArbitrageEnv")

    class _ArbitrageEnv(gym.Env):
        metadata: ClassVar[dict] = {"render_modes": []}

        def __init__(self, config: EnvConfig | None = None) -> None:
            super().__init__()
            self.cfg = config or EnvConfig()
            self.action_space = spaces.Box(
                low=np.array([-1.0, 0.0], dtype=np.float32),
                high=np.array([1.0, 1.0], dtype=np.float32),
                dtype=np.float32,
            )
            self.observation_space = spaces.Box(
                low=-np.inf, high=np.inf, shape=(16,), dtype=np.float32
            )
            self.rng = random.Random(self.cfg.seed)
            self.state = PortfolioState(
                cash=self.cfg.initial_cash, peak_equity=self.cfg.initial_cash
            )
            self._cur_obs: Any = None

        def _synthetic_observation(self) -> Any:
            """For unit tests / smoke runs we generate a plausible obs.

            In production, obs is supplied by the inference runner.
            """
            return np.array(
                [
                    self.rng.random(),  # p_breakout
                    self.rng.random() * 0.4,  # p_peak
                    self.rng.random() * 0.4,  # p_decline
                    self.rng.gauss(0.0, 0.3),  # vel mean
                    abs(self.rng.gauss(0.5, 0.2)),  # vel sigma
                    0.4 + self.rng.random() * 0.5,  # confidence
                    -1.0 + self.rng.random() * 0.3,  # p10
                    0.5 + self.rng.random() * 1.0,  # p90
                    sum(p["size"] for p in self.state.open_positions.values()),
                    self.state.drawdown(),
                    sum(p["size"] for p in self.state.open_positions.values()),
                    min(1.0, len(self.state.open_positions) / 5.0),
                    self.rng.random() * 48,
                    1.0 if self.rng.random() < 0.3 else 0.0,  # is_breakout
                    self.state.cash / self.cfg.initial_cash,
                    self.state.timestep / self.cfg.horizon_steps,
                ],
                dtype=np.float32,
            )

        def reset(self, *, seed: int | None = None, options: Any = None) -> tuple:
            if seed is not None:
                self.rng = random.Random(seed)
            self.state = PortfolioState(
                cash=self.cfg.initial_cash, peak_equity=self.cfg.initial_cash
            )
            self._cur_obs = self._synthetic_observation()
            return self._cur_obs, {}

        def _apply_action(self, action: Any) -> float:
            """Apply (size_delta, conviction) and return *fee paid this step*."""
            size_delta = float(action[0]) * float(action[1])
            fees = abs(size_delta) * self.state.cash * self.cfg.fee_rate
            self.state.cash -= fees
            self.state.last_action_size = size_delta
            return fees

        def step(self, action: Any) -> tuple:
            prev_drawdown = self.state.drawdown()
            prev_equity = self.state.equity()

            self._apply_action(action)

            # Walk the synthetic market one step forward.
            obs = self._synthetic_observation()
            self._cur_obs = obs

            self.state.timestep += 1

            new_equity = self.state.equity()
            new_drawdown = self.state.drawdown()
            realised = new_equity - prev_equity

            dd_increase = max(0.0, prev_drawdown - new_drawdown)
            action_change = abs(self.state.last_action_size)
            correlation_violation = 0.0
            if len(self.state.open_positions) > self.cfg.max_concurrent_positions:
                correlation_violation = float(
                    len(self.state.open_positions) - self.cfg.max_concurrent_positions
                )

            reward = (
                realised
                - self.cfg.drawdown_penalty * dd_increase
                - self.cfg.action_change_penalty * action_change
                - self.cfg.correlation_penalty * correlation_violation
            )

            terminated = self.state.timestep >= self.cfg.horizon_steps
            truncated = self.state.cash <= 0
            info = {
                "equity": new_equity,
                "drawdown": new_drawdown,
                "realised_pnl": self.state.realised_pnl,
            }
            return obs, float(reward), terminated, truncated, info

        def close(self) -> None:
            return None

    return _ArbitrageEnv


# Lazy proxy: instantiating ArbitrageEnv builds the real class.
def ArbitrageEnv(config: EnvConfig | None = None) -> Any:  # noqa: N802
    """Construct the gym env. Raises a clean error without gymnasium."""
    if not _HAS_GYM:
        raise RuntimeError(
            "ArbitrageEnv requires gymnasium + numpy. "
            "Install with `pip install gymnasium numpy` or use HeuristicPolicy."
        )
    cls = _make_env_class()
    return cls(config)
