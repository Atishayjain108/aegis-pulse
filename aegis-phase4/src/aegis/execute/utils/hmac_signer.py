"""HMAC-SHA256 signing helpers.

Used for:
  - Signing AlertEnvelope payloads before they hit a webhook.
  - Verifying inbound webhook ACKs (future).

The signer never logs the secret. The signer fails closed (returns None) if
no key is configured — the caller decides whether that is a fatal condition.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Final


def sign_payload(*, key: str, payload: bytes) -> str | None:
    """Return hex-encoded HMAC-SHA256, or None if key is empty.

    Caller must treat `None` as "no signature available" — typically that
    means the channel that needed it should be disabled at startup, never
    reached here. But we are defensive.
    """
    if not key:
        return None
    mac = hmac.new(key.encode("utf-8"), payload, hashlib.sha256)
    return mac.hexdigest()


def verify_payload(*, key: str, payload: bytes, signature_hex: str) -> bool:
    """Constant-time HMAC verification."""
    if not key or not signature_hex:
        return False
    expected = hmac.new(key.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_hex)


__all__: Final = ["sign_payload", "verify_payload"]
