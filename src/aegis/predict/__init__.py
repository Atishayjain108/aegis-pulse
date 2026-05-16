"""
AEGIS Pulse — Phase 3: Predictive Apex (Hybrid ML Core).

This package implements the model layer that turns Phase 1 signals
and Phase 2 agent decisions into calibrated, uncertainty-quantified
predictions of trend lifecycle stage and 24/72-hour velocity.

Architecture (top-down):

    Phase 1 signals  ──►  features.builder.build_feature_window()
                              │
                              ▼
                          tensors  ──►  ┌────────────────┐
                                        │ PatchTST       │  (temporal)
                                        │ Autoformer     │  swappable
                                        │ TimesNet       │
                                        └───────┬────────┘
    Phase 2 graph    ──►  features.graph.build_creator_graph()
                              │                 │
                              ▼                 ▼
                          PyG Data ──►   ┌────────────────┐
                                         │ HGT            │  (relational)
                                         │ GraphSAGE+GAT  │  swappable
                                         └───────┬────────┘
                                                 │
                              ┌──────────────────┘
                              ▼
                          ┌────────────────┐
                          │ EnsembleFusion │  3-layer MLP +
                          │                │  Platt + isotonic
                          └───────┬────────┘
                                  ▼
                          ┌──────────────────┐
                          │ UncertaintyHead  │  deep ensembles
                          │                  │  + MC dropout
                          │                  │  + conformal
                          └───────┬──────────┘
                                  ▼
                          ┌──────────────────┐
                          │  CausalLayer     │  DoWhy-style
                          │  (DoWhy/EconML)  │  attribution
                          └───────┬──────────┘
                                  ▼
                          ┌──────────────────┐
                          │  RL Policy (PPO) │  acts on prediction
                          │  (Ray RLlib)     │  + portfolio state
                          └──────────────────┘

Doctrine (re-stated, identical to Phase 2):

    HEURISTIC FIRST.  MODEL AS AUGMENTATION.

Every prediction has a deterministic, model-free baseline. The
neural models, when available, refine the baseline — they cannot
flip a verdict on their own. This guarantees:

  * Tests run fully offline, no GPU, no weight downloads.
  * `aegis predict` works on a fresh laptop in <30 s.
  * Catastrophic model failure (NaNs, OOM, mismatched dims) is
    caught and logged; the pipeline returns the heuristic.
  * Audit trails are reproducible: a verdict is a function of
    features + (optional) model_id + (optional) seed.

Author:  AEGIS Pulse core team
Phase:   3 (Predictive Apex)
"""

from __future__ import annotations

__all__ = [
    "PHASE",
    "MODELS",
    "FEATURE_NAMES",
    "DEFAULT_HORIZONS",
    "DEFAULT_FEATURE_WINDOW",
]

PHASE: str = "3"

# Canonical model registry. Strings here are validated against the
# loader factory — anything outside this set is rejected at load time.
MODELS: tuple[str, ...] = (
    # Temporal backbones
    "patchtst",
    "autoformer",
    "timesnet",
    # Relational backbones
    "hgt",
    "graphsage_gat",
    # Fusion / ensemble
    "ensemble_fusion",
    # Uncertainty wrappers
    "deep_ensemble",
    "mc_dropout",
    # Causal
    "dowhy_causal",
    "tabpfn_counterfactual",
    # RL
    "ppo_executor",
    # Heuristic baselines (always available)
    "heuristic_temporal",
    "heuristic_relational",
)

# Canonical feature window:
#   - 168 hourly buckets = 7 days of history
#   - 24 hourly buckets  = 1 day  forecast horizon (default)
DEFAULT_FEATURE_WINDOW: int = 168
DEFAULT_HORIZONS: tuple[int, ...] = (1, 6, 24, 72)  # hours-ahead

# Per-bucket feature vector. Order is the schema — DO NOT reorder
# without bumping FEATURE_SCHEMA_VERSION below; ONNX models compiled
# against an older order will silently produce garbage on newer data.
FEATURE_NAMES: tuple[str, ...] = (
    "signal_count",
    "unique_authors",
    "platform_diversity",  # entropy across platforms
    "velocity_1h",
    "velocity_6h",
    "velocity_24h",
    "sentiment_mean",
    "sentiment_std",
    "commercial_intent",
    "novelty",
    "coordination_risk",
    "engagement_total",  # views+likes+comments+shares+saves (log1p)
    "engagement_per_author",
    "comment_density",  # comments / signal_count
    "share_density",  # shares / signal_count
    "save_density",  # saves / signal_count
    "hour_of_day_sin",
    "hour_of_day_cos",
    "day_of_week_sin",
    "day_of_week_cos",
)
FEATURE_DIM: int = len(FEATURE_NAMES)

# Bump on any change to FEATURE_NAMES order or semantics.
FEATURE_SCHEMA_VERSION: str = "3.0.0"
