"""
aegis.security.rbac.enforcer — Role-based access control engine.

Defines roles, permissions, and route-level enforcement for the AEGIS
FastAPI services (predict :8100, execute :8200, dashboard :8300).

Design
------
Roles form a hierarchy: ``viewer < analyst < operator < admin``.
Each role inherits all permissions of roles below it.
Permissions are defined as ``<resource>:<action>`` strings.

FastAPI integration example::

    from aegis.security.rbac import require_permission

    @app.get("/predict")
    async def predict(
        _: None = Depends(require_permission("predict:read"))
    ):
        ...

Error codes: AEGIS-SEC-0051..0060
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum

import structlog
from fastapi import Depends, HTTPException, Request, status

from aegis.security.tls.jwt_manager import JWTManager, TokenPayload

_log = structlog.get_logger(__name__)


# ── Role hierarchy ─────────────────────────────────────────────────────────── #

class Role(str, Enum):
    """Ordered roles (higher index = more privileged)."""

    VIEWER = "viewer"
    ANALYST = "analyst"
    OPERATOR = "operator"
    ADMIN = "admin"
    SERVICE = "service"  # internal service-to-service calls


_ROLE_RANK: dict[Role, int] = {
    Role.VIEWER: 0,
    Role.ANALYST: 1,
    Role.OPERATOR: 2,
    Role.ADMIN: 3,
    Role.SERVICE: 3,
}


# ── Permission catalogue ───────────────────────────────────────────────────── #

_ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.VIEWER: frozenset(
        {
            "dashboard:read",
            "signals:read",
            "predict:read",
            "alerts:read",
        }
    ),
    Role.ANALYST: frozenset(
        {
            "dashboard:read",
            "signals:read",
            "signals:export",
            "predict:read",
            "predict:batch",
            "alerts:read",
            "audit:read",
        }
    ),
    Role.OPERATOR: frozenset(
        {
            "dashboard:read",
            "dashboard:ops",
            "signals:read",
            "signals:write",
            "signals:export",
            "predict:read",
            "predict:batch",
            "predict:admin",
            "alerts:read",
            "alerts:write",
            "alerts:ack",
            "killswitch:read",
            "killswitch:trip",
            "killswitch:arm",
            "audit:read",
            "scrape:trigger",
        }
    ),
    Role.ADMIN: frozenset(
        {
            "dashboard:read",
            "dashboard:ops",
            "dashboard:admin",
            "signals:read",
            "signals:write",
            "signals:delete",
            "signals:export",
            "predict:read",
            "predict:batch",
            "predict:admin",
            "alerts:read",
            "alerts:write",
            "alerts:delete",
            "alerts:ack",
            "killswitch:read",
            "killswitch:trip",
            "killswitch:arm",
            "killswitch:override",
            "audit:read",
            "audit:export",
            "secrets:read",
            "secrets:write",
            "scrape:trigger",
            "users:read",
            "users:write",
            "users:delete",
        }
    ),
    Role.SERVICE: frozenset(
        {
            "signals:read",
            "signals:write",
            "predict:read",
            "predict:batch",
            "alerts:write",
            "audit:write",
        }
    ),
}


class RBACEnforcer:
    """Evaluates whether a principal has a given permission.

    Instances are cheap — create one per application and reuse.

    Parameters
    ----------
    jwt_manager:
        Used to verify tokens in FastAPI dependencies.
    """

    def __init__(self, jwt_manager: JWTManager | None = None) -> None:
        self._jwt = jwt_manager or JWTManager()

    # ── Core check ────────────────────────────────────────────────────────── #

    def has_permission(self, role: str | Role, permission: str) -> bool:
        """Return True if ``role`` has ``permission``.

        Includes inherited permissions from less-privileged roles.
        """
        try:
            r = Role(role)
        except ValueError:
            _log.warning("rbac.unknown_role", role=role)
            return False

        rank = _ROLE_RANK[r]
        for candidate_role, candidate_rank in _ROLE_RANK.items():
            if candidate_rank <= rank and permission in _ROLE_PERMISSIONS.get(
                candidate_role, frozenset()
            ):
                return True
        return False

    def permissions_for(self, role: str | Role) -> frozenset[str]:
        """Return all permissions (including inherited) for a role."""
        try:
            r = Role(role)
        except ValueError:
            return frozenset()

        rank = _ROLE_RANK[r]
        result: set[str] = set()
        for candidate_role, candidate_rank in _ROLE_RANK.items():
            if candidate_rank <= rank:
                result |= _ROLE_PERMISSIONS.get(candidate_role, frozenset())
        return frozenset(result)

    # ── FastAPI dependency factory ────────────────────────────────────────── #

    def require_permission(self, permission: str) -> Callable:
        """Return a FastAPI ``Depends``-compatible callable.

        Usage::

            @app.get("/admin")
            async def admin_view(
                _: None = Depends(enforcer.require_permission("users:read"))
            ):
                ...
        """
        jwt = self._jwt

        async def _check(request: Request) -> TokenPayload:
            # Extract Bearer token from Authorization header
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "error_code": "AEGIS-SEC-0051",
                        "message": "Missing Authorization header",
                        "docs": "https://aegis.internal/docs/errors/AEGIS-SEC-0051",
                    },
                )
            token = auth_header[len("Bearer "):]
            try:
                payload = jwt.verify_access_token(token)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "error_code": "AEGIS-SEC-0052",
                        "message": f"Invalid token: {exc}",
                    },
                ) from exc

            if not self.has_permission(payload.role, permission):
                _log.warning(
                    "rbac.access_denied",
                    actor=payload.sub,
                    role=payload.role,
                    required=permission,
                    path=str(request.url),
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "error_code": "AEGIS-SEC-0053",
                        "message": (
                            f"Role '{payload.role}' lacks permission '{permission}'"
                        ),
                        "docs": "https://aegis.internal/docs/errors/AEGIS-SEC-0053",
                    },
                )
            return payload

        return Depends(_check)

    def require_role(self, minimum_role: str | Role) -> Callable:
        """Require the user has at least ``minimum_role`` rank."""
        try:
            min_rank = _ROLE_RANK[Role(minimum_role)]
        except (ValueError, KeyError) as exc:
            raise ValueError(f"Unknown role: {minimum_role}") from exc

        jwt = self._jwt

        async def _check(request: Request) -> TokenPayload:
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"error_code": "AEGIS-SEC-0051", "message": "Missing auth"},
                )
            token = auth_header[len("Bearer "):]
            try:
                payload = jwt.verify_access_token(token)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"error_code": "AEGIS-SEC-0052", "message": str(exc)},
                ) from exc

            try:
                user_rank = _ROLE_RANK[Role(payload.role)]
            except (ValueError, KeyError):
                user_rank = -1

            if user_rank < min_rank:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "error_code": "AEGIS-SEC-0053",
                        "message": (
                            f"Role '{payload.role}' below minimum '{minimum_role}'"
                        ),
                    },
                )
            return payload

        return Depends(_check)
