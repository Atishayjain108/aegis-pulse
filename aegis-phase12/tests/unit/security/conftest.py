"""Shared pytest fixtures for Phase 12 security tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import SecretStr


@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture
def security_config():
    """Return a test-safe SecurityConfig with known values."""
    from aegis.security.config import SecurityConfig

    return SecurityConfig(
        vault_dev_mode=True,
        vault_addr="http://127.0.0.1:8200",
        jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"),
        hmac_key=SecretStr("test-hmac-key-not-for-prod-use-ok"),
        pii_enabled=True,
        rate_limit_enabled=True,
        audit_log_path="/tmp/aegis_test_audit.jsonl",
    )


@pytest.fixture
def jwt_manager(security_config):
    """Return a JWTManager with test config."""
    from aegis.security.tls.jwt_manager import JWTManager

    return JWTManager(config=security_config)


@pytest.fixture
def pii_scrubber(security_config):
    """Return a PIIScrubber with NER disabled (no spaCy in CI)."""
    from aegis.security.pii.scrubber import PIIScrubber

    scrubber = PIIScrubber(config=security_config)
    scrubber._spacy_available = False
    return scrubber


@pytest.fixture
def rbac_enforcer(jwt_manager):
    """Return an RBACEnforcer with test JWT manager."""
    from aegis.security.rbac.enforcer import RBACEnforcer

    return RBACEnforcer(jwt_manager=jwt_manager)


@pytest.fixture
async def audit_logger(tmp_path: Path, security_config):
    """Return a started AuditLogger writing to a temp path."""
    from aegis.security.audit.logger import AuditLogger
    from aegis.security.config import SecurityConfig

    cfg = SecurityConfig(
        audit_log_path=str(tmp_path / "test_audit.jsonl"),
        jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"),
        hmac_key=SecretStr("test-hmac-key-not-for-prod-use-ok"),
    )
    logger = AuditLogger(config=cfg)
    await logger.start()
    yield logger
    await logger.stop()
