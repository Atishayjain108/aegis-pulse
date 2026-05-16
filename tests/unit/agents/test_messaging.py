"""
HMAC signing roundtrip and message TTL tests.

We don't unit-test the Redis Streams bus here — that requires either
fakeredis or a live broker, both of which add brittleness. The
critical correctness properties of the messaging layer (signature
integrity + freshness via TTL) are pure functions and fully
testable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aegis.agents.messaging.hmac_sign import (
    _INSECURE_DEFAULT_KEY,
    get_signing_key,
    sign,
    verify,
)
from aegis.agents.schemas import AgentMessage, Priority


def _msg(**overrides) -> AgentMessage:
    defaults = {
        "correlation_id": "c-test-1",
        "from_agent": "scout",
        "to_agent": "auditor",
        "priority": Priority.P2_OPPORTUNITY,
        "ttl_seconds": 300,
    }
    defaults.update(overrides)
    return AgentMessage(**defaults)


class TestSignVerifyRoundtrip:
    def test_sign_then_verify_returns_true(self) -> None:
        msg = _msg()
        signed = sign(msg)
        assert signed.hmac_signature
        assert verify(signed) is True

    def test_unsigned_message_fails_verify(self) -> None:
        msg = _msg()
        # Pristine message has no signature.
        assert msg.hmac_signature is None
        assert verify(msg) is False

    def test_tamper_payload_fails_verify(self) -> None:
        msg = _msg(payload={"score": 0.5})
        signed = sign(msg)
        # Mutate one field while keeping the old signature → mismatch.
        tampered = signed.model_copy(update={"payload": {"score": 0.99}})
        assert verify(tampered) is False

    def test_tamper_priority_fails_verify(self) -> None:
        msg = _msg(priority=Priority.P2_OPPORTUNITY)
        signed = sign(msg)
        tampered = signed.model_copy(update={"priority": Priority.P0_BREAKOUT})
        assert verify(tampered) is False

    def test_payload_modification_fails_verify(self) -> None:
        msg = _msg(payload={"score": 0.5, "agent": "scout"})
        signed = sign(msg)
        tampered = signed.model_copy(update={"payload": {"score": 0.5, "agent": "evil"}})
        assert verify(tampered) is False

    def test_explicit_key_overrides_env(self) -> None:
        key_a = b"key-a-32-bytes-long-padding-here"
        key_b = b"key-b-32-bytes-long-padding-here"
        msg = _msg()
        signed_a = sign(msg, key=key_a)
        # Same key → verifies.
        assert verify(signed_a, key=key_a) is True
        # Different key → fails.
        assert verify(signed_a, key=key_b) is False

    def test_signature_is_hex_string(self) -> None:
        signed = sign(_msg())
        assert signed.hmac_signature is not None
        # SHA-256 hex digest = 64 chars.
        assert len(signed.hmac_signature) == 64
        int(signed.hmac_signature, 16)  # valid hex


class TestSigningKeyResolution:
    def test_uses_env_when_set(self, monkeypatch) -> None:
        monkeypatch.setenv("AEGIS_AGENT_HMAC_KEY", "production-secret")
        key, secure = get_signing_key()
        assert key == b"production-secret"
        assert secure is True

    def test_falls_back_to_insecure_default(self, monkeypatch) -> None:
        monkeypatch.delenv("AEGIS_AGENT_HMAC_KEY", raising=False)
        key, secure = get_signing_key()
        assert key == _INSECURE_DEFAULT_KEY
        assert secure is False

    def test_empty_env_falls_back(self, monkeypatch) -> None:
        monkeypatch.setenv("AEGIS_AGENT_HMAC_KEY", "   ")  # whitespace
        key, secure = get_signing_key()
        assert secure is False


class TestAgentMessageTTL:
    def test_fresh_message_not_expired(self) -> None:
        msg = _msg(ttl_seconds=60)
        assert msg.is_expired() is False

    def test_expired_message_detected(self) -> None:
        old_ts = datetime.now(tz=UTC) - timedelta(seconds=120)
        msg = _msg(ttl_seconds=60, timestamp=old_ts)
        assert msg.is_expired() is True

    def test_just_under_ttl_not_expired(self) -> None:
        recent_ts = datetime.now(tz=UTC) - timedelta(seconds=10)
        msg = _msg(ttl_seconds=60, timestamp=recent_ts)
        assert msg.is_expired() is False

    def test_just_over_ttl_expired(self) -> None:
        old_ts = datetime.now(tz=UTC) - timedelta(seconds=70)
        msg = _msg(ttl_seconds=60, timestamp=old_ts)
        assert msg.is_expired() is True

    def test_long_ttl_holds(self) -> None:
        old_ts = datetime.now(tz=UTC) - timedelta(seconds=3000)
        msg = _msg(ttl_seconds=86_000, timestamp=old_ts)
        assert msg.is_expired() is False


class TestCanonicalSerialization:
    """Two messages with identical fields must produce identical signatures."""

    def test_field_order_independent(self) -> None:
        # Pydantic preserves construction order; we verify that the
        # canonical serializer (sort_keys=True) gives consistent output.
        msg1 = AgentMessage(
            correlation_id="c1",
            from_agent="a",
            to_agent="b",
            payload={"a": 1, "b": 2},
        )
        msg2 = AgentMessage(
            correlation_id="c1",
            from_agent="a",
            to_agent="b",
            payload={"b": 2, "a": 1},
            timestamp=msg1.timestamp,
            message_id=msg1.message_id,
        )
        key = b"same-key-bytes-here"
        sig1 = sign(msg1, key=key).hmac_signature
        sig2 = sign(msg2, key=key).hmac_signature
        assert sig1 == sig2
