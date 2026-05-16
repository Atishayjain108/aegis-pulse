"""
Walk-forward backtester.

Public surface:
    BacktestSpec               — frozen config
    WalkForwardBacktester      — runs the slide
    run_backtest()             — convenience function
    AggregatedMetrics          — cross-fold averages

Concept
-------
Given a flat sequence of (window, truth_at_h) samples already sorted in
time, we slide a (train, test) window forward in fixed steps:

    [train_start ─── train_end][purge][test_start ─── test_end]
                                         <─── advance step ───>

The model is *retrained* (or re-calibrated) inside each step. For
heuristic predictors there is nothing to retrain; for calibrators
(Platt, isotonic) we re-fit on the train slice; for neural nets the
caller supplies a `train_fn` that handles the heavy lift.

The `purge` window is the maximum forecasting horizon. Without it,
labels for the last `h` steps of the train slice would have been
constructed from data that overlaps the test slice, causing leakage.

Adversarial noise (controlled by `BacktestSpec.adversarial_noise_prob`)
flips a fraction of the test windows by injecting Gaussian jitter on
features. This is a cheap robustness check: a model that was "winning"
only because it overfit clean validation data will visibly degrade.

Output
------
We produce ONE `BacktestResult` per fold. The aggregated metrics
across folds are reported alongside (`AggregatedMetrics`) and used
by the registry's promotion gate.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .. import DEFAULT_HORIZONS
from ..constants import (
    BACKTEST_ADVERSARIAL_NOISE_PROB,
    BACKTEST_PURGE_DAYS,
    BACKTEST_TEST_DAYS,
)
from ..errors import LookaheadError
from ..schemas import BacktestResult, FeatureWindow, Prediction
from .metrics import (
    compute_metrics,
    expected_calibration_error,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BacktestSpec:
    """Configuration for a single walk-forward backtest run."""

    horizon: int  # which forecasting horizon to score
    train_days: int = 60  # rolling train window length
    test_days: int = BACKTEST_TEST_DAYS
    purge_days: int = BACKTEST_PURGE_DAYS
    step_days: int = 7  # advance the window one week per fold
    adversarial_noise_prob: float = BACKTEST_ADVERSARIAL_NOISE_PROB
    adversarial_noise_sigma: float = 0.05
    seed: int = 1234

    def __post_init__(self) -> None:
        if self.horizon not in DEFAULT_HORIZONS:
            logger.warning(
                "backtest horizon %d not in DEFAULT_HORIZONS=%s",
                self.horizon,
                DEFAULT_HORIZONS,
            )
        if self.purge_days < 1:
            object.__setattr__(self, "purge_days", 1)


@dataclass
class _Sample:
    """One backtest example. Kept private — schemas don't need it."""

    timestamp: datetime
    window: FeatureWindow
    truth: dict[str, Any]


