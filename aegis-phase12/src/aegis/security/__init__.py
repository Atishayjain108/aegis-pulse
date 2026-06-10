"""
aegis.security — Phase 12: Security & Secrets Layer.

Provides zero-trust, defence-in-depth security primitives for the entire
AEGIS Pulse platform:

    - Vault integration (HashiCorp Vault OSS) for secret storage & rotation
    - SOPS + age envelope encryption for secrets at rest in git
    - PII scrubbing pipeline (NER + regex) before any persistence
    - Append-only, HMAC-signed audit log with MinIO WORM archival
    - RBAC model with per-route enforcement
    - mkcert / TLS helpers for local development
    - JWT + refresh-token authentication flow
    - Security headers middleware (CSP, HSTS, COOP/COEP, X-Frame-Options)
    - Rate-limiting middleware (Redis token bucket)
    - Secret scanning utilities (detect-secrets integration)
    - Cryptographic helpers (Ed25519, HMAC-SHA256, Fernet)

Architecture position
---------------------
Phase 12 is a *horizontal* concern that every other phase imports.
It has **no imports** from Phases 0-11 (dependency direction is inward-only)
so it can be tested in complete isolation and used as a standalone library.

Compatibility: Python 3.12.x, pydantic v2, asyncpg, aioredis, FastAPI 0.115+
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = [
    "AuditLogger",
    "JWTManager",
    "PIIScrubber",
    "RBACEnforcer",
    "RateLimitMiddleware",
    "SecretsManager",
    "SecurityConfig",
    "SecurityHeadersMiddleware",
    "VaultClient",
    "get_security_config",
]

# Re-export primary public API so callers use `from aegis.security import X`
from aegis.security.audit.logger import AuditLogger
from aegis.security.config import SecurityConfig, get_security_config
from aegis.security.middleware.headers import SecurityHeadersMiddleware
from aegis.security.middleware.ratelimit import RateLimitMiddleware
from aegis.security.pii.scrubber import PIIScrubber
from aegis.security.rbac.enforcer import RBACEnforcer
from aegis.security.secrets.manager import SecretsManager
from aegis.security.tls.jwt_manager import JWTManager
from aegis.security.vault.client import VaultClient
