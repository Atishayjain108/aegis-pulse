"""Tests for HMAC signing helpers."""

from __future__ import annotations

from aegis.execute.utils.hmac_signer import sign_payload, verify_payload


def test_sign_and_verify_roundtrip():
    key = "secret"
    body = b'{"alert_id":"x"}'
    sig = sign_payload(key=key, payload=body)
    assert sig is not None and len(sig) == 64  # SHA-256 hex
    assert verify_payload(key=key, payload=body, signature_hex=sig) is True


def test_sign_returns_none_when_key_empty():
    assert sign_payload(key="", payload=b"x") is None


def test_verify_fails_on_tamper():
    key = "secret"
    body = b"hello"
    sig = sign_payload(key=key, payload=body)
    assert sig is not None
    assert verify_payload(key=key, payload=b"hello!", signature_hex=sig) is False


def test_verify_fails_on_empty_key_or_sig():
    assert verify_payload(key="", payload=b"x", signature_hex="abc") is False
    assert verify_payload(key="k", payload=b"x", signature_hex="") is False
