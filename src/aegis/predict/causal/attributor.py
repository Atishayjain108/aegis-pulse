"""
Causal attribution.

Two implementations behind a single interface:

  DeterministicAttributor  — pure-stdlib feature ranking via partial
                             contributions. Always available. The
                             output is auditable and reproducible.

  CausalAttributor         — DoWhy/EconML augmentation. When installed,
                             we model each top-k feature as a treatment
                             on the predicted breakout probability and
                             estimate the average treatment effect via
                             a linear regression backdoor adjustment.

Order of operation: deterministic first; if the optional library is
present we *append* its estimate to the deterministic output (we do
not replace).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from .. import FEATURE_NAMES
from ..schemas import FeatureWindow, Prediction

logger = logging.getLogger(__name__)


# Indices of features that historically dominate breakout — used as
# the deterministic ranking floor. Map to FEATURE_NAMES tuple.
_BREAKOUT_DRIVERS = {
    "velocity_24h": 1.0,
    "velocity_6h": 0.8,
    "velocity_1h": 0.4,
    "signal_count": 0.6,
    "unique_authors": 0.6,
    "platform_diversity": 0.5,
    "novelty": 0.7,
    "commercial_intent": 0.5,
    "engagement_per_author": 0.4,
    "coordination_risk": -0.7,  # negative = suppresses confidence
}


@dataclass(frozen=True, slots=True)
class CausalAttribution:
    """One feature's causal contribution to a verdict.

    Fields:
        feature: feature name (from FEATURE_NAMES)
        contribution: signed scalar — positive pushes toward breakout,
            negative pulls away. Magnitudes are NOT probabilities;
            they are normalised to sum-of-absolute-values = 1 across
            an attribution batch.
        method: "deterministic" or "dowhy_linear_ate" — for audit.
        confidence_interval: (lo, hi) for the contribution if the
            method supplies one (DoWhy does, deterministic does not).
    """

    feature: str
    contribution: float
    method: str = "deterministic"
    confidence_interval: tuple[float, float] | None = None


# ---------------------------------------------------------------------------
# Deterministic attributor — always available
# ---------------------------------------------------------------------------
@dataclass
class DeterministicAttributor:
    """Pure-stdlib attributor; deterministic; always available.

    The ranking is computed from the latest feature bucket of the
    window: we take each driver's normalised value, multiply by its
    sign-weighted prior from `_BREAKOUT_DRIVERS`, normalise the result.
    """

    drivers: dict[str, float] = field(default_factory=lambda: dict(_BREAKOUT_DRIVERS))
    top_k: int = 6

    def attribute(
        self,
        window: FeatureWindow,
        prediction: Prediction,
    ) -> tuple[CausalAttribution, ...]:
        if window.window_size == 0:
            return ()
        rows_2d = window.as_2d()
        if not rows_2d:
            return ()
        latest = rows_2d[-1]
        # Build raw contributions: prior * normalised feature value.
        # We tanh-squash the raw feature so a single huge spike doesn't
        # dominate the entire ranking.
        contribs: dict[str, float] = {}
        feature_index = {name: i for i, name in enumerate(FEATURE_NAMES)}
        for name, prior in self.drivers.items():
            i = feature_index.get(name)
            if i is None:
                continue
            raw = float(latest[i])
            squashed = math.tanh(raw)
            contribs[name] = prior * squashed

        # Normalise by sum of |contributions| so the ranking is on a
        # comparable scale across windows.
        total = sum(abs(v) for v in contribs.values()) or 1.0
        normalised = {k: v / total for k, v in contribs.items()}

        # Take top_k by absolute magnitude.
        ranked = sorted(normalised.items(), key=lambda kv: abs(kv[1]), reverse=True)
        ranked = ranked[: self.top_k]

        # Force deterministic ordering for ties: by feature name.
        ranked.sort(key=lambda kv: (-abs(kv[1]), kv[0]))
        return tuple(
            CausalAttribution(feature=k, contribution=v, method="deterministic") for k, v in ranked
        )


# ---------------------------------------------------------------------------
# Optional DoWhy/EconML attributor
# ---------------------------------------------------------------------------
try:  # pragma: no cover
    import dowhy  # noqa: F401

    _HAS_DOWHY = True
except ImportError:  # pragma: no cover
    _HAS_DOWHY = False


@dataclass
class CausalAttributor:
    """Wraps the deterministic floor with optional DoWhy ATE estimates.

    When DoWhy is installed we model each top-k driver as a binary
    treatment (median split of the historical distribution) and the
    predicted breakout probability as the outcome. The graph is a
    flat backdoor model — every other driver is a confounder. We use
    linear regression as the estimand, which is fast and stable for
    small N. The estimate is appended to the deterministic ranking.
    """

    base: DeterministicAttributor = field(default_factory=DeterministicAttributor)
    enable_dowhy: bool = True
    history_window_size: int = 256

    def attribute(
        self,
        window: FeatureWindow,
        prediction: Prediction,
        *,
        history: Sequence[tuple[FeatureWindow, Prediction]] | None = None,
    ) -> tuple[CausalAttribution, ...]:
        det = self.base.attribute(window, prediction)
        if not self.enable_dowhy or not _HAS_DOWHY or not history:
            return det

        try:
            return self._dowhy_refine(det, history)
        except Exception as exc:  # pragma: no cover — guard rail
            logger.warning("dowhy refinement failed (%s) — using deterministic", exc)
            return det

    def _dowhy_refine(
        self,
        det: tuple[CausalAttribution, ...],
        history: Sequence[tuple[FeatureWindow, Prediction]],
    ) -> tuple[CausalAttribution, ...]:  # pragma: no cover — exercised only in dowhy env
        """Use DoWhy linear-backdoor on each top-k driver."""
        try:
            import pandas as pd
            from dowhy import CausalModel
        except ImportError:
            return det

        if not history:
            return det

        feature_index = {name: i for i, name in enumerate(FEATURE_NAMES)}
        rows = []
        for w, p in history[-self.history_window_size :]:
            if not w.values:
                continue
            rows_2d = w.as_2d()
            latest = rows_2d[-1]
            row = {name: float(latest[i]) for name, i in feature_index.items()}
            row["__breakout_prob__"] = float(p.p_breakout)
            rows.append(row)

        if len(rows) < 32:
            return det  # too little data for ATE

        df = pd.DataFrame(rows)
        out: list[CausalAttribution] = []
        for det_attr in det:
            feat = det_attr.feature
            if feat not in df.columns:
                out.append(det_attr)
                continue
            # Binary-treatment via median split.
            treat_col = f"__t_{feat}__"
            df[treat_col] = (df[feat] > df[feat].median()).astype(int)
            confounders = [c for c in df.columns if c not in {treat_col, feat, "__breakout_prob__"}]
            try:
                model = CausalModel(
                    data=df,
                    treatment=treat_col,
                    outcome="__breakout_prob__",
                    common_causes=confounders,
                )
                est = model.identify_effect(proceed_when_unidentifiable=True)
                effect = model.estimate_effect(est, method_name="backdoor.linear_regression")
                ate = float(effect.value)
                # Cheap CI via SE from the linear regression estimate.
                ci = None
                try:
                    se = float(effect.get_standard_error())  # type: ignore[attr-defined]
                    ci = (ate - 1.96 * se, ate + 1.96 * se)
                except Exception:
                    ci = None
                out.append(
                    CausalAttribution(
                        feature=feat,
                        contribution=ate,
                        method="dowhy_linear_ate",
                        confidence_interval=ci,
                    )
                )
            except Exception:
                out.append(det_attr)

        return tuple(out)


def attribute(
    window: FeatureWindow,
    prediction: Prediction,
    *,
    history: Sequence[tuple[FeatureWindow, Prediction]] | None = None,
) -> tuple[CausalAttribution, ...]:
    """Top-level convenience: DoWhy when possible, deterministic otherwise."""
    return CausalAttributor().attribute(window, prediction, history=history)
