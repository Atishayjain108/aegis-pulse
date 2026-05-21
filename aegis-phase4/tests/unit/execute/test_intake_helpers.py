"""Additional intake-worker unit tests.

Covers the pure decode helpers + eviction-after-merge-window logic. Real
Redis isn't required for any of these.
"""

from __future__ import annotations

import time
from uuid import uuid4

from aegis.execute.pipeline import Pipeline
from aegis.execute.workers.intake_worker import (
    MERGE_WINDOW_S,
    IntakeWorker,
    _decode_payload,
)
from tests.integration.execute.conftest import FakeRepository  # type: ignore[import-not-found]


def test_decode_payload_unwraps_body_json():
    raw = {b"body": b'{"trend_id":"x","final_verdict":"HOLD"}'}
    out = _decode_payload(raw)
    assert out == {"trend_id": "x", "final_verdict": "HOLD"}


def test_decode_payload_string_keys_supported():
    raw = {"body": '{"a":1}'}
    out = _decode_payload(raw)
    assert out == {"a": 1}


def test_decode_payload_already_flat():
    raw = {"trend_id": "x", "final_verdict": "HOLD"}
    out = _decode_payload(raw)
    # When no "body" key present, the flat map is returned as-is.
    assert out["trend_id"] == "x"


async def test_intake_evicts_partial_after_merge_window():
    repo = FakeRepository()
    pipe = Pipeline(repository=repo)
    worker = IntakeWorker(pipeline=pipe, tenant_id=uuid4(), redis_client=None)

    # Submit a Phase 2 partial input.
    await worker.submit_phase2_dict({
        "trend_id": "t-evict",
        "final_verdict": "HOLD",
        "final_score": 0.4,
        "final_confidence": 0.6,
        "final_priority": 2,
    })
    # Manipulate the buffered entry's received_at so it's now expired.
    assert "t-evict" in worker._pending  # type: ignore[attr-defined]
    worker._pending["t-evict"].received_at = time.monotonic() - (MERGE_WINDOW_S + 1)  # type: ignore[attr-defined]

    # Submitting a Phase 3 message for a *different* trend should sweep the
    # expired partial out of the buffer.
    await worker.submit_phase3_dict({
        "trend_id": "t-other",
        "bundle": {"predictions": []},
        "policy_action": None,
    })
    # Buffer no longer carries the expired item.
    assert "t-evict" not in worker._pending  # type: ignore[attr-defined]


async def test_intake_start_without_redis_is_noop():
    pipe = Pipeline(repository=FakeRepository())
    worker = IntakeWorker(pipeline=pipe, tenant_id=uuid4(), redis_client=None)
    # `start()` is supposed to return immediately when Redis is absent.
    await worker.start()
    # No background task should be running.
    assert worker._task is None  # type: ignore[attr-defined]
