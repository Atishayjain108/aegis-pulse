"""aegis.execution_intel.schemas — frozen Pydantic v2 models for Phase D."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aegis.execution_intel.taxonomy import (
    AssumptionKind,
    AssumptionStatus,
    ExecutionFailureCategory,
    ExecutionOutcome,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class ExecutionAssumption(BaseModel):
    """One thing a recommendation rests on, with an explicit status (Rule 1).

    ``status`` defaults to ``UNVERIFIED`` — a recommendation never treats an
    assumption as true until evidence promotes it. ``verified_via`` names the
    real check (e.g. ``"printful_api"``) when ``status == VERIFIED``.
    """

    model_config = ConfigDict(frozen=True)

    assumption_id: str = Field(default_factory=_new_uuid)
    plan_id: str
    kind: AssumptionKind
    claim: str = ""
    status: AssumptionStatus = AssumptionStatus.UNVERIFIED
    evidence: dict[str, Any] = Field(default_factory=dict)
    verified_via: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class ExecutionRecord(BaseModel):
    """A durable plan → outcome → cost → failure record (Rule 2).

    Created when a plan is recommended; outcome/pnl/cost fields stay ``None``
    until a real settled order fills them via ``ExecutionMemory.record_outcome``.
    """

    model_config = ConfigDict(frozen=True)

    record_id: str = Field(default_factory=_new_uuid)
    plan_id: str
    trend_id: str = "unknown"
    opportunity_type: str = "product"
    region: str | None = None
    supplier_name: str | None = None

    planned_units: int = 0
    planned_unit_cost_usd: float | None = None
    planned_margin_pct: float | None = None

    outcome: ExecutionOutcome = ExecutionOutcome.PENDING
    realized_units: int | None = None
    realized_pnl_usd: float | None = None
    realized_cost_usd: float | None = None
    delay_hours: float | None = None
    failure_category: ExecutionFailureCategory = ExecutionFailureCategory.NONE
    failure_detail: str = ""

    created_at: datetime = Field(default_factory=_utcnow)
    settled_at: datetime | None = None
    settlement_timestamp: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SupplierTrustScore(BaseModel):
    """Derived supplier reliability (Rule 3). All rates are measured, not asserted.

    ``trust`` is ``None`` (UNVERIFIED) when there is no settled fulfillment
    history — we never guess a supplier's reliability. ``verification_rate`` is
    available earlier (after any real verify call) and reported separately.
    """

    model_config = ConfigDict(frozen=True)

    supplier_name: str
    trust: float | None = None              # fulfillment success rate, [0,1] or None
    verification_rate: float | None = None  # verify success rate, [0,1] or None
    delay_rate: float | None = None
    cancellation_rate: float | None = None
    avg_response_ms: float | None = None
    n_verifications: int = 0
    n_fulfillments: int = 0
    updated_at: datetime = Field(default_factory=_utcnow)

    @property
    def is_verified(self) -> bool:
        """True when at least one real fulfillment backs the trust score."""
        return self.n_fulfillments > 0


class BuyerDemandProxy(BaseModel):
    """Demand intelligence as an honest PROXY (Rule 4 + Rule 1).

    ``demand_intensity`` is derived from real signal velocity/engagement — it is
    an *observed proxy*, not verified purchase demand. ``buyer_trust`` is always
    ``None`` (UNVERIFIED) until real order data exists (``n_orders > 0``); we do
    not score buyers we have never transacted with.
    """

    model_config = ConfigDict(frozen=True)

    region: str = "GLOBAL"
    category: str = "general"
    demand_intensity: float | None = None      # proxy, [0,1] or None
    buyer_trust: float | None = None            # fulfillment rate, or None=UNVERIFIED
    n_demand_observations: int = 0
    n_orders: int = 0
    n_fulfilled: int = 0
    updated_at: datetime = Field(default_factory=_utcnow)

    @property
    def demand_is_proxy(self) -> bool:
        """Always True: demand here is a signal-derived proxy, never verified."""
        return True

    @property
    def buyer_is_verified(self) -> bool:
        """True only when at least one real order backs the buyer trust score."""
        return self.n_orders > 0


class SurvivabilityScore(BaseModel):
    """Execution Survivability Score (Rule 5). Deterministic, not Monte Carlo.

    ``overall`` is the product of per-mode survival probabilities. It is ``None``
    (abstained / UNVERIFIED) when a critical input — currently supplier
    reliability — has no measured basis. ``mode_survival`` maps each failure mode
    to its survival probability in [0,1]. ``basis`` records which inputs were
    measured vs assumed, for explainability (Rule 9).
    """

    model_config = ConfigDict(frozen=True)

    plan_id: str
    overall: float | None = None
    abstained: bool = False
    reason: str = ""
    mode_survival: dict[str, float] = Field(default_factory=dict)
    basis: dict[str, str] = Field(default_factory=dict)  # mode -> "measured"|"assumed"|"unverified"
    assessed_at: datetime = Field(default_factory=_utcnow)

    @property
    def is_verified(self) -> bool:
        """True when the overall score rests on at least one measured input."""
        return self.overall is not None and "measured" in self.basis.values()


class ExecutionScoreVector(BaseModel):
    """Six SEPARATED execution metrics (Rule 6). They are NEVER merged.

    Each metric answers a different question and is reported independently:
      * ``risk``         — how dangerous is this plan? (higher = riskier)
      * ``confidence``   — how sure is the predictor of its directional call?
      * ``trust``        — how reliable is the supplier we depend on?
      * ``evidence``     — how strong is the underlying signal evidence?
      * ``execution``    — historical success rate of similar settled plans
      * ``survivability``— deterministic simulated survival probability

    There is deliberately NO composite/blended field. ``None`` on any metric
    means UNVERIFIED — it is never substituted with a default (Rule 1). The
    ``source`` map records, per metric, whether it was ``measured`` or is
    ``unverified`` for explainability (Rule 9).
    """

    model_config = ConfigDict(frozen=True)

    plan_id: str
    risk: float | None = None
    confidence: float | None = None
    trust: float | None = None
    evidence: float | None = None
    execution: float | None = None
    survivability: float | None = None
    source: dict[str, str] = Field(default_factory=dict)
    assessed_at: datetime = Field(default_factory=_utcnow)

    @property
    def verified_metrics(self) -> int:
        """Count of the six metrics backed by a measured (non-None) value."""
        return sum(
            v is not None
            for v in (
                self.risk, self.confidence, self.trust,
                self.evidence, self.execution, self.survivability,
            )
        )


class FailureForecast(BaseModel):
    """Predicted per-mode failure probabilities for a plan (Rule 8).

    Stored at recommendation time so it can later be scored against the settled
    outcome. ``probabilities`` maps each failure mode to P(failure) in [0,1].
    ``abstained`` is True when the forecast could not be grounded (e.g. supplier
    unverified) — an abstention is recorded honestly, not a guessed 0.5.
    """

    model_config = ConfigDict(frozen=True)

    forecast_id: str = Field(default_factory=_new_uuid)
    plan_id: str
    probabilities: dict[str, float] = Field(default_factory=dict)
    abstained: bool = False
    created_at: datetime = Field(default_factory=_utcnow)


class ForecastAccuracy(BaseModel):
    """Measured quality of stored forecasts vs settled outcomes (Rule 8).

    Computed over forecasts whose plan has since settled. ``brier`` is the mean
    squared error of P(any failure) vs observed failure (0/1). ``base_rate`` is
    the observed failure rate; ``brier_skill`` > 0 means the forecaster beats
    always predicting the base rate. ``n`` is the sample size.
    """

    model_config = ConfigDict(frozen=True)

    n: int = 0
    base_rate: float | None = None
    brier: float | None = None
    brier_skill: float | None = None


class ArbitrageRationale(BaseModel):
    """The three arbitrage questions for an opportunity (Rule 7).

    These are ADVISORY hypotheses, not verified facts — each is phrased as a
    question-answer and flagged ``verified=False`` until evidence backs it.
    """

    model_config = ConfigDict(frozen=True)

    opportunity_type: str = "product"
    why_exists: str = ""
    why_not_captured: str = ""
    what_destroys_it: str = ""
    verified: bool = False


class ExecutionAudit(BaseModel):
    """Weekly self-audit over execution memory (Rule 10).

    Every list is derived from real settled records / measured reliability —
    empty when there is no data yet (no fabricated entries).
    """

    model_config = ConfigDict(frozen=True)

    period_days: int = 7
    n_settled: int = 0
    best_plans: list[dict[str, Any]] = Field(default_factory=list)
    worst_plans: list[dict[str, Any]] = Field(default_factory=list)
    most_reliable_suppliers: list[dict[str, Any]] = Field(default_factory=list)
    most_demanded_markets: list[dict[str, Any]] = Field(default_factory=list)
    most_profitable: list[dict[str, Any]] = Field(default_factory=list)
    common_failure_causes: list[dict[str, Any]] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=_utcnow)


class ExecutionExplanation(BaseModel):
    """Plain-language answers to the six Rule 9 questions for one plan.

    Each answer cites measured vs unverified inputs — it never asserts a reason
    that is not backed by the score vector / memories.
    """

    model_config = ConfigDict(frozen=True)

    plan_id: str
    why_now: str = ""
    why_this_opportunity: str = ""
    why_this_supplier: str = ""
    why_this_buyer: str = ""
    why_this_region: str = ""
    why_this_risk_level: str = ""
    generated_at: datetime = Field(default_factory=_utcnow)
