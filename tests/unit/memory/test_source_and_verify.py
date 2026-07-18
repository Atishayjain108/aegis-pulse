"""Phase C Stage 3 — Source Memory + Reality Verification Layer unit tests.

Query-aware fake pool, no live Postgres. Covers profile building from settled
outcomes + signal recency, the pure evidence/unknowns scorers, and the composed
RealityAssessment (evidence/trust/reality/unknowns + advisory gate).
"""

from __future__ import annotations

from typing import Any

from aegis.memory import (
    OpportunityType,
    RealityVerifier,
    SourceMemory,
    score_evidence,
    score_unknowns,
)
from aegis.memory.source import _freshness


class _Conn:
    def __init__(self, pool: _Pool) -> None:
        self._pool = pool

    async def execute(self, query: str, *args: Any) -> str:
        self._pool.executed.append(" ".join(query.split()))
        return "OK"

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        q = " ".join(query.split())
        if "JOIN LATERAL" in q:
            return self._pool.outcome_rows
        if "n_signals" in q:
            return self._pool.signal_rows
        if "FROM source_profiles" in q:
            return self._pool.profile_rows
        if "FROM opportunities" in q:
            return self._pool.pattern_rows
        return []

    async def fetchrow(self, *_: Any) -> dict[str, Any] | None:
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
        self.outcome_rows: list[dict[str, Any]] = []
        self.signal_rows: list[dict[str, Any]] = []
        self.profile_rows: list[dict[str, Any]] = []
        self.pattern_rows: list[dict[str, Any]] = []

    def acquire(self) -> _Acquire:
        return _Acquire(_Conn(self))

    async def close(self) -> None:
        pass


class _BoomPool:
    def acquire(self) -> Any:
        raise RuntimeError("db down")

    async def close(self) -> None:
        pass


# --------------------------------------------------------------------------- #
# Pure scorers                                                                 #
# --------------------------------------------------------------------------- #
def test_freshness_decay() -> None:
    assert _freshness(0.0) == 1.0
    assert _freshness(15.0) == 0.5
    assert _freshness(40.0) == 0.0


def test_score_evidence_saturates() -> None:
    full = score_evidence(signal_count=100, author_count=100, platform_count=10)
    assert full == 1.0
    thin = score_evidence(signal_count=0, author_count=0, platform_count=0)
    assert thin == 0.0


def test_score_unknowns_counts_present_inputs() -> None:
    full = score_unknowns(
        signal_count=10, author_count=5, platform_count=2,
        source_platforms=["reddit"], prediction_confidence=0.7,
    )
    assert full == 1.0
    half = score_unknowns(
        signal_count=10, author_count=None, platform_count=None,
        source_platforms=None, prediction_confidence=0.7,
    )
    assert half == 0.4


# --------------------------------------------------------------------------- #
# SourceMemory                                                                 #
# --------------------------------------------------------------------------- #
async def test_build_profiles_from_settled_outcomes() -> None:
    pool = _Pool()
    pool.outcome_rows = [
        {"platform": "reddit", "p": 0.6 + 0.02 * i,
         "st": "correct" if i % 2 == 0 else "incorrect"}
        for i in range(8)
    ]
    pool.signal_rows = [
        {"platform": "reddit", "n_signals": 120, "age_days": 1.0},
    ]
    profiles = await SourceMemory(pool).build_profiles(min_outcomes=5)  # type: ignore[arg-type]
    assert len(profiles) == 1
    reddit = profiles[0]
    assert reddit.source_id == "reddit"
    assert reddit.n_outcomes == 8
    assert reddit.reliability_score == 0.5   # 4 of 8 correct
    assert reddit.freshness_score is not None and reddit.freshness_score > 0.9
    # persisted
    assert any("source_profiles" in q for q in pool.executed)


