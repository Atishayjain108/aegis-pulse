"""Phase C Stage 4 — Entity + Market + Knowledge Graph + Self-Audit + Backtest.

Query-aware fake pool, no live Postgres. Covers the final knowledge layer:
entity upsert/outcome/trust, market history comparison, graph edge accrual +
discovery, the self-audit report, and the OOS reality backtest.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aegis.memory import (
    EntityKind,
    EntityMemory,
    GraphEdge,
    KnowledgeGraph,
    MarketEpoch,
    MarketMemory,
    Opportunity,
    RealityBacktester,
    RelationType,
    SelfAudit,
)

_NOW = datetime(2026, 6, 1, tzinfo=UTC)


class _Conn:
    def __init__(self, pool: _Pool) -> None:
        self._pool = pool

    async def execute(self, query: str, *args: Any) -> str:
        self._pool.executed.append(" ".join(query.split()))
        return "OK"

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        q = " ".join(query.split())
        if "FROM entities" in q:
            return self._pool.entity_rows
        if "FROM knowledge_edges" in q:
            return self._pool.edge_rows
        if "FROM source_profiles" in q:
            return self._pool.source_rows
        if "FROM opportunities" in q:
            return self._pool.opp_rows
        return []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        q = " ".join(query.split())
        if "SELECT entity_id FROM entities" in q:
            return self._pool.entity_id_row
        if "FROM entities" in q:
            return self._pool.entity_row
        if "FROM market_epochs" in q:
            return self._pool.market_row
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
        self.entity_rows: list[dict[str, Any]] = []
        self.entity_row: dict[str, Any] | None = None
        self.entity_id_row: dict[str, Any] | None = None
        self.edge_rows: list[dict[str, Any]] = []
        self.source_rows: list[dict[str, Any]] = []
        self.opp_rows: list[dict[str, Any]] = []
        self.market_row: dict[str, Any] | None = None

    def acquire(self) -> _Acquire:
        return _Acquire(_Conn(self))

    async def close(self) -> None:
        pass


class _BoomPool:
    def acquire(self) -> Any:
        raise RuntimeError("db down")

    async def close(self) -> None:
        pass


def _entity_row(name: str = "nike", trust: float = 0.6) -> dict[str, Any]:
    return {
        "entity_kind": "brand", "canonical_name": name, "attributes": "{}",
        "trust": trust, "n_observations": 5, "first_seen": _NOW, "last_seen": _NOW,
        "metadata": "{}",
    }


# --------------------------------------------------------------------------- #
# Entity Memory                                                               #
# --------------------------------------------------------------------------- #
async def test_entity_upsert_and_get() -> None:
    pool = _Pool()
    mem = EntityMemory(pool)  # type: ignore[arg-type]
    assert await mem.upsert(EntityKind.BRAND, "Nike", attributes={"x": 1}) is True
    assert any("INSERT INTO entities" in q for q in pool.executed)
    pool.entity_row = _entity_row()
    ent = await mem.get(EntityKind.BRAND, "Nike")
    assert ent is not None
    assert ent.canonical_name == "nike"
    assert ent.entity_kind is EntityKind.BRAND


async def test_entity_get_missing_returns_none() -> None:
    pool = _Pool()
    assert await EntityMemory(pool).get(EntityKind.BRAND, "ghost") is None  # type: ignore[arg-type]


async def test_entity_record_outcome_updates_trust() -> None:
    pool = _Pool()
    pool.entity_id_row = {"entity_id": "e-123"}
    ok = await EntityMemory(pool).record_outcome(  # type: ignore[arg-type]
        EntityKind.BRAND, "Nike", "opp-1", "realized",
    )
    assert ok is True
    assert any("INSERT INTO entity_outcomes" in q for q in pool.executed)
    assert any("UPDATE entities" in q for q in pool.executed)


async def test_entity_record_outcome_unknown_entity() -> None:
    pool = _Pool()
    pool.entity_id_row = None
    ok = await EntityMemory(pool).record_outcome(  # type: ignore[arg-type]
        EntityKind.BRAND, "ghost", "opp-1", "realized",
    )
    assert ok is False


async def test_entity_top_entities() -> None:
    pool = _Pool()
    pool.entity_rows = [_entity_row("nike", 0.8), _entity_row("adidas", 0.6)]
    ents = await EntityMemory(pool).top_entities(EntityKind.BRAND)  # type: ignore[arg-type]
    assert [e.canonical_name for e in ents] == ["nike", "adidas"]


async def test_entity_methods_swallow_errors() -> None:
    mem = EntityMemory(_BoomPool())  # type: ignore[arg-type]
    assert await mem.upsert(EntityKind.BRAND, "x") is False
    assert await mem.record_outcome(EntityKind.BRAND, "x", "o", "realized") is False
    assert await mem.get(EntityKind.BRAND, "x") is None
    assert await mem.top_entities(EntityKind.BRAND) == []


# --------------------------------------------------------------------------- #
# Market Memory                                                               #
# --------------------------------------------------------------------------- #
async def test_market_record_and_compare() -> None:
    pool = _Pool()
    mem = MarketMemory(pool)  # type: ignore[arg-type]
    epoch = MarketEpoch(
        period_start=_NOW, period_end=_NOW, category="apparel", demand_index=0.5,
    )
    assert await mem.record_epoch(epoch) is True
    pool.market_row = {"mean_demand": 0.4, "n": 10}
    cmp = await mem.compare_to_history(
        category="apparel", region=None, current_demand=0.6
    )
    assert cmp["historical_mean"] == 0.4
    assert cmp["delta"] == 0.2
    assert cmp["signal"] == "above_trend"


async def test_market_compare_no_history() -> None:
    pool = _Pool()
    pool.market_row = {"mean_demand": None, "n": 0}
    cmp = await MarketMemory(pool).compare_to_history(  # type: ignore[arg-type]
        category="x", region=None, current_demand=0.5
    )
    assert cmp["signal"] == "no_history"


async def test_market_methods_swallow_errors() -> None:
    mem = MarketMemory(_BoomPool())  # type: ignore[arg-type]
    epoch = MarketEpoch(period_start=_NOW, period_end=_NOW)
    assert await mem.record_epoch(epoch) is False
    cmp = await mem.compare_to_history(category="x", region=None, current_demand=0.5)
    assert cmp["signal"] == "unknown"


# --------------------------------------------------------------------------- #
# Knowledge Graph                                                             #
# --------------------------------------------------------------------------- #
async def test_graph_add_edge_and_relate_opportunity() -> None:
    pool = _Pool()
    g = KnowledgeGraph(pool)  # type: ignore[arg-type]
    edge = GraphEdge(
        src_kind="trend", src_id="ai", dst_kind="opportunity", dst_id="o1",
        relation=RelationType.SIGNAL_OF,
    )
    assert await g.add_edge(edge) is True
    opp = Opportunity(
        trend_key="ai", prediction_id="p1",
        evidence={"source_platforms": ["reddit", "hn"]},
    )
    written = await g.relate_opportunity(opp)
    assert written == 3  # trend→opp + 2 sources


async def test_graph_neighbors_and_discover() -> None:
    pool = _Pool()
    pool.edge_rows = [
        {"src_kind": "trend", "src_id": "ai", "dst_kind": "opportunity",
         "dst_id": "o1", "relation": "signal_of", "weight": 2.0, "evidence_count": 2},
    ]
    g = KnowledgeGraph(pool)  # type: ignore[arg-type]
    nbrs = await g.neighbors("trend", "ai")
    assert nbrs[0].dst_id == "o1"
    disc = await g.discover(RelationType.SIGNAL_OF)
    assert disc[0]["weight"] == 2.0


async def test_graph_methods_swallow_errors() -> None:
    g = KnowledgeGraph(_BoomPool())  # type: ignore[arg-type]
    edge = GraphEdge(src_kind="a", src_id="1", dst_kind="b", dst_id="2",
                     relation=RelationType.SIGNAL_OF)
    assert await g.add_edge(edge) is False
    assert await g.neighbors("a", "1") == []
    assert await g.discover(RelationType.SIGNAL_OF) == []


# --------------------------------------------------------------------------- #
# Self-Audit report                                                           #
# --------------------------------------------------------------------------- #
async def test_self_audit_report() -> None:
    pool = _Pool()
    pool.opp_rows = [
        {"opportunity_type": "info_arb", "category": "general", "total": 10,
         "realized": 7, "failed": 3, "avg_confidence": 0.6},
    ]
    pool.source_rows = [
        {"source_id": "reddit", "trust": 0.7, "reliability_score": 0.6, "n_outcomes": 12},
    ]
    rep = await SelfAudit(pool).weekly_report(days_back=7)  # type: ignore[arg-type]
    assert rep["window_days"] == 7
    assert rep["best_opportunities"][0]["realized_rate"] == 0.7
    assert rep["best_sources"][0]["source_id"] == "reddit"


async def test_self_audit_source_leaderboard_swallows_error() -> None:
    # patterns/failures use OpportunityMemory/FailureMemory (own guards); the
    # direct source query is the one we force to fail here.
    rep = await SelfAudit(_BoomPool()).weekly_report()  # type: ignore[arg-type]
    assert rep["best_sources"] == []


# --------------------------------------------------------------------------- #
# Reality backtest (Rule 12)                                                  #
# --------------------------------------------------------------------------- #
async def test_backtest_separation() -> None:
    pool = _Pool()
    # high-evidence opps realise more than low-evidence ones.
    pool.opp_rows = (
        [{"evidence_score": 0.9, "outcome": "realized"} for _ in range(8)]
        + [{"evidence_score": 0.9, "outcome": "failed"} for _ in range(2)]
        + [{"evidence_score": 0.1, "outcome": "failed"} for _ in range(8)]
        + [{"evidence_score": 0.1, "outcome": "realized"} for _ in range(2)]
        + [{"evidence_score": 0.5, "outcome": "realized"} for _ in range(3)]  # mid bucket
    )
    res = await RealityBacktester(pool).run()  # type: ignore[arg-type]
    assert res["status"] == "ok"
    assert res["buckets"]["high"] == 0.8
    assert res["buckets"]["low"] == 0.2
    assert res["separation"] == 0.6  # higher evidence ⇒ higher realization


async def test_backtest_no_data() -> None:
    res = await RealityBacktester(_Pool()).run()  # type: ignore[arg-type]
    assert res["status"] == "no_data"


async def test_backtest_swallows_error() -> None:
    res = await RealityBacktester(_BoomPool()).run()  # type: ignore[arg-type]
    assert res["status"] == "error"
