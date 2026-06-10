"""
aegis.security.crypto — Low-level cryptographic primitives for AEGIS.

Provides:
    - Ed25519 key generation + sign/verify (for prediction audit trail)
    - HMAC-SHA256 helpers (for inter-service message signing)
    - Secure random token generation
    - Constant-time comparison wrappers
    - SHA-256 / SHA-512 digest helpers

All functions are synchronous (CPU-bound ops, no I/O).
Error codes: AEGIS-SEC-0101..0110
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import secrets
import struct
import time
from base64 import b64decode, b64encode
from typing import NamedTuple


class Ed25519KeyPair(NamedTuple):
    """Ed25519 key pair container."""

    private_key_pem: bytes
    public_key_pem: bytes
    private_key_hex: str
    public_key_hex: str


def generate_ed25519_keypair() -> Ed25519KeyPair:
    """Generate a new Ed25519 key pair."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    # Raw 32-byte representations — must use Raw encoding AND Raw format together
    private_raw = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    return Ed25519KeyPair(
        private_key_pem=private_pem,
        public_key_pem=public_pem,
        private_key_hex=private_raw.hex(),
        public_key_hex=public_raw.hex(),
    )


def sign_ed25519(private_key_pem: bytes, data: bytes) -> str:
    """Sign ``data`` with an Ed25519 private key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    key = load_pem_private_key(private_key_pem, password=None)
    assert isinstance(key, Ed25519PrivateKey), "Expected Ed25519PrivateKey"
    sig = key.sign(data)
    return b64encode(sig).decode()


def verify_ed25519(public_key_pem: bytes, data: bytes, signature_b64: str) -> bool:
    """Verify an Ed25519 signature. Returns True if valid, False if invalid."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    try:
        key = load_pem_public_key(public_key_pem)
        assert isinstance(key, Ed25519PublicKey)
        sig = b64decode(signature_b64)
        key.verify(sig, data)
        return True
    except (InvalidSignature, Exception):
        return False


def hmac_sha256(key: bytes | str, data: bytes | str) -> str:
    """Compute HMAC-SHA256 and return hex digest."""
    if isinstance(key, str):
        key = key.encode("utf-8")
    if isinstance(data, str):
        data = data.encode("utf-8")
    return _hmac.new(key, data, hashlib.sha256).hexdigest()


def hmac_verify(key: bytes | str, data: bytes | str, expected_hex: str) -> bool:
    """Constant-time HMAC verification."""
    computed = hmac_sha256(key, data)
    return _hmac.compare_digest(computed, expected_hex)


def sign_message(
    key: bytes | str,
    payload: bytes | str,
    *,
    timestamp: int | None = None,
) -> tuple[str, int]:
    """Sign a payload with timestamp-bound HMAC."""
    ts = timestamp if timestamp is not None else int(time.time())
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    data = struct.pack(">Q", ts) + payload
    return hmac_sha256(key, data), ts


def verify_signed_message(
    key: bytes | str,
    payload: bytes | str,
    signature_hex: str,
    timestamp: int,
    *,
    max_age_s: int = 300,
) -> bool:
    """Verify a timestamp-bound signed message."""
    now = int(time.time())
    if abs(now - timestamp) > max_age_s:
        return False
    expected, _ = sign_message(key, payload, timestamp=timestamp)
    return _hmac.compare_digest(expected, signature_hex)


def secure_token(nbytes: int = 32) -> str:
    """Generate a cryptographically secure URL-safe token."""
    return secrets.token_urlsafe(nbytes)


def secure_hex(nbytes: int = 32) -> str:
    """Generate a cryptographically secure hex token."""
    return secrets.token_hex(nbytes)


def sha256_hex(data: bytes | str) -> str:
    """Return SHA-256 hex digest of ``data``."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sha256_b64(data: bytes | str) -> str:
    """Return SHA-256 base64-encoded digest of ``data``."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return b64encode(hashlib.sha256(data).digest()).decode()


def content_hash(data: bytes | str) -> str:
    """Compute a stable 32-char content hash for deduplication."""
    return sha256_hex(data)[:32]
