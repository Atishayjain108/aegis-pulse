"""Tests for `aegis.harden.bridge_phase4`."""

from __future__ import annotations

import json

import pytest

from aegis.harden.bridge_phase4 import publish, to_phase4_payload
from aegis.harden.schemas import HardenVerdict


class _FakeStreamClient:
    def __init__(self, *, raise_on_xadd: bool = False) -> None:
        self.calls: list[tuple[str, dict, dict]] = []
        self._raise = raise_on_xadd
        self._counter = 0

    async def xadd(self, name, fields, **kwargs):  # noqa: D401
        if self._raise:
            raise RuntimeError("redis down")
        self.calls.append((name, dict(fields), dict(kwargs)))
        self._counter += 1
        return f"{name}-{self._counter}".encode()


class TestToPhase4Payload:
    def test_proceed_maps_to_enter(self) -> None:
        v = HardenVerdict(
            trend_id="t1",
            source="honeypot",
            verdict="proceed",
            score=0.1,
            confidence=0.9,
            reason_code="clean",
        )
        p = to_phase4_payload(v)
        doc = json.loads(p["data"])
        assert doc["verdict"] == "ENTER"
        assert doc["trend_id"] == "t1"
        assert doc["phase5_source"] == "honeypot"
        assert p["source"] == "phase5/honeypot"

    def test_warn_maps_to_hold(self) -> None:
        v = HardenVerdict(
            trend_id="t1",
            source="smoothing",
            verdict="warn",
            score=0.5,
            confidence=0.5,
            reason_code="unstable",
        )
        p = to_phase4_payload(v)
        doc = json.loads(p["data"])
        assert doc["verdict"] == "HOLD"

    def test_block_maps_to_block(self) -> None:
        v = HardenVerdict(
            trend_id="t1",
            source="poisoning",
            verdict="block",
            score=0.9,
            confidence=0.95,
            reason_code="poisoning-reject",
        )
        p = to_phase4_payload(v)
        doc = json.loads(p["data"])
        assert doc["verdict"] == "BLOCK"

    def test_trend_id_optional(self) -> None:
        v = HardenVerdict(
            trend_id=None,
            source="honeypot",
            verdict="proceed",
            score=0.1,
            confidence=0.9,
            reason_code="clean",
        )
        p = to_phase4_payload(v)
        assert p["trend_id"] == ""

    def test_doc_is_json_string(self) -> None:
        v = HardenVerdict(
            trend_id="t",
            source="honeypot",
            verdict="proceed",
            score=0.1,
            confidence=0.9,
            reason_code="clean",
        )
        p = to_phase4_payload(v)
        # Must be a valid JSON document
        assert isinstance(p["data"], str)
        assert "trend_id" in p["data"]


class TestPublish:
    async def test_publish_success(self) -> None:
        client = _FakeStreamClient()
        v = HardenVerdict(
            trend_id="t1",
            source="honeypot",
            verdict="proceed",
            score=0.1,
            confidence=0.9,
            reason_code="clean",
        )
        entry_id = await publish(client, "aegis:phase5:verdicts", v)
        assert entry_id is not None
        assert len(client.calls) == 1
        name, fields, kwargs = client.calls[0]
        assert name == "aegis:phase5:verdicts"
        assert kwargs.get("maxlen") == 10_000
        assert kwargs.get("approximate") is True
        doc = json.loads(fields["data"])
        assert doc["verdict"] == "ENTER"

    async def test_publish_swallows_errors(self) -> None:
        client = _FakeStreamClient(raise_on_xadd=True)
        v = HardenVerdict(
            trend_id="t1",
            source="honeypot",
            verdict="proceed",
            score=0.1,
            confidence=0.9,
            reason_code="clean",
        )
        # Must not raise — best-effort publish.
        entry_id = await publish(client, "aegis:phase5:verdicts", v)
        assert entry_id is None