PredictFn = Callable[[FeatureWindow], Awaitable[Prediction]]
TrainFn = Callable[[Sequence[_Sample]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class AggregatedMetrics:
    """Cross-fold averages — what the promotion gate compares.

    BacktestResult is locked to a single fold (it carries fold_index +
    train/test windows). The promotion gate needs *averages* across
    folds; this struct carries them.
    """

    n_folds: int
    n_test_total: int
    accuracy: float
    macro_f1: float
    breakout_precision: float
    breakout_recall: float
    mae_log_velocity: float
    coverage_90: float
    sharpness: float
    ece: float
    brier_breakout: float


def _default_train_fn() -> TrainFn:
    async def _noop(_samples: Sequence[_Sample]) -> None:
        return None

    return _noop


def _check_no_lookahead(
    train_samples: Sequence[_Sample],
    test_samples: Sequence[_Sample],
    purge_seconds: float,
) -> None:
    """Raise LookaheadError if the train window touches the test window."""
    if not train_samples or not test_samples:
        return
    last_train = max(s.timestamp for s in train_samples)
    first_test = min(s.timestamp for s in test_samples)
    gap = (first_test - last_train).total_seconds()
    if gap < purge_seconds:
        raise LookaheadError(
            f"purge violated: gap={gap:.0f}s < required={purge_seconds:.0f}s "
            f"(last_train={last_train.isoformat()}, first_test={first_test.isoformat()})"
        )


def _inject_noise(
    window: FeatureWindow,
    rng: random.Random,
    sigma: float,
) -> FeatureWindow:
    """Return a copy of the window with Gaussian jitter on every feature.

    Cyclic time features (indices 16..19) are NOT perturbed — corrupting
    them would not test robustness, just nonsense.
    """
    cyclic = {16, 17, 18, 19}
    rows = window.as_2d()
    new_flat: list[float] = []
    for row in rows:
        for i, v in enumerate(row):
            if i in cyclic:
                new_flat.append(float(v))
            else:
                new_flat.append(float(v) + rng.gauss(0.0, sigma))
    return window.model_copy(update={"values": new_flat})


def _accuracy(y_true: Sequence[Any], y_pred: Sequence[Any]) -> float:
    if not y_true:
        return 0.0
    return sum(1 for t, p in zip(y_true, y_pred, strict=False) if t == p) / len(y_true)


def _pinball(quantile_preds: Sequence[float], truths: Sequence[float], q: float) -> float:
    """Pinball loss for a quantile estimator (asymmetric absolute error)."""
    if not quantile_preds:
        return 0.0
    n = len(quantile_preds)
    total = 0.0
    for p, t in zip(quantile_preds, truths, strict=False):
        diff = t - p
        total += max(q * diff, (q - 1) * diff)
    return total / n


def _build_fold_result(
    *,
    model_id: str,
    fold_index: int,
    spec: BacktestSpec,
    train_lo_ts: float,
    train_hi_ts: float,
    test_lo_ts: float,
    test_hi_ts: float,
    n_train: int,
    predictions: Sequence[Prediction],
    truths: Sequence[dict],
) -> BacktestResult:
    """Build a single-fold BacktestResult conforming to the schema."""
    n_test = len(predictions)
    if n_test == 0:
        return BacktestResult(
            model_id=model_id,
            fold_index=fold_index,
            train_start=datetime.fromtimestamp(train_lo_ts, tz=UTC),
            train_end=datetime.fromtimestamp(train_hi_ts, tz=UTC),
            test_start=datetime.fromtimestamp(test_lo_ts, tz=UTC),
            test_end=datetime.fromtimestamp(test_hi_ts, tz=UTC),
            n_train=n_train,
            n_test=0,
            accuracy=0.0,
            macro_f1=0.0,
            breakout_precision=0.0,
            breakout_recall=0.0,
            mae_log_velocity=0.0,
            pinball_p10=0.0,
            pinball_p90=0.0,
            ece=0.0,
            coverage_90=0.0,
            sharpness=0.0,
            notes=f"empty_fold:horizon={spec.horizon}",
        )

    m = compute_metrics(predictions, truths)
    y_true = [t["stage"] for t in truths]
    y_pred = [p.stage for p in predictions]
    acc = _accuracy(y_true, y_pred)

    # Breakout-specific precision / recall (binary).
    tp = sum(
        1
        for p, t in zip(predictions, truths, strict=False)
        if int(t.get("breakout", 0)) == 1 and p.p_breakout >= 0.5
    )
    fp = sum(
        1
        for p, t in zip(predictions, truths, strict=False)
        if int(t.get("breakout", 0)) == 0 and p.p_breakout >= 0.5
    )
    fn = sum(
        1
        for p, t in zip(predictions, truths, strict=False)
        if int(t.get("breakout", 0)) == 1 and p.p_breakout < 0.5
    )
    breakout_prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    breakout_rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0

    truths_v = [float(t.get("velocity", 0.0)) for t in truths]
    p10s = [p.velocity_p10 for p in predictions]
    p90s = [p.velocity_p90 for p in predictions]
    pinball_10 = _pinball(p10s, truths_v, 0.10)
    pinball_90 = _pinball(p90s, truths_v, 0.90)
    sharpness = sum(hi - lo for lo, hi in zip(p10s, p90s, strict=False)) / n_test

    p_breakout = [p.p_breakout for p in predictions]
    o_breakout = [int(t.get("breakout", 0)) for t in truths]
    ece = expected_calibration_error(p_breakout, o_breakout)

    return BacktestResult(
        model_id=model_id,
        fold_index=fold_index,
        train_start=datetime.fromtimestamp(train_lo_ts, tz=UTC),
        train_end=datetime.fromtimestamp(train_hi_ts, tz=UTC),
        test_start=datetime.fromtimestamp(test_lo_ts, tz=UTC),
        test_end=datetime.fromtimestamp(test_hi_ts, tz=UTC),
        n_train=n_train,
        n_test=n_test,
        accuracy=min(1.0, max(0.0, acc)),
        macro_f1=min(1.0, max(0.0, float(m["macro_f1"]))),
        breakout_precision=min(1.0, max(0.0, breakout_prec)),
        breakout_recall=min(1.0, max(0.0, breakout_rec)),
        mae_log_velocity=max(0.0, float(m["mae_velocity"])),
        pinball_p10=max(0.0, pinball_10),
        pinball_p90=max(0.0, pinball_90),
        ece=max(0.0, ece),
        coverage_90=min(1.0, max(0.0, float(m["coverage_p10_p90"]))),
        sharpness=max(0.0, sharpness),
        notes=f"horizon={spec.horizon}",
    )


@dataclass
class WalkForwardBacktester:
    """Slides a (train, test) window forward across a sample stream."""

    spec: BacktestSpec
    train_fn: TrainFn | None = None
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.spec.seed)
        if self.train_fn is None:
            self.train_fn = _default_train_fn()

    async def run(
        self,
        samples: Sequence[_Sample],
        predict_fn: PredictFn,
        *,
        model_id: str,
    ) -> tuple[tuple[BacktestResult, ...], AggregatedMetrics]:
        """Run the walk-forward backtest across all folds."""
        if not samples:
            return (), AggregatedMetrics(
                n_folds=0,
                n_test_total=0,
                accuracy=0.0,
                macro_f1=0.0,
                breakout_precision=0.0,
                breakout_recall=0.0,
                mae_log_velocity=0.0,
                coverage_90=0.0,
                sharpness=0.0,
                ece=0.0,
                brier_breakout=0.0,
            )

        sorted_samples = sorted(samples, key=lambda s: s.timestamp)
        first_t = sorted_samples[0].timestamp
        last_t = sorted_samples[-1].timestamp

        train_s = self.spec.train_days * 86400
        test_s = self.spec.test_days * 86400
        purge_s = self.spec.purge_days * 86400
        step_s = self.spec.step_days * 86400

        fold = 0
        results: list[BacktestResult] = []
        all_preds: list[Prediction] = []
        all_truths: list[dict] = []
        cursor_t = first_t.timestamp() + train_s

        while cursor_t + purge_s + test_s <= last_t.timestamp():
            train_lo = cursor_t - train_s
            train_hi = cursor_t
            test_lo = train_hi + purge_s
            test_hi = test_lo + test_s

            train_chunk = [
                s for s in sorted_samples if train_lo <= s.timestamp.timestamp() < train_hi
            ]
            test_chunk = [s for s in sorted_samples if test_lo <= s.timestamp.timestamp() < test_hi]

            if not train_chunk or not test_chunk:
                cursor_t += step_s
                continue

            _check_no_lookahead(train_chunk, test_chunk, purge_s)

            assert self.train_fn is not None
            await self.train_fn(train_chunk)

            fold_preds: list[Prediction] = []
            fold_truths: list[dict] = []
            for s in test_chunk:
                window = s.window
                if (
                    self.spec.adversarial_noise_prob > 0
                    and self._rng.random() < self.spec.adversarial_noise_prob
                ):
                    window = _inject_noise(window, self._rng, self.spec.adversarial_noise_sigma)
                pred = await predict_fn(window)
                fold_preds.append(pred)
                fold_truths.append(s.truth)

            results.append(
                _build_fold_result(
                    model_id=model_id,
                    fold_index=fold,
                    spec=self.spec,
                    train_lo_ts=train_lo,
                    train_hi_ts=train_hi,
                    test_lo_ts=test_lo,
                    test_hi_ts=test_hi,
                    n_train=len(train_chunk),
                    predictions=fold_preds,
                    truths=fold_truths,
                )
            )
            all_preds.extend(fold_preds)
            all_truths.extend(fold_truths)
            fold += 1
            cursor_t += step_s

        if fold == 0:
            logger.warning("backtest produced 0 folds")

        agg = self._aggregate(results, all_preds, all_truths)
        return tuple(results), agg

    def _aggregate(
        self,
        folds: Sequence[BacktestResult],
        all_preds: Sequence[Prediction],
        all_truths: Sequence[dict],
    ) -> AggregatedMetrics:
        """Average metrics across folds, weighted by n_test."""
        if not folds:
            return AggregatedMetrics(
                n_folds=0,
                n_test_total=0,
                accuracy=0.0,
                macro_f1=0.0,
                breakout_precision=0.0,
                breakout_recall=0.0,
                mae_log_velocity=0.0,
                coverage_90=0.0,
                sharpness=0.0,
                ece=0.0,
                brier_breakout=0.0,
            )
        total = sum(f.n_test for f in folds) or 1

        def w(attr: str) -> float:
            return sum(getattr(f, attr) * f.n_test for f in folds) / total

        # Brier across all folds
        p_breakout = [p.p_breakout for p in all_preds]
        o_breakout = [int(t.get("breakout", 0)) for t in all_truths]
        from .metrics import _brier

        brier = _brier(p_breakout, o_breakout) if p_breakout else 0.0

        return AggregatedMetrics(
            n_folds=len(folds),
            n_test_total=total,
            accuracy=w("accuracy"),
            macro_f1=w("macro_f1"),
            breakout_precision=w("breakout_precision"),
            breakout_recall=w("breakout_recall"),
            mae_log_velocity=w("mae_log_velocity"),
            coverage_90=w("coverage_90"),
            sharpness=w("sharpness"),
            ece=w("ece"),
            brier_breakout=brier,
        )


async def run_backtest(
    samples: Sequence[_Sample],
    predict_fn: PredictFn,
    *,
    horizon: int,
    model_id: str,
    spec: BacktestSpec | None = None,
    train_fn: TrainFn | None = None,
) -> tuple[tuple[BacktestResult, ...], AggregatedMetrics]:
    """One-call convenience over `WalkForwardBacktester`."""
    spec = spec or BacktestSpec(horizon=horizon)
    backtester = WalkForwardBacktester(spec=spec, train_fn=train_fn)
    return await backtester.run(samples, predict_fn, model_id=model_id)
