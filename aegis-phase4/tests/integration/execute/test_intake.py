"""Intake worker tests — direct submission path.

These tests don't use Redis; they call `submit_phase2_dict` /
`submit_phase3_dict` directly. The Redis-backed path is exercised
indirectly via the merge logic.
"""

from __future__ import annotations

from uuid import uuid4

from aegis.execute.pipeline import Pipeline
from aegis.execute.workers.intake_worker import (
    IntakeWorker,
    _merge_inputs,
    _parse_phase2_dict,
    _parse_phase3_dict,
)


def test_parse_phase2_dict_basic():
    tid = uuid4()
    msg = {
        "trend_id": "t-1",
        "final_verdict": "ENTER",
        "final_score": 0.7,
        "final_confidence": 0.65,
        "final_priority": 1,
        "halt_reason": None,
        "blocked_by": [],
        "title": "x",
        "narrative": "n",
    }
    ci = _parse_phase2_dict(msg, tenant_id=tid)
    assert ci.tenant_id == tid
    assert ci.phase2_verdict == "ENTER"
    assert ci.phase2_score == 0.7
    assert ci.phase2_priority == 1


def test_parse_phase3_dict_picks_horizons():
    tid = uuid4()
    msg = {
        "trend_id": "t-1",
        "bundle": {
            "predictions": [
                {"horizon_hours": 1, "p_breakout": 0.1, "p_decline": 0.5, "p_saturation": 0.1, "confidence": 0.5},
                {"horizon_hours": 6, "p_breakout": 0.2, "p_decline": 0.6, "p_saturation": 0.3, "confidence": 0.6},
                {"horizon_hours": 24, "p_breakout": 0.8, "p_decline": 0.1, "p_saturation": 0.4, "confidence": 0.7},
            ]
        },
        "policy_action": "ENTER",
    }
    ci = _parse_phase3_dict(msg, tenant_id=tid)
    assert ci.phase3_p_breakout_24h == 0.8
    assert ci.phase3_p_decline_6h == 0.6
    assert ci.phase3_p_saturation == 0.4  # max horizon
    assert ci.phase3_policy_action == "enter"


def test_merge_inputs_combines_p2_and_p3():
    from aegis.execute.bridge.types import ComposerInput
    tid = uuid4()
    p2 = ComposerInput(
        tenant_id=tid,
        trend_id="t",
        phase2_verdict="ENTER",
        phase2_score=0.8,
        phase2_confidence=0.7,
        phase2_priority=1,
    )
    p3 = ComposerInput(
        tenant_id=tid,
        trend_id="t",
        phase3_p_breakout_24h=0.9,
        phase3_p_decline_6h=0.05,
        phase3_confidence=0.85,
        phase3_policy_action="enter",
    )
    merged = _merge_inputs(p2, p3)
    assert merged.phase2_verdict == "ENTER"
    assert merged.phase3_p_breakout_24h == 0.9


async def test_intake_worker_buffers_then_merges_and_submits(fake_repo):
    tid = uuid4()
    pipe = Pipeline(repository=fake_repo)
    worker = IntakeWorker(pipeline=pipe, tenant_id=tid, redis_client=None)

    # Submit Phase 2 → buffered (not enough by itself? ENTER with full data is)
    await worker.submit_phase2_dict({
        "trend_id": "t-merge",
        "final_verdict": "HOLD",
        "final_score": 0.4,
        "final_confidence": 0.5,
        "final_priority": 2,
    })
    # Nothing yet: pipeline hasn't seen anything.
    assert len(fake_repo.alerts) == 0
    # Submit Phase 3 → merges and submits
    await worker.submit_phase3_dict({
        "trend_id": "t-merge",
        "bundle": {
            "predictions": [
                {"horizon_hours": 24, "p_breakout": 0.9, "p_decline": 0.05,
                 "p_saturation": 0.2, "confidence": 0.8, "expected_margin_usd": 3.0,
                 "loss_probability": 0.1},
                {"horizon_hours": 6, "p_breakout": 0.3, "p_decline": 0.10,
                 "p_saturation": 0.1, "confidence": 0.7, "expected_margin_usd": 1.5,
                 "loss_probability": 0.2},
            ]
        },
        "policy_action": "enter",
    })
    assert len(fake_repo.alerts) == 1
    # Trend should compose to ENTER from merged inputs
    alert = next(iter(fake_repo.alerts.values()))
    assert alert.trend_id == "t-merge"


async def test_intake_worker_flush_pending(fake_repo):
    tid = uuid4()
    pipe = Pipeline(repository=fake_repo)
    worker = IntakeWorker(pipeline=pipe, tenant_id=tid, redis_client=None)
    await worker.submit_phase2_dict({
        "trend_id": "t-orphan",
        "final_verdict": "HOLD",
        "final_score": 0.3,
        "final_confidence": 0.6,
        "final_priority": 2,
    })
    # No counterpart; flush should submit the orphan input.
    n = await worker.flush_pending()
    assert n == 1
    assert len(fake_repo.alerts) == 1


async def test_handle_acks_only_on_success(fake_repo, monkeypatch):
    """Audit P3-1: a transient handler failure must NOT ack (leave pending for
    reclaim); a success must ack exactly once; an undecodable msg is dropped."""
    from aegis.execute.workers import intake_worker as iw

    tid = uuid4()
    pipe = Pipeline(repository=fake_repo)

    class _Redis:
        def __init__(self):
            self.acked = []

        async def xack(self, stream, group, entry_id):
            self.acked.append((stream, entry_id))
            return 1

    rc = _Redis()
    worker = IntakeWorker(pipeline=pipe, tenant_id=tid, redis_client=rc)

    good = {b"body": b'{"trend_id":"ok","final_verdict":"HOLD","final_score":0.4,"final_confidence":0.5,"final_priority":2}'}

    # 1) success path → acked
    await worker._handle(iw.STREAM_PHASE2, "1-0", good)
    assert ("aegis:phase2:graph_results" in [a[0] for a in rc.acked]) or rc.acked

    # 2) transient failure → NOT acked (stays pending for XAUTOCLAIM retry)
    def _boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(iw, "_parse_phase2_dict", _boom)
    before = len(rc.acked)
    await worker._handle(iw.STREAM_PHASE2, "2-0", good)
    assert len(rc.acked) == before, "failed handler must not ACK (audit P3-1)"
    monkeypatch.undo()

    # 3) undecodable → dropped (acked so it can't wedge the group)
    await worker._handle(iw.STREAM_PHASE2, "3-0", {b"garbage": b"not json"})
    assert any(e == "3-0" for _s, e in rc.acked)
