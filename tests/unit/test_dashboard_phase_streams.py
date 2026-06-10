"""CONN-1 / DASH-2: dashboard phase-7/8/9 event-bus consumer endpoints."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from aegis.dashboard.app import app


def _entries(*payloads: dict) -> list[tuple[str, dict]]:
    return [(f"{i}-0", {"body": json.dumps(p)}) for i, p in enumerate(payloads)]


async def _call(endpoint: str, mock_r) -> list[dict]:
    with patch("aegis.dashboard.app._get_redis", return_value=mock_r):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(endpoint)
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.parametrize(
    ("endpoint", "stream"),
    [
        ("/api/geo/recent", "aegis:phase7:geo_opportunities"),
        ("/api/compliance/recent", "aegis:phase8:compliance_assessments"),
        ("/api/evolve/recent", "aegis:phase9:evolve_events"),
    ],
)
async def test_phase_recent_returns_parsed_entries(endpoint, stream):
    mock_r = AsyncMock()
    mock_r.xrevrange = AsyncMock(return_value=_entries({"k": "v1"}, {"k": "v2"}))
    data = await _call(endpoint, mock_r)
    assert len(data) == 2
    assert data[0]["k"] == "v1"
    assert data[0]["stream_id"] == "0-0"
    mock_r.xrevrange.assert_awaited_once()
    assert mock_r.xrevrange.await_args.args[0] == stream


async def test_phase_recent_redis_error_returns_empty():
    mock_r = AsyncMock()
    mock_r.xrevrange = AsyncMock(side_effect=RuntimeError("redis down"))
    data = await _call("/api/geo/recent", mock_r)
    assert data == []


async def test_phase_recent_skips_malformed_entries():
    mock_r = AsyncMock()
    mock_r.xrevrange = AsyncMock(return_value=[("0-0", {"body": "not-json"}), ("1-0", {"body": json.dumps({"ok": 1})})])
    data = await _call("/api/compliance/recent", mock_r)
    assert data == [{"ok": 1, "stream_id": "1-0"}]


async def test_phase_recent_limit_capped_at_100():
    mock_r = AsyncMock()
    mock_r.xrevrange = AsyncMock(return_value=[])
    await _call("/api/evolve/recent?limit=9999", mock_r)
    assert mock_r.xrevrange.await_args.kwargs["count"] == 100
