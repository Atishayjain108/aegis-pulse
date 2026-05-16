"""
Walk-forward backtester.

Why walk-forward and not k-fold:
--------------------------------
Time-series data has temporal autocorrelation; random k-fold leaks the
future into the past. A walk-forward backtest trains on [t0, t1],
evaluates on [t1+purge, t2], slides forward, repeats. The `purge`
window is critical for trend detection because labels (did this trend
break out?) are only knowable after a horizon has elapsed — so naive
sliding windows would label a sample with information that wasn't
available at training time.

Outputs `BacktestResult` schemas which the registry's promotion gate
consumes.
"""

from .metrics import compute_metrics
from .runner import (
    AggregatedMetrics,
    BacktestSpec,
    WalkForwardBacktester,
    run_backtest,
)

__all__ = [
    "AggregatedMetrics",
    "BacktestSpec",
    "WalkForwardBacktester",
    "compute_metrics",
    "run_backtest",
]
