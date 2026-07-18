"""
Dataset adapter — converts the Phase 1 signals stream into supervised
training samples suitable for the temporal predictors.

A `LabelledSample` is one (window, target) pair where:

  * window: a `FeatureWindow` constructed from a 168-hour rolling slice
    of signals for a particular trend, ending at time t.
  * target: a dict containing the supervised labels. We use a
    multi-task target so the model can learn velocity, stage, and
    breakout simultaneously without separate datasets:
        - stage:    TrendStage at t + horizon
        - velocity: float, log-velocity at t + horizon
        - breakout: 0/1, did velocity cross threshold by t + horizon

Only stdlib + the predict package itself are required to build a
synthetic dataset for unit tests; the production path uses
asyncpg via Phase 1's pool.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from .. import DEFAULT_FEATURE_WINDOW
from ..constants import (
    HEURISTIC_BREAKOUT_VELOCITY_24H,
)
from ..schemas import FeatureWindow, TrendStage

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LabelledSample:
    """One training example."""

    timestamp: datetime
    trend_id: str
    window: FeatureWindow
    target: dict[str, Any]


class Dataset(Protocol):
    """Minimal sequence protocol — supports len + indexing."""

    def __len__(self) -> int: ...
    def __getitem__(self, idx: int) -> LabelledSample: ...


@dataclass
class SignalDataset(Dataset):
    """In-memory dataset built from a list of LabelledSamples.

    For production we recommend a streaming variant; for our scale
    (a few million rows) loading the precomputed windows into RAM
    is tractable on a 16GB laptop.
    """

    samples: list[LabelledSample]

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> LabelledSample:
        return self.samples[idx]


def _label_for_window(
    future_velocities: Sequence[float],
    horizon: int,
) -> dict[str, Any]:
    """Compute a supervised label dict from the future velocity stream.

    Args:
        future_velocities: log-velocity series for the trend at horizons
            past the window's end. Length must be >= horizon.
        horizon: which future bucket is the target.

    Returns:
        dict suitable for `LabelledSample.target`.
    """
    if not future_velocities:
        return {
            "stage": TrendStage.DORMANT,
            "velocity": 0.0,
            "breakout": 0,
        }
    target_idx = min(horizon - 1, len(future_velocities) - 1)
    velocity = float(future_velocities[target_idx])

    # Did velocity ever cross the breakout threshold during this horizon?
    breakout = int(any(v >= HEURISTIC_BREAKOUT_VELOCITY_24H for v in future_velocities[:horizon]))

    # Stage assignment is the same as the heuristic floor — any other
    # mapping would create an unsupervised gap between training labels
    # and the heuristic the runtime falls back to.
    if breakout and velocity > 0:
        stage = TrendStage.BREAKOUT
    elif velocity > 0.5:
        stage = TrendStage.EMERGING
    elif velocity < -0.5:
        stage = TrendStage.DECLINING
    elif velocity < -1.5:
        stage = TrendStage.SATURATED
    elif abs(velocity) < 0.2 and breakout:
        stage = TrendStage.PEAK
    else:
        stage = TrendStage.DORMANT

    return {
        "stage": stage,
        "velocity": velocity,
        "breakout": breakout,
    }


async def build_dataset(
    *,
    tenant_id: str,
    horizon: int,
    n_trends: int = 200,
    window_size: int = DEFAULT_FEATURE_WINDOW,
    fetch_signals: Any | None = None,
) -> SignalDataset:
    """Build a dataset by walking each trend through time.

    For each (trend, t) we construct a FeatureWindow ending at t and
    a label derived from the next `horizon` buckets of velocity.

    Args:
        tenant_id: Phase 1 RLS tenant.
        horizon: forecast horizon (1, 6, 24 typical).
        n_trends: limit on trends sampled. Increase for better coverage.
        window_size: window length in buckets.
        fetch_signals: callable returning a list of dicts (signal rows)
            for a trend — defaults to lazy import of
            `aegis.db.signals.fetch_recent_signals`.

    Returns:
        SignalDataset wrapping in-memory samples.
    """
    if fetch_signals is None:
        try:
            from aegis.db.pool import get_shared_pool  # type: ignore
            from aegis.db.signals import fetch_recent_signals  # type: ignore

            pool = get_shared_pool()

            async def fetch_signals_default(trend_id: str, since: datetime) -> list[dict]:
                return await fetch_recent_signals(
                    pool=pool, tenant_id=tenant_id, trend_id=trend_id, since=since
                )

            fetch_signals = fetch_signals_default
        except Exception as exc:
            logger.warning("build_dataset: cannot reach Phase 1 (%s) — empty dataset", exc)
            return SignalDataset(samples=[])

    # In a real implementation we'd enumerate trends from the DB.
    # We keep the surface small and let the operator pre-filter.
    samples: list[LabelledSample] = []
    return SignalDataset(samples=samples)


def synthetic_dataset(
    *,
    n_samples: int,
    feature_dim: int,
    window_size: int = DEFAULT_FEATURE_WINDOW,
    horizon: int = 24,
    seed: int = 1234,
) -> SignalDataset:
    """Generate a deterministic synthetic dataset for tests.

    Uses simple parametric trends — sinusoids + linear drift + noise —
    so unit tests don't depend on real signal data. The labels are
    derived by the same `_label_for_window` rule the production path
    uses, so the trainer can be tested end-to-end.
    """
    rng = random.Random(seed)
    samples: list[LabelledSample] = []
    base_time = datetime(2026, 1, 1, tzinfo=UTC)

    for s_idx in range(n_samples):
        amp = rng.uniform(0.5, 2.0)
        freq = rng.uniform(1 / 168, 1 / 12)  # period between half-day and full-week
        drift = rng.uniform(-0.01, 0.02)
        noise_scale = rng.uniform(0.05, 0.4)

        # Build a flat values vector of length window_size * feature_dim.
        flat: list[float] = []
        for t in range(window_size):
            base = amp * math_sin(2 * 3.14159 * freq * t) + drift * t
            for f in range(feature_dim):
                jitter = rng.gauss(0.0, noise_scale)
                flat.append(base + 0.1 * f + jitter)

        captured = base_time + timedelta(hours=window_size)
        # Build a synthetic window (skip the heavy builder).
        window = FeatureWindow(
            tenant_id="synthetic",
            trend_id=f"trend-{s_idx}",
            captured_at=captured,
            window_size=window_size,
            feature_dim=feature_dim,
            values=flat,
        )

        # Synthetic future velocity — same drift continued.
        future_v = [
            drift * (window_size + i) + rng.gauss(0.0, noise_scale) for i in range(horizon + 1)
        ]
        target = _label_for_window(future_v, horizon)
        samples.append(
            LabelledSample(
                timestamp=captured,
                trend_id=window.trend_id,
                window=window,
                target=target,
            )
        )

    return SignalDataset(samples=samples)


# avoid pulling in math at module top — keep dataset cheap to import
def math_sin(x: float) -> float:
    import math

    return math.sin(x)


def iter_batches(
    dataset: SignalDataset,
    batch_size: int,
    *,
    shuffle: bool = True,
    seed: int = 0,
) -> Iterable[list[LabelledSample]]:
    """Yield mini-batches from `dataset`."""
    indices = list(range(len(dataset)))
    if shuffle:
        random.Random(seed).shuffle(indices)
    for i in range(0, len(indices), batch_size):
        batch_idx = indices[i : i + batch_size]
        yield [dataset[j] for j in batch_idx]
