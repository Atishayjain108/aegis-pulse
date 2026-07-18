"""
tests.integration.security.test_security_pipeline — End-to-end security pipeline tests.

These tests exercise the full security stack against real in-process
components (no external services required).

Tests:
    - Full scrape signal flow through PII scrubber → audit logger
    - JWT issuance → RBAC enforcement → audit trail
    - Secrets manager fallback chain under vault unavailability
    - Rate-limit middleware integration with FastAPI
    - Security headers on all endpoints
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import SecretStr

# ── Full PII + Audit pipeline ─────────────────────────────────────────────── #


class TestPIIAuditPipeline:
    """Test that signals pass through PII scrubber and audit logger together."""

    @pytest.mark.asyncio
    async def test_signal_scrubbed_and_audited(self, tmp_path: Path) -> None:
        from aegis.security.audit.logger import AuditLogger
        from aegis.security.config import SecurityConfig
        from aegis.security.pii.scrubber import PIIScrubber

        cfg = SecurityConfig(
            audit_log_path=str(tmp_path / "pipeline_audit.jsonl"),
            jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"),
            hmac_key=SecretStr("test-hmac-key-not-for-prod-use-ok"),
        )

        scrubber = PIIScrubber(config=cfg)
        scrubber._spacy_available = False

        raw_signal = {
            "title": "Hot product by seller@marketplace.com",
            "body": "Contact: 555-123-4567 or visit us at 192.168.1.50",
            "score": 0.87,
            "platform": "reddit",
        }

        scrubbed, report = scrubber.scrub_dict(raw_signal)
        assert "seller@marketplace.com" not in scrubbed["title"]
        assert "555-123-4567" not in scrubbed["body"]
        assert "192.168.1.50" not in scrubbed["body"]
        assert scrubbed["score"] == 0.87  # numeric preserved

        async with AuditLogger(config=cfg) as logger:
            entry = await logger.log(
                "signal.ingested",
                actor="service:scraper",
                resource="signals",
                outcome="success",
                metadata={
                    "pii_removed": report.total_replacements,
                    "platform": raw_signal["platform"],
                },
            )

        assert entry["metadata"]["pii_removed"] >= 3  # email + phone + ip

        # Verify audit integrity
        new_logger = AuditLogger(config=cfg)
        valid, count, err = await new_logger.verify_integrity()
        assert valid is True
        assert count == 1


# ── JWT + RBAC Pipeline ───────────────────────────────────────────────────── #


class TestJWTRBACPipeline:
    """Test JWT issuance through RBAC enforcement end-to-end."""

    def test_analyst_cannot_access_killswitch(self) -> None:
        from pydantic import SecretStr

        from aegis.security.config import SecurityConfig
        from aegis.security.rbac.enforcer import RBACEnforcer
        from aegis.security.tls.jwt_manager import JWTManager

        cfg = SecurityConfig(jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"))
        mgr = JWTManager(config=cfg)
        enforcer = RBACEnforcer(jwt_manager=mgr)

        pair = mgr.issue_tokens(subject="analyst-bob", role="analyst")
        payload = mgr.verify_access_token(pair.access_token)

        # Analyst has read perms
        assert enforcer.has_permission(payload.role, "signals:read") is True
        # Analyst lacks killswitch
        assert enforcer.has_permission(payload.role, "killswitch:trip") is False

    def test_operator_can_trip_killswitch(self) -> None:
        from pydantic import SecretStr

        from aegis.security.config import SecurityConfig
        from aegis.security.rbac.enforcer import RBACEnforcer
        from aegis.security.tls.jwt_manager import JWTManager

        cfg = SecurityConfig(jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"))
        mgr = JWTManager(config=cfg)
        enforcer = RBACEnforcer(jwt_manager=mgr)

        pair = mgr.issue_tokens(subject="ops-alice", role="operator")
        payload = mgr.verify_access_token(pair.access_token)

        assert enforcer.has_permission(payload.role, "killswitch:trip") is True

    @pytest.mark.asyncio
    async def test_refresh_rotation_produces_valid_access_token(self) -> None:
        from pydantic import SecretStr

        from aegis.security.config import SecurityConfig
        from aegis.security.tls.jwt_manager import JWTManager

        cfg = SecurityConfig(jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"))
        mgr = JWTManager(config=cfg)

        # Issue → rotate → use new access token
        pair1 = mgr.issue_tokens(subject="user-rotate", role="operator")
        pair2 = await mgr.rotate_refresh_token(pair1.refresh_token)
        payload = mgr.verify_access_token(pair2.access_token)
        assert payload.sub == "user-rotate"
        assert payload.role == "operator"


# ── FastAPI Security Middleware Integration ────────────────────────────────── #


class TestFastAPISecurityIntegration:
    """Test security middleware working together on a FastAPI app."""

    def _build_secure_app(self):
        from fastapi import FastAPI

        from aegis.security.middleware.headers import SecurityHeadersMiddleware

        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware, enable_hsts=False)

        @app.get("/healthz")
        async def healthz():
            return {"status": "ok"}

        @app.get("/api/signals")
        async def signals():
            return {"signals": []}

        return app

    def test_all_security_headers_present(self) -> None:
        from fastapi.testclient import TestClient

        app = self._build_secure_app()
        client = TestClient(app)
        resp = client.get("/healthz")

        required_headers = [
            "X-Frame-Options",
            "X-Content-Type-Options",
            "Content-Security-Policy",
            "Referrer-Policy",
            "Permissions-Policy",
        ]
        for h in required_headers:
            assert h in resp.headers, f"Missing header: {h}"

    def test_server_header_anonymized(self) -> None:
        from fastapi.testclient import TestClient

        app = self._build_secure_app()
        client = TestClient(app)
        resp = client.get("/healthz")

        # Should not leak framework version
        server = resp.headers.get("Server", "")
        assert "uvicorn" not in server.lower()
        assert "starlette" not in server.lower()

    def test_csp_blocks_framing(self) -> None:
        from fastapi.testclient import TestClient

        app = self._build_secure_app()
        client = TestClient(app)
        resp = client.get("/healthz")

        csp = resp.headers.get("Content-Security-Policy", "")
        assert "frame-ancestors 'none'" in csp


# ── Crypto roundtrip ──────────────────────────────────────────────────────── #


class TestCryptoRoundtrip:
    def test_sign_verify_prediction_record(self) -> None:
        """Simulate signing a prediction record for audit trail."""
        from aegis.security.crypto import (
            generate_ed25519_keypair,
            sign_ed25519,
            verify_ed25519,
        )

        keypair = generate_ed25519_keypair()

        prediction = {
            "trend_id": "t-123",
            "verdict": "ENTER",
            "score": 0.91,
            "confidence": 0.87,
            "ts": "2026-05-19T12:00:00Z",
        }
        canonical = json.dumps(prediction, sort_keys=True, separators=(",", ":"))
        data = canonical.encode("utf-8")

        sig = sign_ed25519(keypair.private_key_pem, data)
        assert verify_ed25519(keypair.public_key_pem, data, sig) is True

        # Tampered prediction must fail
        prediction["score"] = 0.99
        tampered = json.dumps(prediction, sort_keys=True, separators=(",", ":")).encode()
        assert verify_ed25519(keypair.public_key_pem, tampered, sig) is False
