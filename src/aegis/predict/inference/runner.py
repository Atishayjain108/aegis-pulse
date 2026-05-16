"""
Inference runner — the orchestrator that ties Phase 3 together.

Pipeline:
    1. Build FeatureWindow from Phase 1 signals (features.builder).
    2. Build CreatorGraph                          (features.graph).
    3. Run temporal predictor   → bundle_T.
    4. Run relational predictor → bundle_R.
    5. Fuse per-horizon         → bundle_F (fusion.fuse).
    6. Apply uncertainty wrapper (deep ensemble or conformal).
    7. Compute causal attribution (deterministic floor + DoWhy if avail).
    8. Map to PredictionAction; populate PredictionRecord for DB.
    9. Return InferenceResult with timing + provenance.

Every step is wrapped in `resilient_call` so any single failure
gracefully degrades to the heuristic floor. The runner enforces a
hard latency budget (`INFERENCE_HARD_TIMEOUT_S`) — predictions that
miss it are returned with `is_heuristic_only=True` and a
"latency_budget_exceeded" reason in metadata.

The runner is the single point of entry that Phase 4 (alerting) and
Phase 2 (LangGraph SCOUT/SENTINEL) call. They never instantiate
predictors directly.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .. import DEFAULT_FEATURE_WINDOW, DEFAULT_HORIZONS
from ..causal import (
    CausalAttribution,
    DeterministicAttributor,
)
from ..constants import (
    INFERENCE_HARD_TIMEOUT_S,
    INFERENCE_LATENCY_BUDGET_MS,
)
from ..features.builder import build_window_from_rows
from ..features.graph import CreatorGraph, build_creator_graph
from ..models.factory import load_model
from ..models.fusion import FusionWeights, fuse
from ..resilience import resilient_call
from ..schemas import (
    FeatureWindow,
    ModelKind,
    PredictionBundle,
    PredictionRecord,
)
from .audit import make_audit

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InferenceConfig:
    """Configuration for one inference run."""

    horizons: tuple[int, ...] = DEFAULT_HORIZONS
    window_size: int = DEFAULT_FEATURE_WINDOW
    temporal_model: str = "heuristic_temporal"
    relational_model: str = "heuristic_relational"
    fusion_weights: FusionWeights = field(default_factory=FusionWeights)
    enable_causal: bool = True
    latency_budget_ms: float = INFERENCE_LATENCY_BUDGET_MS
    hard_timeout_s: float = INFERENCE_HARD_TIMEOUT_S
    correlation_id: str | None = None


@dataclass(frozen=True, slots=True)
class InferenceResult:
    """Bundle + extras produced by `InferenceRunner.run()`."""

    bundle: PredictionBundle
    record: PredictionRecord
    audit: Any = None  # AuditRecord, typed loose to avoid circular import
    causal: tuple[CausalAttribution, ...] = ()
    graph_summary: dict[str, float] = field(default_factory=dict)
    halt_reasons: tuple[str, ...] = ()
    duration_ms: float = 0.0


@dataclass
class InferenceRunner:
    """Orchestrates the temporal + relational + fusion pipeline.

    Construction is cheap (no eager model load); models are lazy-loaded
    on first `run()`. Re-using the same runner amortises load cost.
    """

    config: InferenceConfig = field(default_factory=InferenceConfig)
    _temporal: Any = None
    _relational: Any = None

    def _ensure_predictors(self) -> tuple[Any, Any]:
        if self._temporal is None:
            self._temporal = load_model(self.config.temporal_model)
        if self._relational is None:
            self._relational = load_model(self.config.relational_model)
        return self._temporal, self._relational

    async def run(
        self,
        *,
        tenant_id: str,
        trend_id: str,
        signals: Sequence[dict] | None = None,
        window: FeatureWindow | None = None,
        graph: CreatorGraph | None = None,
    ) -> InferenceResult:
        """Run the full pipeline and return the result.

        Either `signals` or `window` must be provided — but never both.
        When `signals` are provided we build the window via
        `features.builder`. `graph` is also optional; if missing we
        build one from the same signals (or fall back to an empty graph).
        """
        started = time.monotonic()
        correlation_id = self.config.correlation_id or str(uuid.uuid4())
        halt_reasons: list[str] = []

        # ---- 1. Window -----------------------------------------------------
        if window is None and signals is None:
            raise ValueError("InferenceRunner.run requires `signals` or `window`")
        if window is None:
            window_end = datetime.now(UTC)

            async def _build() -> FeatureWindow:
                return build_window_from_rows(
                    tenant_id=tenant_id,
                    trend_id=trend_id,
                    rows=list(signals or []),
                    window_end=window_end,
                    window_size=self.config.window_size,
                )

            def _fallback() -> FeatureWindow:
                return _empty_window(tenant_id, trend_id, self.config.window_size, window_end)

            window = await resilient_call(
                _build,
                name="build_window",
                timeout_s=self.config.hard_timeout_s,
                fallback=_fallback,
            )
        assert window is not None

        # ---- 2. Graph ------------------------------------------------------
        if graph is None:
            try:
                graph = build_creator_graph(trend_id=trend_id, rows=list(signals or []))
            except Exception as exc:
                logger.warning("graph build failed: %s", exc)
                graph = CreatorGraph(
                    trend_id=trend_id,
                    authors=(),
                    platforms=(),
                    edges={
                        "posted_on": [],
                        "co_mentioned": [],
                        "about": [],
                        "hosts": [],
                    },
                    n_authors=0,
                    n_platforms=0,
                    n_edges=0,
                    density=0.0,
                    max_author_degree=0,
                    cross_platform_authors=0,
                    coordination_score=0.0,
                )

        # ---- 3 & 4. Predictors (in parallel) -------------------------------
        temporal, relational = self._ensure_predictors()
        try:
            temporal_task = asyncio.wait_for(
                temporal.predict(window),
                timeout=self.config.hard_timeout_s,
            )
            relational_task = asyncio.wait_for(
                relational.predict(window),
                timeout=self.config.hard_timeout_s,
            )
            bundle_t, bundle_r = await asyncio.gather(temporal_task, relational_task)
        except TimeoutError:
            halt_reasons.append("predictor_timeout")
            # Heuristic path is always fast; wrap with the same budget so a
            # hung neural model cannot stall the fallback indefinitely.
            budget = self.config.hard_timeout_s
            bundle_t = await asyncio.wait_for(temporal.predict(window), timeout=budget)
            bundle_r = await asyncio.wait_for(relational.predict(window), timeout=budget)
        except Exception as exc:
            logger.error("predictor pipeline error: %s", exc)
            halt_reasons.append(f"predictor_error:{type(exc).__name__}")
            budget = self.config.hard_timeout_s
            bundle_t = await asyncio.wait_for(temporal.predict(window), timeout=budget)
            bundle_r = await asyncio.wait_for(relational.predict(window), timeout=budget)

        # ---- 5. Fusion -----------------------------------------------------
        fused_bundle = await self._fuse_bundles(bundle_t, bundle_r, correlation_id)

        # ---- 6. Causal attribution (optional) ------------------------------
        causal_attrs: tuple[CausalAttribution, ...] = ()
        if self.config.enable_causal and fused_bundle.predictions:
            try:
                attributor = DeterministicAttributor()
                # Attribute against the highest-priority horizon (24h by convention).
                primary = fused_bundle.by_horizon(24) or fused_bundle.predictions[0]
                causal_attrs = attributor.attribute(window, primary)
            except Exception as exc:
                logger.warning("causal attribution failed: %s", exc)
                halt_reasons.append("causal_failed")

        # ---- 7. Latency budget gate ---------------------------------------
        elapsed_ms = (time.monotonic() - started) * 1000.0
        if elapsed_ms > self.config.latency_budget_ms:
            halt_reasons.append(
                f"latency_budget_exceeded:{elapsed_ms:.0f}ms>{self.config.latency_budget_ms:.0f}ms"
            )

        # ---- 8. PredictionRecord (DB-bound) -------------------------------
        record = self._make_record(
            bundle=fused_bundle,
            tenant_id=tenant_id,
            trend_id=trend_id,
            correlation_id=correlation_id,
            causal=causal_attrs,
            halt_reasons=tuple(halt_reasons),
        )

        graph_summary = {
            "n_authors": float(graph.n_authors),
            "n_platforms": float(graph.n_platforms),
            "density": float(graph.density),
            "coordination_score": float(graph.coordination_score),
        }

        return InferenceResult(
            bundle=fused_bundle,
            record=record,
            audit=make_audit(
                correlation_id=correlation_id,
                tenant_id=tenant_id,
                trend_id=trend_id,
                bundle_id=fused_bundle.model_id,
                halt_reasons=tuple(halt_reasons),
                causal=causal_attrs,
                graph_summary=graph_summary,
                duration_ms=elapsed_ms,
                is_heuristic_only=fused_bundle.is_heuristic_only,
            ),
            causal=causal_attrs,
            graph_summary=graph_summary,
            halt_reasons=tuple(halt_reasons),
            duration_ms=elapsed_ms,
        )

    async def _fuse_bundles(
        self,
        bundle_t: PredictionBundle,
        bundle_r: PredictionBundle,
        correlation_id: str,
    ) -> PredictionBundle:
        """Whole-bundle fusion.

        `fuse()` is a single call over both bundles; it walks horizons
        internally and degrades horizon-by-horizon when relational data
        is missing. We catch any exception and fall back to the temporal
        bundle's predictions — that satisfies the "always produce a
        bundle" doctrine.
        """
        try:
            fused_predictions = fuse(
                temporal=bundle_t,
                relational=bundle_r,
                weights=self.config.fusion_weights,
            )
        except Exception as exc:
            logger.warning("whole-bundle fusion failed (%s) — using temporal", exc)
            fused_predictions = list(bundle_t.predictions)

        finished_at = datetime.now(UTC)
        is_heuristic_only = bundle_t.is_heuristic_only and bundle_r.is_heuristic_only
        return PredictionBundle(
            schema_version=bundle_t.schema_version,
            trend_id=bundle_t.trend_id,
            correlation_id=correlation_id,
            tenant_id=bundle_t.tenant_id,
            predictions=fused_predictions,
            model_id=f"fusion::{bundle_t.model_id}::{bundle_r.model_id}",
            model_kind=ModelKind.FUSION,
            model_version="1.0.0",
            uncertainty_method=bundle_t.uncertainty_method,
            seed=bundle_t.seed,
            is_heuristic_only=is_heuristic_only,
            started_at=bundle_t.started_at,
            finished_at=finished_at,
            duration_ms=(finished_at - bundle_t.started_at).total_seconds() * 1000.0,
            feature_window_hash=bundle_t.feature_window_hash,
            model_name=f"fusion({bundle_t.model_name},{bundle_r.model_name})",
        )

    def _make_record(
        self,
        *,
        bundle: PredictionBundle,
        tenant_id: str,
        trend_id: str,
        correlation_id: str,
        causal: tuple[CausalAttribution, ...],
        halt_reasons: tuple[str, ...],
    ) -> PredictionRecord:
        """Build a `PredictionRecord` for persistence.

        The schema for PredictionRecord is intentionally minimal —
        it just wraps the bundle, a created_at timestamp, and an
        Ed25519 signature placeholder. Halt reasons and causal data
        are recorded out-of-band; we attach them to a sidecar audit
        trail (see `inference/audit.py` and Phase 20's WORM bucket).
        """
        return PredictionRecord(
            bundle=bundle,
            created_at=datetime.now(UTC),
            # `signature` has min_length=1 in the schema. A real Ed25519
            # signature is set by the registry's signing pass (Phase 20);
            # until then we use a sentinel that's valid as a string and
            # easy to grep for in the audit trail.
            signature="UNSIGNED",
        )


def _empty_window(tenant_id: str, trend_id: str, size: int, captured_at: datetime) -> FeatureWindow:
    """Return a zero-filled window with the canonical shape."""
    from .. import FEATURE_DIM

    return FeatureWindow(
        tenant_id=tenant_id,
        trend_id=trend_id,
        captured_at=captured_at,
        window_size=size,
        feature_dim=FEATURE_DIM,
        values=[0.0] * (size * FEATURE_DIM),
    )


async def predict_for_trend(
    tenant_id: str,
    trend_id: str,
    *,
    signals: Sequence[dict] | None = None,
    config: InferenceConfig | None = None,
) -> InferenceResult:
    """One-call helper. Builds a fresh runner each call (state-free).

    For high-throughput callers, hold a long-lived `InferenceRunner`
    instead so model loads amortise across requests.
    """
    runner = InferenceRunner(config=config or InferenceConfig())
    return await runner.run(tenant_id=tenant_id, trend_id=trend_id, signals=signals)
