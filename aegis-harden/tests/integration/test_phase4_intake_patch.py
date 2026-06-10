"""Tests for the Phase 4 intake parser (`integration-patches/02-phase4-intake-phase5_intake.py`).

These exercise the env-flag + envelope-parsing logic without requiring
Phase 4 to be installed.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from aegis.harden.bridge_phase4 import to_phase4_payload
from aegis.harden.schemas import HardenVerdict


def _load_module():
    here = Path(__file__).resolve()
    # here -> .../aegis-phase5/aegis-phase5/tests/integration/<file>.py
    # integration-patches/ lives at .../aegis-phase5/
    module_path = (
        here.parent.parent.parent.parent
        / "integration-patches"
        / "02-phase4-intake-phase5_intake.py"
    )
    spec = importlib.util.spec_from_file_location("phase5_intake_mod", module_path)
    assert spec and spec.loader, f"could not load {module_path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase5_intake_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def m():
    return _load_module()


class TestEnvFlag:
    def test_default_enabled(self, m, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AEGIS_EXECUTE_ENABLE_PHASE5", raising=False)
        assert m.phase5_intake_enabled() is True

    @pytest.mark.parametrize("val", ["true", "TRUE", "1", "yes", "on"])
    def test_truthy_values(self, m, monkeypatch: pytest.MonkeyPatch, val: str) -> None:
        monkeypatch.setenv("AEGIS_EXECUTE_ENABLE_PHASE5", val)
        assert m.phase5_intake_enabled() is True

    @pytest.mark.parametrize("val", ["false", "FALSE", "0", "no", "off", ""])
    def test_falsy_values(self, m, monkeypatch: pytest.MonkeyPatch, val: str) -> None:
        monkeypatch.setenv("AEGIS_EXECUTE_ENABLE_PHASE5", val)
        assert m.phase5_intake_enabled() is False


class TestEnvelopeParsing:
    def _verdict(self, **over) -> HardenVerdict:
        kw = dict(
            trend_id="t-001",
            source="honeypot",
            verdict="block",
            score=0.92,
            confidence=0.85,
            reason_code="trap-suffix",
            detail="url=https://x.com/honeypot reasons=url-token:/honeypot",
        )
        kw.update(over)
        return HardenVerdict(**kw)

    def test_roundtrip_str_keys(self, m) -> None:
        """The envelope written by Phase 5 deserializes correctly with str keys."""
        fields = to_phase4_payload(self._verdict())
        parsed = m.parse_phase5_envelope(fields)
        assert parsed["trend_id"] == "t-001"
        assert parsed["verdict"] == "BLOCK"
        assert parsed["score"] == pytest.approx(0.92)
        assert parsed["confidence"] == pytest.approx(0.85)
        assert parsed["reason_code"] == "trap-suffix"
        assert parsed["phase5_source"] == "honeypot"
        assert parsed["raw_source"] == "phase5/honeypot"
        assert parsed["schema"] == "harden_verdict.v1"

    def test_roundtrip_bytes_keys(self, m) -> None:
        """Redis with `decode_responses=False` gives bytes — must still parse."""
        fields_str = to_phase4_payload(self._verdict())
        fields_bytes = {k.encode(): v.encode() for k, v in fields_str.items()}
        parsed = m.parse_phase5_envelope(fields_bytes)
        assert parsed["trend_id"] == "t-001"
        assert parsed["verdict"] == "BLOCK"

    def test_each_verdict_value_maps(self, m) -> None:
        expected = {"proceed": "ENTER", "warn": "HOLD", "block": "BLOCK"}
        for src_v, dst_v in expected.items():
            fields = to_phase4_payload(self._verdict(verdict=src_v))
            parsed = m.parse_phase5_envelope(fields)
            assert parsed["verdict"] == dst_v

    def test_missing_data_safe_default(self, m) -> None:
        """If `data` is absent or malformed, we still get a safe HOLD verdict."""
        parsed = m.parse_phase5_envelope({"trend_id": "x", "source": "phase5/honeypot"})
        assert parsed["verdict"] == "HOLD"
        assert parsed["score"] == 0.0
        assert parsed["confidence"] == 0.0

    def test_malformed_json_safe(self, m) -> None:
        parsed = m.parse_phase5_envelope(
            {
                "data": "{not json",
                "trend_id": "x",
                "source": "phase5/honeypot",
            }
        )
        assert parsed["verdict"] == "HOLD"
        assert parsed["trend_id"] == "x"

    def test_trend_id_falls_back_to_top_level(self, m) -> None:
        """When the JSON blob lacks trend_id, the top-level field is used."""
        parsed = m.parse_phase5_envelope(
            {
                "data": '{"verdict": "ENTER", "score": 0.5, "confidence": 0.5}',
                "trend_id": "from-top-level",
                "source": "phase5/honeypot",
            }
        )
        assert parsed["trend_id"] == "from-top-level"

    def test_unknown_source_safe(self, m) -> None:
        """A missing `source` doesn't crash; defaults to phase5/unknown."""
        parsed = m.parse_phase5_envelope(
            {
                "data": '{"trend_id": "t", "verdict": "HOLD", "score": 0.1, "confidence": 0.5}',
            }
        )
        assert parsed["raw_source"] == "phase5/unknown"


