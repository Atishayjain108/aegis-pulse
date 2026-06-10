"""
aegis.security.tls.jwt_manager — JWT access + refresh token lifecycle.

Issues signed JWTs (HS256 by default) and manages:
    - Access tokens (short-lived, 15 min default)
    - Refresh tokens (long-lived, 7 days default)
    - Token revocation via Redis blocklist
    - Refresh token rotation (one-use)

Token payload schema (``TokenPayload``)::

    {
        "sub":   "user-uuid or service-name",
        "role":  "viewer | analyst | operator | admin | service",
        "iss":   "aegis-pulse",
        "iat":   <unix timestamp>,
        "exp":   <unix timestamp>,
        "jti":   "<uuid>",        # unique token ID (for revocation)
        "type":  "access | refresh"
    }

Error codes: AEGIS-SEC-0061..0070
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt as pyjwt  # PyJWT
import structlog
from pydantic import BaseModel, Field

from aegis.security.config import SecurityConfig, get_security_config

_log = structlog.get_logger(__name__)


class TokenPayload(BaseModel):
    """Validated JWT payload returned by ``verify_*_token``."""

    sub: str = Field(description="Subject (user ID or service name)")
    role: str = Field(description="User role")
    iss: str = Field(description="Issuer")
    iat: int = Field(description="Issued-at (Unix timestamp)")
    exp: int = Field(description="Expiry (Unix timestamp)")
    jti: str = Field(description="JWT ID (unique per token)")
    type: Literal["access", "refresh"] = Field(description="Token type")

    @property
    def issued_at(self) -> datetime:
        return datetime.fromtimestamp(self.iat, tz=UTC)

    @property
    def expires_at(self) -> datetime:
        return datetime.fromtimestamp(self.exp, tz=UTC)

    @property
    def is_expired(self) -> bool:
        return datetime.now(tz=UTC) >= self.expires_at


class TokenPair(BaseModel):
    """Issued access + refresh token pair."""

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    access_expires_in: int = Field(description="Access token lifetime in seconds")
    refresh_expires_in: int = Field(description="Refresh token lifetime in seconds")


class JWTManager:
    """Issues and verifies JWTs with Redis-backed revocation.

    Parameters
    ----------
    config:
        Injected config; defaults to global singleton.
    redis_client:
        Async Redis client for token revocation list.
        If ``None``, revocation is skipped (dev mode only).
    """

    def __init__(
        self,
        config: SecurityConfig | None = None,
        redis_client: object | None = None,
    ) -> None:
        self._cfg = config or get_security_config()
        self._redis = redis_client  # aioredis.Redis or None

    # ── Token issuance ─────────────────────────────────────────────────────── #

    def issue_tokens(
        self,
        *,
        subject: str,
        role: str = "viewer",
    ) -> TokenPair:
        """Issue a fresh access + refresh token pair.

        Parameters
        ----------
        subject:
            User ID or service principal name.
        role:
            Role to embed in the token.

        Returns
        -------
        TokenPair
        """
        access_ttl = timedelta(minutes=self._cfg.jwt_access_ttl_minutes)
        refresh_ttl = timedelta(days=self._cfg.jwt_refresh_ttl_days)

        access_token = self._create_token(subject, role, "access", access_ttl)
        refresh_token = self._create_token(subject, role, "refresh", refresh_ttl)

        _log.info(
            "jwt.issued",
            sub=subject,
            role=role,
            access_exp_m=self._cfg.jwt_access_ttl_minutes,
        )
        return TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            access_expires_in=int(access_ttl.total_seconds()),
            refresh_expires_in=int(refresh_ttl.total_seconds()),
        )

    # ── Verification ──────────────────────────────────────────────────────── #

    def verify_access_token(self, token: str) -> TokenPayload:
        """Verify and decode an access token.

        Raises
        ------
        jwt.ExpiredSignatureError
            Token has expired.
        jwt.InvalidTokenError
            Token is malformed or signature is invalid.
        ValueError
            Token is a refresh token (wrong type).
        """
        payload = self._decode(token)
        if payload.type != "access":
            raise ValueError("[AEGIS-SEC-0061] Expected access token, got refresh token")
        return payload

    def verify_refresh_token(self, token: str) -> TokenPayload:
        """Verify and decode a refresh token."""
        payload = self._decode(token)
        if payload.type != "refresh":
            raise ValueError("[AEGIS-SEC-0062] Expected refresh token, got access token")
        return payload

    async def rotate_refresh_token(self, refresh_token: str) -> TokenPair:
        """Consume a refresh token and issue a new pair.

        The old refresh token is added to the revocation blocklist.
        """
        payload = self.verify_refresh_token(refresh_token)

        # Revoke old refresh token
        await self.revoke_token(refresh_token, payload.jti)

        # Issue new pair
        new_pair = self.issue_tokens(subject=payload.sub, role=payload.role)
        _log.info("jwt.rotated", sub=payload.sub)
        return new_pair

    async def revoke_token(self, token: str, jti: str | None = None) -> None:
        """Add a token to the Redis revocation blocklist."""
        if self._redis is None:
            _log.debug("jwt.revoke_skipped", reason="redis_unavailable")
            return
        if jti is None:
            try:
                payload = self._decode(token)
                jti = payload.jti
            except Exception:
                return

        key = f"aegis:jwt:revoked:{jti}"
        # Keep in Redis until token naturally expires (+ 60s buffer)
        try:
            remaining = self._cfg.jwt_refresh_ttl_days * 86400 + 60
            await self._redis.setex(key, remaining, "1")  # type: ignore[union-attr]
            _log.info("jwt.revoked", jti=jti)
        except Exception as exc:
            _log.error("jwt.revocation_failed", jti=jti, error=str(exc))

    async def is_revoked(self, jti: str) -> bool:
        """Check whether a token JTI has been revoked."""
        if self._redis is None:
            return False
        try:
            exists = await self._redis.exists(f"aegis:jwt:revoked:{jti}")  # type: ignore[union-attr]
            return bool(exists)
        except Exception:
            return False

    # ── Internals ─────────────────────────────────────────────────────────── #

    def _create_token(
        self,
        subject: str,
        role: str,
        token_type: Literal["access", "refresh"],
        ttl: timedelta,
    ) -> str:
        now = datetime.now(tz=UTC)
        claims: dict[str, object] = {
            "sub": subject,
            "role": role,
            "iss": self._cfg.jwt_issuer,
            "iat": int(now.timestamp()),
            "exp": int((now + ttl).timestamp()),
            "jti": str(uuid.uuid4()),
            "type": token_type,
        }
        return pyjwt.encode(
            claims,
            self._cfg.jwt_secret.get_secret_value(),
            algorithm=self._cfg.jwt_algorithm,
        )

    def _decode(self, token: str) -> TokenPayload:
        try:
            raw = pyjwt.decode(
                token,
                self._cfg.jwt_secret.get_secret_value(),
                algorithms=[self._cfg.jwt_algorithm],
                options={"verify_exp": True},
            )
        except pyjwt.ExpiredSignatureError as exc:
            raise pyjwt.ExpiredSignatureError(
                f"[AEGIS-SEC-0063] Token expired: {exc}"
            ) from exc
        except pyjwt.InvalidTokenError as exc:
            raise pyjwt.InvalidTokenError(
                f"[AEGIS-SEC-0064] Invalid token: {exc}"
            ) from exc
        return TokenPayload(**raw)
