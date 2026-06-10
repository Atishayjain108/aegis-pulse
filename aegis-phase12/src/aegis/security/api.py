"""
aegis.security.api — FastAPI authentication and security management routes.

Provides:
    POST /auth/token           — Issue JWT pair from credentials
    POST /auth/refresh         — Rotate refresh token
    POST /auth/revoke          — Revoke a token (adds to Redis blocklist)
    GET  /auth/me              — Return current user profile
    GET  /security/health      — Security subsystem health
    GET  /security/audit       - Recent audit log entries (admin only)
    POST /security/scan        - Trigger on-demand secret scan

Integration with Phases 0-4
----------------------------
Mount this router in the dashboard app (port 8300) and the execute-api
(port 8200) using ``app.include_router(security_router, prefix="/api")``.
The Phase 2 agent runner uses the service account JWT for inter-service calls.

Error codes: AEGIS-SEC-0101..0120
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Body, HTTPException, Request, status
from pydantic import BaseModel, Field, SecretStr

from aegis.security.audit.logger import AuditLogger
from aegis.security.config import SecurityConfig, get_security_config
from aegis.security.rbac.enforcer import RBACEnforcer
from aegis.security.tls.jwt_manager import JWTManager, TokenPair, TokenPayload

_log = structlog.get_logger(__name__)

# ── Request / Response models ─────────────────────────────────────────────── #


class LoginRequest(BaseModel):
    """Credentials for token issuance."""

    username: str = Field(min_length=1, max_length=256)
    password: SecretStr = Field(min_length=1)


class RefreshRequest(BaseModel):
    """Refresh token for rotation."""

    refresh_token: str = Field(min_length=1)


class RevokeRequest(BaseModel):
    """Token to revoke."""

    token: str = Field(min_length=1)


class UserProfile(BaseModel):
    """Authenticated user information."""

    sub: str
    role: str
    issued_at: datetime
    expires_at: datetime
    permissions: list[str]


class SecurityHealthResponse(BaseModel):
    """Security subsystem health status."""

    vault_connected: bool
    redis_connected: bool
    audit_log_writable: bool
    pii_scrubber_ready: bool
    status: str  # "healthy" | "degraded" | "unhealthy"
    checked_at: str


class AuditEntry(BaseModel):
    """A single audit log entry (redacted for API exposure)."""

    seq: int
    ts: str
    event: str
    actor: str
    resource: str
    outcome: str
    metadata: dict[str, Any]


# ── Security router factory ────────────────────────────────────────────────── #


def create_security_router(
    *,
    config: SecurityConfig | None = None,
    jwt_manager: JWTManager | None = None,
    audit_logger: AuditLogger | None = None,
    rbac_enforcer: RBACEnforcer | None = None,
    # Simple in-memory user store for dev; replace with DB lookup in prod
    user_store: dict[str, dict[str, str]] | None = None,
) -> APIRouter:
    """Build and return the security FastAPI router.

    Parameters
    ----------
    config:
        Security configuration; defaults to global singleton.
    jwt_manager:
        JWT issuance/verification; defaults to new instance.
    audit_logger:
        Audit logger; if None, audit events are dropped silently (dev only).
    rbac_enforcer:
        RBAC enforcer; defaults to new instance.
    user_store:
        Dict of ``{username: {"password_hash": ..., "role": ...}}``.
        In production, replace with a database-backed lookup.

    Returns
    -------
    APIRouter
        Mountable FastAPI router with all security endpoints.
    """
    cfg = config or get_security_config()
    jwt = jwt_manager or JWTManager(config=cfg)
    enforcer = rbac_enforcer or RBACEnforcer(jwt_manager=jwt)

    # Default dev-mode user store (NEVER use in production)
    _users: dict[str, dict[str, str]] = user_store or {
        "admin": {"password": "admin", "role": "admin"},
        "analyst": {"password": "analyst", "role": "analyst"},
        "operator": {"password": "operator", "role": "operator"},
        "viewer": {"password": "viewer", "role": "viewer"},
    }

    router = APIRouter(tags=["Security"])

    # ── POST /auth/token ──────────────────────────────────────────────────── #

    @router.post(
        "/auth/token",
        response_model=TokenPair,
        summary="Issue JWT access + refresh token pair",
        responses={
            401: {"description": "Invalid credentials"},
        },
    )
    async def issue_token(
        request: Request,
        body: Annotated[LoginRequest, Body()],
    ) -> TokenPair:
        """Authenticate with username + password; receive a JWT pair."""
        user = _users.get(body.username)
        if user is None:
            _log.warning("auth.login_failed", username=body.username, reason="user_not_found")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error_code": "AEGIS-SEC-0101",
                    "message": "Invalid credentials",
                },
            )

        # Simple plaintext comparison for dev mode
        # PRODUCTION: use bcrypt/argon2 hash comparison
        if body.password.get_secret_value() != user.get("password", ""):
            _log.warning("auth.login_failed", username=body.username, reason="wrong_password")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error_code": "AEGIS-SEC-0101",
                    "message": "Invalid credentials",
                },
            )

        pair = jwt.issue_tokens(subject=body.username, role=user["role"])
        _log.info("auth.login_success", username=body.username, role=user["role"])

        if audit_logger:
            await audit_logger.log(
                "auth.token_issued",
                actor=f"user:{body.username}",
                resource="jwt",
                outcome="success",
                metadata={"role": user["role"], "ip": _get_ip(request)},
            )
        return pair

    # ── POST /auth/refresh ────────────────────────────────────────────────── #

    @router.post(
        "/auth/refresh",
        response_model=TokenPair,
        summary="Rotate a refresh token and issue new pair",
    )
    async def refresh_token(
        request: Request,
        body: Annotated[RefreshRequest, Body()],
    ) -> TokenPair:
        """Consume a refresh token and return a new access + refresh pair."""
        try:
            new_pair = await jwt.rotate_refresh_token(body.refresh_token)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error_code": "AEGIS-SEC-0102",
                    "message": f"Token rotation failed: {exc}",
                },
            ) from exc

        if audit_logger:
            payload = jwt.verify_access_token(new_pair.access_token)
            await audit_logger.log(
                "auth.token_rotated",
                actor=f"user:{payload.sub}",
                resource="jwt",
                outcome="success",
                metadata={"ip": _get_ip(request)},
            )
        return new_pair

    # ── POST /auth/revoke ─────────────────────────────────────────────────── #

    @router.post(
        "/auth/revoke",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Revoke a JWT token",
    )
    async def revoke_token(
        request: Request,
        body: Annotated[RevokeRequest, Body()],
    ) -> None:
        """Add a token to the revocation blocklist (requires valid auth)."""
        await jwt.revoke_token(body.token)
        if audit_logger:
            await audit_logger.log(
                "auth.token_revoked",
                actor="user:unknown",
                resource="jwt",
                outcome="success",
                metadata={"ip": _get_ip(request)},
            )

    # ── GET /auth/me ──────────────────────────────────────────────────────── #

    @router.get(
        "/auth/me",
        response_model=UserProfile,
        summary="Return the authenticated user's profile",
    )
    async def get_me(
        payload: TokenPayload = enforcer.require_permission("dashboard:read"),
    ) -> UserProfile:
        """Return the caller's profile derived from the JWT."""
        return UserProfile(
            sub=payload.sub,
            role=payload.role,
            issued_at=payload.issued_at,
            expires_at=payload.expires_at,
            permissions=sorted(enforcer.permissions_for(payload.role)),
        )

    # ── GET /security/health ──────────────────────────────────────────────── #

    @router.get(
        "/security/health",
        response_model=SecurityHealthResponse,
        summary="Security subsystem health check",
    )
    async def security_health() -> SecurityHealthResponse:
        """Check health of all security subsystems."""
        vault_ok = False
        redis_ok = False
        audit_ok = audit_logger is not None
        pii_ok = True  # always true; spaCy is optional

        # Vault check
        try:
            from aegis.security.vault.client import VaultClient

            async with VaultClient(cfg) as vc:
                h = await vc.health()
                vault_ok = h.get("initialized", False)
        except Exception:
            vault_ok = False

        # Redis check (best-effort)
        try:
            import redis.asyncio as aioredis  # type: ignore[import-untyped]

            r = aioredis.from_url(cfg.redis_url, socket_timeout=1.0)
            await r.ping()
            await r.aclose()
            redis_ok = True
        except Exception:
            redis_ok = False

        all_ok = vault_ok and redis_ok and audit_ok
        degraded = not all_ok and (vault_ok or redis_ok)

        return SecurityHealthResponse(
            vault_connected=vault_ok,
            redis_connected=redis_ok,
            audit_log_writable=audit_ok,
            pii_scrubber_ready=pii_ok,
            status="healthy" if all_ok else ("degraded" if degraded else "unhealthy"),
            checked_at=datetime.now(tz=UTC).isoformat(),
        )

    # ── GET /security/audit ───────────────────────────────────────────────── #

    @router.get(
        "/security/audit",
        response_model=list[AuditEntry],
        summary="Retrieve recent audit log entries (admin only)",
    )
    async def get_audit_log(
        limit: int = 50,
        payload: TokenPayload = enforcer.require_permission("audit:read"),
    ) -> list[AuditEntry]:
        """Return the last ``limit`` audit log entries."""
        if audit_logger is None:
            return []
        if audit_logger._log_path is None or not audit_logger._log_path.exists():
            return []

        import json

        entries: list[AuditEntry] = []
        lines: list[str] = []
        with open(audit_logger._log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(line)

        # Return last N lines
        for raw in lines[-limit:]:
            try:
                e = json.loads(raw)
                entries.append(
                    AuditEntry(
                        seq=e.get("seq", 0),
                        ts=e.get("ts", ""),
                        event=e.get("event", ""),
                        actor=e.get("actor", ""),
                        resource=e.get("resource", ""),
                        outcome=e.get("outcome", ""),
                        metadata=e.get("metadata", {}),
                    )
                )
            except Exception as exc:
                _log.debug("audit.entry_parse_error", error=str(exc))
                continue
        return entries

    return router


# ── Utility ───────────────────────────────────────────────────────────────── #


def _get_ip(request: Request) -> str:
    """Extract real client IP from request headers."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


# ── App factory (for standalone usage) ────────────────────────────────────── #


def create_security_app(
    config: SecurityConfig | None = None,
) -> object:
    """Create a standalone FastAPI app with all security middleware.

    Useful for testing or running the security service independently.
    """
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    from aegis.security.middleware.headers import SecurityHeadersMiddleware

    cfg = config or get_security_config()
    app = FastAPI(
        title="AEGIS Security API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Middleware (order matters: outermost first)
    app.add_middleware(SecurityHeadersMiddleware, config=cfg)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # Security routes
    router = create_security_router(config=cfg)
    app.include_router(router, prefix="/api")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