class TestStreamSourceConstruction:
    def test_attributes(self, m) -> None:
        fake_redis = object()  # not used since we don't call methods
        src = m.Phase5StreamSource(redis_client=fake_redis, ensure_group=False)
        assert src.stream_name == "aegis:phase5:verdicts"
        assert src.consumer_group == "aegis-execute-intake"

    def test_parse_entry_delegates(self, m) -> None:
        """`parse_entry()` is identical to module-level `parse_phase5_envelope()`."""
        fake_redis = object()
        src = m.Phase5StreamSource(redis_client=fake_redis, ensure_group=False)
        fields = {"data": '{"trend_id": "x", "verdict": "HOLD", "score": 0.1, "confidence": 0.5}'}
        assert src.parse_entry(fields) == m.parse_phase5_envelope(fields)


class TestEnsureConsumerGroup:
    async def test_creates_group(self, m) -> None:
        """`ensure_consumer_group()` calls `xgroup_create` with mkstream=True."""
        calls = []

        class FakeRedis:
            async def xgroup_create(self, **kw):
                calls.append(kw)
                return b"OK"

        src = m.Phase5StreamSource(redis_client=FakeRedis(), ensure_group=True)
        await src.ensure_consumer_group()
        assert len(calls) == 1
        assert calls[0]["name"] == "aegis:phase5:verdicts"
        assert calls[0]["groupname"] == "aegis-execute-intake"
        assert calls[0]["mkstream"] is True

    async def test_skipped_when_flag_off(self, m) -> None:
        calls = []

        class FakeRedis:
            async def xgroup_create(self, **kw):
                calls.append(kw)

        src = m.Phase5StreamSource(redis_client=FakeRedis(), ensure_group=False)
        await src.ensure_consumer_group()
        assert calls == []

    async def test_busygroup_is_silent_ok(self, m) -> None:
        """An existing group raises BUSYGROUP — must be swallowed."""

        class FakeRedis:
            async def xgroup_create(self, **kw):
                raise RuntimeError("BUSYGROUP Consumer Group name already exists")

        src = m.Phase5StreamSource(redis_client=FakeRedis(), ensure_group=True)
        # Must not raise.
        await src.ensure_consumer_group()

    async def test_unexpected_error_warns_not_raises(self, m) -> None:
        class FakeRedis:
            async def xgroup_create(self, **kw):
                raise RuntimeError("connection refused")

        src = m.Phase5StreamSource(redis_client=FakeRedis(), ensure_group=True)
        # Must not raise — the worker remains alive even if Redis is down.
        await src.ensure_consumer_group()