async def test_build_profiles_uses_prior_when_few_outcomes() -> None:
    pool = _Pool()
    pool.outcome_rows = [{"platform": "hn", "p": 0.6, "st": "correct"}]
    pool.signal_rows = [{"platform": "hn", "n_signals": 5, "age_days": 2.0}]
    profiles = await SourceMemory(pool).build_profiles(min_outcomes=5)  # type: ignore[arg-type]
    assert profiles[0].trust == 0.3  # prior: too few outcomes


async def test_build_profiles_swallows_errors() -> None:
    assert await SourceMemory(_BoomPool()).build_profiles() == []  # type: ignore[arg-type]


async def test_trust_for_fills_prior_for_missing() -> None:
    pool = _Pool()
    pool.profile_rows = [{"source_id": "reddit", "trust": 0.72}]
    tmap = await SourceMemory(pool).trust_for(["reddit", "tiktok"])  # type: ignore[arg-type]
    assert tmap["reddit"] == 0.72
    assert tmap["tiktok"] == 0.3
    assert await SourceMemory(pool).trust_for([]) == {}  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# RealityVerifier                                                              #
# --------------------------------------------------------------------------- #
async def test_assess_passes_with_strong_history_and_trust() -> None:
    pool = _Pool()
    pool.pattern_rows = [
        {"opportunity_type": "info_arb", "category": "general",
         "total": 10, "realized": 8, "failed": 2, "avg_confidence": 0.7},
    ]
    pool.profile_rows = [{"source_id": "reddit", "trust": 0.8}]
    a = await RealityVerifier(pool).assess(  # type: ignore[arg-type]
        trend_key="ai", opportunity_type=OpportunityType.INFO_ARB,
        category="general", signal_count=60, author_count=30,
        platform_count=3, source_platforms=["reddit"],
        prediction_confidence=0.7,
    )
    assert a.reality_score == 0.8     # 8/10 realized
    assert a.trust_score == 0.8
    assert a.evidence_score >= 0.4
    assert a.unknowns_score == 1.0
    assert a.passed is True


async def test_source_persist_and_trust_for_swallow_errors() -> None:
    from aegis.memory.schemas import SourceProfile

    boom = _BoomPool()
    assert await SourceMemory(boom)._persist(SourceProfile(source_id="x")) is False  # type: ignore[arg-type]
    # trust_for degrades to priors for every requested id, never raises.
    assert await SourceMemory(boom).trust_for(["x", "y"]) == {"x": 0.3, "y": 0.3}  # type: ignore[arg-type]


async def test_assess_degrades_to_priors_on_db_errors() -> None:
    # Both the history read and the trust read fail → priors, gate not passed.
    a = await RealityVerifier(_BoomPool()).assess(  # type: ignore[arg-type]
        trend_key="ai", signal_count=60, author_count=30,
        platform_count=2, source_platforms=["reddit", "hn"],
        prediction_confidence=0.7,
    )
    assert a.reality_score == 0.5
    assert a.trust_score == 0.3
    assert a.passed is False


async def test_assess_handles_raising_subqueries() -> None:
    # Force the inner reads to RAISE (not just return empty) so the verifier's
    # own defensive except branches are exercised.
    v = RealityVerifier(_Pool())  # type: ignore[arg-type]

    async def _boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("boom")

    v._opps.patterns = _boom        # type: ignore[assignment]
    v._sources.trust_for = _boom    # type: ignore[assignment]
    a = await v.assess(
        trend_key="ai", signal_count=60, author_count=30,
        platform_count=1, source_platforms=["reddit"],
    )
    assert a.reality_score == 0.5
    assert a.trust_score == 0.3


async def test_assess_fails_gate_on_thin_evidence() -> None:
    pool = _Pool()
    pool.pattern_rows = []            # no history → prior reality 0.5
    a = await RealityVerifier(pool).assess(  # type: ignore[arg-type]
        trend_key="ai", signal_count=1, author_count=0, platform_count=0,
    )
    assert a.reality_score == 0.5
    assert a.trust_score == 0.3       # no sources → prior trust
    assert a.passed is False
