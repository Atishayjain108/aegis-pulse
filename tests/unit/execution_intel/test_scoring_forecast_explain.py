"""PROJECT OMEGA Phase D — S5: Risk Engine + Failure Forecaster + Explainability.

Query-aware fake pool. Covers the six-axis ExecutionScoreVector (Rule 6:
metrics never merged, no composite field), the stored→measured forecast loop
(Rule 8), and the six Rule 9 explanation answers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from aegis.execution_intel import (
    ExecutionExplanation,
    ExecutionScoreVector,
    FailureForecast,
    FailureForecaster,
    ForecastAccuracy,
    ScoreVectorBuilder,
    SurvivabilityScore,
    explain_plan,
)

_NOW = datetime(2026, 6, 17, tzinfo=UTC)


class _Conn:
    def __init__(self, pool: _Pool) -> None:
        self._pool = pool

    async def execute(self, query: str, *args: Any) -> str:
        self._pool.executed.append(" ".join(query.split()))
        return "OK"

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        q = " ".join(query.split())
        if "GROUP BY failure_category" in q:
            return self._pool.failure_rows
        if "FROM execution_forecasts f" in q:
            return self._pool.forecast_join_rows
        return []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        q = " ".join(query.split())
        if "FROM supplier_reliability" in q:
            return self._pool.supplier_row
        if "FROM buyer_demand" in q:
            return self._pool.buyer_row
        if "n_settled" in q and "n_ok" in q:
            return self._pool.success_row
        if "COUNT(*)::int AS n FROM execution_records" in q:
            return self._pool.settled_row
        return None


class _Acquire:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _Conn:
        return self._conn

    async def __aexit__(self, *_: Any) -> None:
        pass


class _Pool:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.supplier_row: dict[str, Any] | None = None
        self.buyer_row: dict[str, Any] | None = None
        self.success_row: dict[str, Any] | None = None
        self.settled_row: dict[str, Any] | None = {"n": 0}
        self.failure_rows: list[dict[str, Any]] = []
        self.forecast_join_rows: list[dict[str, Any]] = []

    def acquire(self) -> _Acquire:
        return _Acquire(_Conn(self))


def _supplier_row(**kw: Any) -> dict[str, Any]:
    base = {
        "supplier_name": "printful",
        "n_verifications": 5, "n_verified": 5,
        "n_fulfillments": 10, "n_fulfilled_ok": 9,
        "n_delays": 1, "n_cancellations": 0,
        "total_response_ms": 500, "n_responses": 5,
        "updated_at": _NOW,
    }
    base.update(kw)
    return base


# --------------------------------------------------------------------------- #
# Rule 6 invariant — no composite / merged field
# --------------------------------------------------------------------------- #


def test_score_vector_has_no_composite_field() -> None:
    fields = set(ExecutionScoreVector.model_fields)
    # The six axes plus bookkeeping only — never a merged/overall/composite.
    for banned in ("overall", "composite", "blended", "total", "combined", "score"):
        assert banned not in fields, f"Rule 6 violated: '{banned}' merges metrics"
    for axis in ("risk", "confidence", "trust", "evidence", "execution", "survivability"):
        assert axis in fields


def test_score_vector_counts_verified_metrics() -> None:
    v = ExecutionScoreVector(plan_id="p", risk=0.2, trust=0.9)
    assert v.verified_metrics == 2


# --------------------------------------------------------------------------- #
# ScoreVectorBuilder
# --------------------------------------------------------------------------- #


async def test_build_vector_mixed_measured_and_unverified() -> None:
    pool = _Pool()
    pool.supplier_row = _supplier_row()      # trust measured
    pool.success_row = {"n_settled": 4, "n_ok": 3}  # execution measured
    builder = ScoreVectorBuilder(pool)  # type: ignore[arg-type]
    v = await builder.build(
        "plan-1", supplier_name="printful", risk=0.3, confidence=0.7,
        compliance_risk=0.0,
    )
    assert v.trust == pytest.approx(0.9)
    assert v.execution == pytest.approx(0.75)
    assert v.risk == 0.3
    assert v.confidence == 0.7
    assert v.evidence is None             # not supplied → UNVERIFIED
    assert v.source["evidence"] == "unverified"
    assert v.source["trust"] == "measured"
    assert v.survivability is not None    # supplier measured → simulator scores


async def test_build_vector_all_unverified_when_empty() -> None:
    pool = _Pool()
    builder = ScoreVectorBuilder(pool)  # type: ignore[arg-type]
    v = await builder.build("plan-1")
    assert v.verified_metrics == 0
    assert v.survivability is None        # simulator abstains w/o supplier


# --------------------------------------------------------------------------- #
# FailureForecaster (Rule 8 stored→measured loop)
# --------------------------------------------------------------------------- #


async def test_forecast_abstains_without_supplier() -> None:
    pool = _Pool()
    f = FailureForecaster(pool)  # type: ignore[arg-type]
    fc = await f.forecast("plan-1")
    assert isinstance(fc, FailureForecast)
    assert fc.abstained is True
    assert fc.probabilities == {}


async def test_forecast_inverts_survival() -> None:
    pool = _Pool()
    pool.supplier_row = _supplier_row()   # trust 0.9 → supplier failure prob 0.1
    f = FailureForecaster(pool)  # type: ignore[arg-type]
    fc = await f.forecast("plan-1", supplier_name="printful")
    assert fc.abstained is False
    assert fc.probabilities["supplier"] == pytest.approx(0.1)
    # assumed modes survive 0.9 → fail 0.1
    assert fc.probabilities["inventory"] == pytest.approx(0.1)


async def test_record_forecast_persists() -> None:
    pool = _Pool()
    f = FailureForecaster(pool)  # type: ignore[arg-type]
    ok = await f.record_forecast(
        FailureForecast(plan_id="plan-1", probabilities={"supplier": 0.2})
    )
    assert ok is True
    assert any("INSERT INTO execution_forecasts" in q for q in pool.executed)


async def test_p_any_failure_independence() -> None:
    p = FailureForecaster._p_any_failure({"a": 0.1, "b": 0.1})
    # 1 - 0.9*0.9 = 0.19
    assert p == pytest.approx(0.19)


async def test_measure_accuracy_empty() -> None:
    pool = _Pool()
    f = FailureForecaster(pool)  # type: ignore[arg-type]
    acc = await f.measure_accuracy()
    assert isinstance(acc, ForecastAccuracy)
    assert acc.n == 0
    assert acc.brier_skill is None


async def test_measure_accuracy_computes_brier_skill() -> None:
    pool = _Pool()
    # 4 settled forecasts; forecaster predicts failures well.
    pool.forecast_join_rows = [
        {"p": 0.9, "failed": True},
        {"p": 0.8, "failed": True},
        {"p": 0.1, "failed": False},
        {"p": 0.2, "failed": False},
    ]
    f = FailureForecaster(pool)  # type: ignore[arg-type]
    acc = await f.measure_accuracy()
    assert acc.n == 4
    assert acc.base_rate == pytest.approx(0.5)
    assert acc.brier is not None
    assert acc.brier_skill is not None
    assert acc.brier_skill > 0     # beats the base-rate predictor


# --------------------------------------------------------------------------- #
# Explainability (Rule 9)
# --------------------------------------------------------------------------- #


def test_explain_answers_all_six_questions() -> None:
    v = ExecutionScoreVector(
        plan_id="plan-1", risk=0.3, confidence=0.7, trust=0.9,
        evidence=0.6, execution=0.75, survivability=0.65,
    )
    surv = SurvivabilityScore(
        plan_id="plan-1", overall=0.65, abstained=False,
        mode_survival={"demand": 0.9}, basis={"supplier": "measured"},
    )
    e = explain_plan(v, surv, supplier_name="printful", region="US", category="apparel")
    assert isinstance(e, ExecutionExplanation)
    for ans in (
        e.why_now, e.why_this_opportunity, e.why_this_supplier,
        e.why_this_buyer, e.why_this_region, e.why_this_risk_level,
    ):
        assert ans  # non-empty
    # Buyer is structurally UNVERIFIED, region cited, risk axes kept separate.
    assert "UNVERIFIED" in e.why_this_buyer
    assert "US" in e.why_this_region
    assert "printful" in e.why_this_supplier


def test_explain_honest_when_unverified() -> None:
    v = ExecutionScoreVector(plan_id="plan-1")  # everything None
    surv = SurvivabilityScore(
        plan_id="plan-1", overall=None, abstained=True,
        reason="supplier reliability is UNVERIFIED",
    )
    e = explain_plan(v, surv)
    assert "UNVERIFIED" in e.why_now
    assert "UNVERIFIED" in e.why_this_supplier
    assert "UNVERIFIED" in e.why_this_risk_level
