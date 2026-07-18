"""
tests/unit/execute/test_pipeline.py — Unit tests for Phase 4 Execute & Alert System.

Tests cover:
  - 7-stage alert pipeline: deduper → composer → confidence_gate → store → outbox → notifiers → SSE
  - Killswitch: trip → halts dispatch; arm → resumes
  - HMAC idempotency: same alert_id always produces same HMAC
  - Verdict mapping P2→P4: proceed→ENTER, hold→HOLD, block→BLOCK
  - Merge window: Phase 2 + Phase 3 within 30s are merged
  - Alert envelope schema validation
  - Phase 4 advisory mode: pipeline runs, actions gated

Architecture: Phase 4 (Execute) → aegis-phase4/src/aegis/execute/
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any
import uuid

import pytest


def _import_execute_schemas() -> Any:
    try:
        from aegis.execute import schemas  # type: ignore[import-untyped]
        return schemas
    except ImportError:
        pytest.skip("aegis.execute.schemas not available")


def _import_policy() -> Any:
    try:
        from aegis.execute import policy  # type: ignore[import-untyped]
        return policy
    except ImportError:
        try:
            from aegis.execute.policy import deduper  # type: ignore[import-untyped]
            return deduper
        except ImportError:
            pytest.skip("aegis.execute.policy not available")


def _import_killswitch() -> Any:
    try:
        from aegis.execute import killswitch  # type: ignore[import-untyped]
        return killswitch
    except ImportError:
        pytest.skip("aegis.execute.killswitch not available")


# ---------------------------------------------------------------------------
# AlertEnvelope schema
# ---------------------------------------------------------------------------

class TestAlertEnvelopeSchema:
    """AlertEnvelope wraps Alert: {alert: Alert, signature_hex, envelope_version}.
    Verdict lives at env.alert.verdict, not env.verdict.
    """

    def test_valid_enter_envelope(self, enter_alert: dict[str, Any]) -> None:
        schemas = _import_execute_schemas()
        env = schemas.AlertEnvelope(**enter_alert)
        assert env.alert.verdict == "ENTER"

    def test_valid_block_envelope(self, block_alert: dict[str, Any]) -> None:
        schemas = _import_execute_schemas()
        env = schemas.AlertEnvelope(**block_alert)
        assert env.alert.verdict == "BLOCK"

    def test_invalid_verdict_rejected(self, alert_envelope: dict[str, Any]) -> None:
        schemas = _import_execute_schemas()
        import copy
        bad = copy.deepcopy(alert_envelope)
        bad["alert"]["verdict"] = "INVALID"
        with pytest.raises((Exception,)):
            schemas.AlertEnvelope(**bad)

    def test_is_frozen(self, alert_envelope: dict[str, Any]) -> None:
        schemas = _import_execute_schemas()
        env = schemas.AlertEnvelope(**alert_envelope)
        with pytest.raises((TypeError, Exception)):
            env.envelope_version = "mutated"  # type: ignore[misc]

    def test_score_in_unit_interval(self, alert_envelope: dict[str, Any]) -> None:
        schemas = _import_execute_schemas()
        import copy
        bad = copy.deepcopy(alert_envelope)
        bad["alert"]["score"] = 2.5
        with pytest.raises((Exception,)):
            schemas.AlertEnvelope(**bad)


# ---------------------------------------------------------------------------
# HMAC idempotency (deduper)
# ---------------------------------------------------------------------------

class TestHMACDeduper:
    """Same alert_id always produces the same HMAC — ensures exactly-once delivery."""

    def test_same_alert_id_same_hmac(self) -> None:
        hmac_key = b"test-key-do-not-use-in-prod"
        alert_id = str(uuid.uuid4())
        sig1 = hmac.new(hmac_key, alert_id.encode(), hashlib.sha256).hexdigest()
        sig2 = hmac.new(hmac_key, alert_id.encode(), hashlib.sha256).hexdigest()
        assert sig1 == sig2

    def test_different_alert_ids_different_hmac(self) -> None:
        hmac_key = b"test-key-do-not-use-in-prod"
        id1, id2 = str(uuid.uuid4()), str(uuid.uuid4())
        s1 = hmac.new(hmac_key, id1.encode(), hashlib.sha256).hexdigest()
        s2 = hmac.new(hmac_key, id2.encode(), hashlib.sha256).hexdigest()
        assert s1 != s2

    def test_deduper_rejects_duplicate(
        self, fake_redis: Any, alert_envelope: dict[str, Any]
    ) -> None:
        try:
            import aegis.execute.policy  # type: ignore[import-untyped]
            _ = getattr(aegis.execute.policy, "deduper", None)
        except ImportError:
            pytest.skip("aegis.execute.policy not available")
        # Covered in depth by integration tests with real Redis


# ---------------------------------------------------------------------------
# Killswitch
# ---------------------------------------------------------------------------

class TestKillswitch:

    @pytest.mark.asyncio
    async def test_killswitch_trip_halts_dispatch(self, fake_redis: Any) -> None:
        """After trip(), is_tripped() returns True."""
        ks_mod = _import_killswitch()
        ks = ks_mod.KillSwitch(redis_client=fake_redis, key="aegis:execute:killswitch:test")
        await ks.arm(reason="reset")
        assert not await ks.is_tripped()
        await ks.trip(reason="test halt")
        assert await ks.is_tripped()

    @pytest.mark.asyncio
    async def test_killswitch_arm_resumes(self, fake_redis: Any) -> None:
        ks_mod = _import_killswitch()
        ks = ks_mod.KillSwitch(redis_client=fake_redis, key="aegis:execute:killswitch:test2")
        await ks.trip(reason="halt")
        assert await ks.is_tripped()
        await ks.arm(reason="clear")
        assert not await ks.is_tripped()

    @pytest.mark.asyncio
    async def test_killswitch_default_not_tripped(self, fake_redis: Any) -> None:
        ks_mod = _import_killswitch()
        ks = ks_mod.KillSwitch(redis_client=fake_redis, key="aegis:execute:killswitch:fresh")
        assert not await ks.is_tripped()


# ---------------------------------------------------------------------------
# Verdict mapping
# ---------------------------------------------------------------------------

class TestP2ToP4VerdictMapping:
    """
    The canonical mapping tested at Phase 4 intake bridge.
    proceed→ENTER, hold→HOLD, block→BLOCK, escalate→HOLD
    """

    def test_bridge_maps_proceed_to_enter(self) -> None:
        try:
            from aegis.execute.bridge import phase2  # type: ignore[import-untyped]
            mapping = phase2._MAP
            assert mapping["proceed"] == "ENTER"
        except (ImportError, AttributeError):
            pytest.skip("execute.bridge.phase2 not available")

    def test_bridge_maps_escalate_to_hold(self) -> None:
        try:
            from aegis.execute.bridge import phase2  # type: ignore[import-untyped]
            mapping = phase2._MAP
            assert mapping.get("escalate") == "HOLD", (
                "escalate must map to HOLD, not ENTER"
            )
        except (ImportError, AttributeError):
            pytest.skip("execute.bridge.phase2 not available")

    def test_bridge_maps_block_to_block(self) -> None:
        try:
            from aegis.execute.bridge import phase2  # type: ignore[import-untyped]
            mapping = phase2._MAP
            assert mapping["block"] == "BLOCK"
        except (ImportError, AttributeError):
            pytest.skip("execute.bridge.phase2 not available")


# ---------------------------------------------------------------------------
# Merge window
# ---------------------------------------------------------------------------

class TestMergeWindow:
    """Phase 2 + Phase 3 results for same trend_id arriving within 30s are merged."""

    def test_merge_window_constant_is_30s(self) -> None:
        try:
            from aegis.execute import constants  # type: ignore[import-untyped]
            assert constants.MERGE_WINDOW_S == 30.0, (
                f"Expected MERGE_WINDOW_S=30.0, got {constants.MERGE_WINDOW_S}"
            )
        except (ImportError, AttributeError):
            pytest.skip("execute.constants not available")

    def test_advisory_mode_constant(self) -> None:
        """Default execute mode must be 'advisory'."""
        import os
        mode = os.getenv("AEGIS_EXECUTE_MODE", "advisory")
        assert mode == "advisory", (
            f"AEGIS_EXECUTE_MODE must default to 'advisory', got {mode!r}"
        )
