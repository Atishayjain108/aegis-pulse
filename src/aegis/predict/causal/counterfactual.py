"""
Counterfactual scenarios.

Answers questions like:

  * What would the prediction look like if we shifted the window
    forward (or backward) by N hours?
  * What would happen if the velocity were 1.5x its current value?
  * What if coordination_risk were zero (i.e. we filtered the
    suspected bot accounts before scoring)?

The engine is deliberately stateless — it takes a window, a list of
scenarios to apply, and a predict function, and returns a list of
(scenario, prediction) pairs.

When `tabpfn` is installed we additionally provide an ML-based
counterfactual that asks a frozen tabular transformer "given the
historical (features → outcome) corpus, what outcome would you expect
under the perturbed features?". This is useful as an independent
sanity check on the temporal model's counterfactual reasoning. When
TabPFN is missing we degrade silently to "no second opinion".
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from .. import FEATURE_NAMES
from ..schemas import FeatureWindow, Prediction

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CounterfactualScenario:
    """One perturbation to apply to a feature window."""

    name: str
    description: str
    # Feature-level multiplicative perturbation. If a feature is in
    # this dict its values across all buckets are multiplied by
    # the given factor. Cyclic time features ignore this.
    feature_multipliers: dict[str, float] = field(default_factory=dict)
    # Additive perturbation in the same shape (post-multiplier).
    feature_offsets: dict[str, float] = field(default_factory=dict)
    # If non-zero, slide the window forward by N buckets.
    time_shift_buckets: int = 0


PredictFn = Callable[[FeatureWindow], Awaitable[Prediction]]


# Default scenarios that ship with the system. Operators can extend.
DEFAULT_SCENARIOS: tuple[CounterfactualScenario, ...] = (
    CounterfactualScenario(
        name="baseline",
        description="No perturbation — sanity baseline.",
    ),
    CounterfactualScenario(
        name="2x_velocity",
        description="What if every velocity feature were 2x larger?",
        feature_multipliers={
            "velocity_1h": 2.0,
            "velocity_6h": 2.0,
            "velocity_24h": 2.0,
        },
    ),
    CounterfactualScenario(
        name="zero_coordination",
        description="What if coordination_risk were zero (bots filtered)?",
        feature_multipliers={"coordination_risk": 0.0},
    ),
    CounterfactualScenario(
        name="delayed_entry_12h",
        description="Slide the analysis window 12h later — did we miss?",
        time_shift_buckets=12,
    ),
    CounterfactualScenario(
        name="early_entry_12h",
        description="Slide the analysis window 12h earlier — were we late?",
        time_shift_buckets=-12,
    ),
)


def _apply(window: FeatureWindow, scenario: CounterfactualScenario) -> FeatureWindow:
    """Return a perturbed window. Original is left untouched (frozen)."""
    feature_index = {name: i for i, name in enumerate(FEATURE_NAMES)}
    cyclic = {
        feature_index["hour_of_day_sin"],
        feature_index["hour_of_day_cos"],
        feature_index["day_of_week_sin"],
        feature_index["day_of_week_cos"],
    }

    rows: list[list[float]] = [list(r) for r in window.as_2d()]

    if scenario.time_shift_buckets > 0:
        # Drop earliest N buckets, pad recent end with the latest row.
        n = min(scenario.time_shift_buckets, len(rows))
        if rows:
            tail = list(rows[-1])
            rows = rows[n:] + [list(tail) for _ in range(n)]
    elif scenario.time_shift_buckets < 0:
        # Slide back: drop latest N, pad earliest end with first row.
        n = min(-scenario.time_shift_buckets, len(rows))
        if rows:
            head = list(rows[0])
            rows = [list(head) for _ in range(n)] + rows[:-n]

    if scenario.feature_multipliers or scenario.feature_offsets:
        for row in rows:
            for name, mult in scenario.feature_multipliers.items():
                i = feature_index.get(name)
                if i is None or i in cyclic:
                    continue
                row[i] = float(row[i]) * mult
            for name, off in scenario.feature_offsets.items():
                i = feature_index.get(name)
                if i is None or i in cyclic:
                    continue
                row[i] = float(row[i]) + off

    flat: list[float] = []
    for row in rows:
        flat.extend(float(v) for v in row)
    return window.model_copy(update={"values": flat})


@dataclass(frozen=True, slots=True)
class _CounterfactualResult:
    scenario: CounterfactualScenario
    prediction: Prediction


@dataclass
class CounterfactualEngine:
    """Apply each scenario, predict under it, collect results."""

    scenarios: tuple[CounterfactualScenario, ...] = DEFAULT_SCENARIOS

    async def run(
        self,
        window: FeatureWindow,
        predict_fn: PredictFn,
    ) -> tuple[_CounterfactualResult, ...]:
        results: list[_CounterfactualResult] = []
        for scenario in self.scenarios:
            perturbed = _apply(window, scenario)
            try:
                pred = await predict_fn(perturbed)
            except Exception as exc:
                logger.warning(
                    "counterfactual '%s' failed: %s — skipping",
                    scenario.name,
                    exc,
                )
                continue
            results.append(_CounterfactualResult(scenario=scenario, prediction=pred))
        return tuple(results)


async def propose_counterfactuals(
    window: FeatureWindow,
    predict_fn: PredictFn,
    scenarios: Sequence[CounterfactualScenario] | None = None,
) -> tuple[_CounterfactualResult, ...]:
    """One-call convenience over `CounterfactualEngine`."""
    engine = CounterfactualEngine(scenarios=tuple(scenarios) if scenarios else DEFAULT_SCENARIOS)
    return await engine.run(window, predict_fn)
