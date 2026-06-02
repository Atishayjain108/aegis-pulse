"""
Centralized constants for the Predictive Apex.

Project rule: every magic number is named, documented with a
rationale, and collected here. Constants are imported by name —
never inlined — so a sweep through this file gives the full picture
of every threshold the system uses to make a decision.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

# =============================================================================
# Latency budgets — enforced by middleware in the inference service.
# =============================================================================

# rationale: KPI from MISSION_OBJECTIVE — model inference p99 < 500ms.
# We budget at 400ms internal so the network adds <100ms round-trip.
INFERENCE_LATENCY_BUDGET_MS: float = 400.0

# rationale: end-to-end pipeline KPI < 2 min. The predict step is
# expected to take <5% of that, so 6s is a hard wall.
INFERENCE_HARD_TIMEOUT_S: float = 6.0

# =============================================================================
# Feature engineering
# =============================================================================

# rationale: 168 hourly buckets = 7 days. Empirically captures one
# weekly seasonality cycle without overwhelming the transformer.
FEATURE_WINDOW_HOURS: int = 168

# rationale: minimum signals to attempt a non-heuristic prediction.
# Below this we always return the heuristic (the data is too sparse
# for a transformer to extract useful structure).
MIN_SIGNALS_FOR_NEURAL: int = 8

# rationale: minimum unique authors. Even 100 signals from 2 authors
# is bot coordination, not a trend; route to heuristic.
MIN_AUTHORS_FOR_NEURAL: int = 4

# rationale: log1p over engagement compresses the tail; raw counts
# can span 0 → 10^7 and explode gradient norms.
ENGAGEMENT_LOG1P: bool = True

# =============================================================================
# Model architecture defaults
# =============================================================================

# PatchTST patch length. Each patch is `PATCH_LEN` consecutive
# timesteps treated as one transformer token.
# rationale: 16 hours per patch ≈ 2/3 of a working day, captures
# intra-day cycles. Original PatchTST paper uses 16 for hourly data.
PATCHTST_PATCH_LEN: int = 16
PATCHTST_STRIDE: int = 8  # 50% overlap
PATCHTST_D_MODEL: int = 128
PATCHTST_N_HEADS: int = 8
PATCHTST_N_LAYERS: int = 3
PATCHTST_DROPOUT: float = 0.2  # dropout used by MC-dropout uncertainty

# Autoformer
AUTOFORMER_D_MODEL: int = 128
AUTOFORMER_MOVING_AVG_KERNEL: int = 25
AUTOFORMER_FACTOR: int = 3

# TimesNet (FFT-folded 2-D inception)
# rationale: d_model=128 matches PatchTST/Autoformer so the fusion MLP
# can mix all three temporal heads homogeneously.
TIMESNET_D_MODEL: int = 128
# rationale: 2 inception layers is enough for our 168-step window;
# more layers add params without measurable AUC lift in our backtests.
TIMESNET_N_LAYERS: int = 2
# rationale: keep top-3 dominant FFT periods. This is enough to capture
# the main daily cycle, the weekly cycle, and one residual oscillation,
# while keeping the routing softmax tight (3-way is well-conditioned).
TIMESNET_K_PERIODS: int = 3
TIMESNET_DROPOUT: float = 0.2

# HGT (heterogeneous graph transformer)
HGT_HIDDEN_DIM: int = 128
HGT_N_LAYERS: int = 2
HGT_N_HEADS: int = 4
HGT_DROPOUT: float = 0.2

# Fusion MLP
FUSION_HIDDEN_DIMS: tuple[int, ...] = (256, 128, 64)
FUSION_DROPOUT: float = 0.2

# =============================================================================
# Uncertainty quantification
# =============================================================================

# rationale: 5 ensemble members balances variance reduction
# (diminishing returns past 5) vs train+inference cost.
DEEP_ENSEMBLE_SIZE: int = 5

# rationale: 30 MC-dropout samples gives ±2% std-error on tail
# probabilities at our class imbalance (Lakshminarayanan et al.).
MC_DROPOUT_SAMPLES: int = 30

# rationale: 90% conformal interval. Per industry convention; lets
# us label predictions "calibrated to 1-in-10 miss".
CONFORMAL_ALPHA: float = 0.10

# =============================================================================
# Heuristic baseline thresholds
# =============================================================================

# rationale: signal-count + velocity floor for "BREAKOUT" classification
# in the heuristic. Calibrated against the Phase 1 historical sample
# (754 signals across 4 platforms) — at this floor, ~3% of trends
# qualify, matching the empirical breakout base rate.
HEURISTIC_BREAKOUT_VELOCITY_24H: float = 1.5  # log-units / hour
HEURISTIC_BREAKOUT_MIN_SIGNALS: int = 25
HEURISTIC_BREAKOUT_MIN_AUTHORS: int = 10

# rationale: peak detection — velocity decelerating but still positive.
HEURISTIC_PEAK_DECEL_THRESHOLD: float = -0.3

# rationale: declining — second derivative clearly negative.
HEURISTIC_DECLINING_VELOCITY_24H: float = -0.5

# rationale: confidence ceiling for heuristic-only predictions.
# Even a strong heuristic signal can't claim >0.75 — the neural
# model gets to push above this when it agrees.
HEURISTIC_CONFIDENCE_CEILING: float = 0.75

# =============================================================================
# Trend analysis — EMA / OLS / momentum (Phase 3.1 upgrade)
# =============================================================================

# rationale: 3-bucket short EMA captures intra-day momentum spikes;
# 12-bucket long EMA captures the half-day trend direction.
# Ratio short/long > 1.0 is the classic golden-cross indicator.
EMA_SHORT_PERIOD: int = 3
EMA_LONG_PERIOD: int = 12

# rationale: EMA cross above/below this delta triggers a bullish/bearish
# momentum bonus in the confidence score.
MOMENTUM_BULLISH_THRESHOLD: float = 0.15   # short EMA > long EMA by 15 %
MOMENTUM_BEARISH_THRESHOLD: float = -0.15  # short EMA < long EMA by 15 %

# rationale: only trust the OLS trend line when R² exceeds this floor.
# Below 0.40 the relationship between time and count is too noisy to
# use for velocity extrapolation; revert to the EMA-decay path.
OLS_STRONG_R2: float = 0.40

# rationale: OLS horizon cap — beyond 24 h the trend line extrapolation
# diverges; blend it out entirely for h > 24 to avoid over-confident
# long-range calls.
OLS_HORIZON_CAP_HOURS: int = 24

# =============================================================================
# Backtest
# =============================================================================

# rationale: 30-day walk-forward window per industry standard.
BACKTEST_TEST_DAYS: int = 30

# rationale: 7-day purge gap between train and test, prevents
# leakage from features that aggregate over a week.
BACKTEST_PURGE_DAYS: int = 7

# rationale: 20% adversarial noise injection probability per
# AEGIS_PULSE_OMEGA spec — tests robustness.
BACKTEST_ADVERSARIAL_NOISE_PROB: float = 0.20

# =============================================================================
# Model promotion gates
# =============================================================================

# rationale: a candidate model must beat the production champion by
# this margin on macro_f1 over the held-out walk-forward window.
PROMOTION_F1_MARGIN: float = 0.02

# rationale: minimum number of backtest samples before a promotion
# decision binds. Below this F1 is dominated by noise; we want at
# least ~1k held-out predictions before declaring a winner.
PROMOTION_MIN_BACKTEST_SAMPLES: int = 1000

# rationale: shadow deployment duration before promotion.
SHADOW_DEPLOY_HOURS: int = 72

# rationale: auto-rollback if production precision drops by more
# than this week-over-week.
AUTO_ROLLBACK_PRECISION_DROP: float = 0.05

# =============================================================================
# Action mapping
# =============================================================================

# rationale: minimum p_breakout to recommend ENTER. Below this we
# default to OBSERVE.
ACTION_ENTER_PROBABILITY_FLOOR: float = 0.55

# rationale: above this, EXIT regardless of velocity.
ACTION_EXIT_PROBABILITY_CEILING: float = 0.65

# rationale: below this confidence we never recommend ENTER or EXIT.
ACTION_CONFIDENCE_FLOOR: float = 0.45

# =============================================================================
# Execution policy (RL layer)
# =============================================================================

# rationale: fractional Kelly. Full Kelly maximises long-run growth but
# the variance is brutal (drawdowns to 50% of peak are routine). 0.25
# Kelly is the standard professional convention — half the growth rate,
# 1/16th the drawdown variance.
KELLY_FRACTION: float = 0.25

# rationale: hard upper bound on a single bet, regardless of Kelly.
# Caps tail risk on misspecified payoff_ratio.
MAX_POSITION_FRACTION: float = 0.10

# rationale: if more than this fraction of open positions share a
# platform, refuse new same-platform entries. Crude correlation proxy.
# Phase 2's HEDGE agent does the proper correlation work; this is the
# Phase 3 floor that runs even without HEDGE.
PORTFOLIO_CORRELATION_CEILING: float = 0.50

# rationale: stop-loss tier 3 — at -25% drawdown we exit unconditionally.
# Tiers 1 (-10%) and 2 (-18%) are handled in Phase 4 by the executor.
STOP_LOSS_DRAWDOWN: float = 0.25

# =============================================================================
# Serving
# =============================================================================

# rationale: cap on /predict/batch — keeps a single HTTP call's tail
# latency bounded. Above ~64 we'd blow through the 500 ms budget on
# any single slow request.
PREDICT_BATCH_MAX: int = 32


__all__ = [name for name in globals() if name.isupper()]
