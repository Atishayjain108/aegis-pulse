"""
HMAC-SHA256 message signing for inter-agent traffic.

Every `AgentMessage` flowing over Redis Streams gets a signature
covering a stable serialization of all fields *except* `hmac_signature`
itself. The receiver re-computes and compares in constant time.

The shared secret is read from `AEGIS_AGENT_HMAC_KEY` (env). If the
key is missing in dev, we fall back to a deterministic
"insecure-default" key and log a warning loudly. In production, the
verifier MUST reject messages signed with the default key.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from ..schemas import AgentMessage

_log = structlog.get_logger("aegis.agents.messaging.hmac")


_INSECURE_DEFAULT_KEY = b"INSECURE-DEFAULT-DEV-KEY-DO-NOT-USE-IN-PROD"


def get_signing_key() -> tuple[bytes, bool]:
    """Return (key_bytes, is_secure)."""
    raw = os.environ.get("AEGIS_AGENT_HMAC_KEY", "").strip()
    if not raw:
        _log.warning(
            "hmac.using_insecure_default_key",
            note="set AEGIS_AGENT_HMAC_KEY in .env for production",
        )
        return _INSECURE_DEFAULT_KEY, False
    return raw.encode("utf-8"), True


def _canonical(msg: AgentMessage) -> bytes:
    """Stable JSON serialization that excludes the signature field
    and is independent of dict ordering or Python version."""
    payload = msg.model_dump(mode="json", exclude={"hmac_signature"})
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign(msg: AgentMessage, *, key: bytes | None = None) -> AgentMessage:
    """Return a copy of `msg` with `hmac_signature` populated."""
    sk = key if key is not None else get_signing_key()[0]
    digest = hmac.new(sk, _canonical(msg), hashlib.sha256).hexdigest()
    return msg.model_copy(update={"hmac_signature": digest})


def verify(msg: AgentMessage, *, key: bytes | None = None) -> bool:
    """Constant-time signature verify. False on missing/mismatch."""
    if not msg.hmac_signature:
        return False
    sk, secure = (key, True) if key is not None else get_signing_key()
    expected = hmac.new(sk, _canonical(msg), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, msg.hmac_signature):
        return False
    if not secure:
        # In dev the default key works, but log so it's never silent.
        _log.warning("hmac.verified_with_insecure_default_key")
    return True
