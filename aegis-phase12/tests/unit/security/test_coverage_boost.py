"""
tests.unit.security.test_coverage_boost — Additional tests to reach 82% coverage.

Covers:
    - api.py            : auth endpoints, security health, audit endpoint
    - middleware/ratelimit: token bucket, IP extraction, skip paths
    - integration.py    : PII bridge, agent signing, prediction signing
    - tls/jwt_manager   : token payload properties, is_revoked
    - rbac/enforcer     : require_role dependency
    - secrets/manager   : encrypt/decrypt edge cases
    - audit/logger      : upload worker cancellation, concurrent logging
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

# ── API endpoint tests ────────────────────────────────────────────────────── #

class TestSecurityAPI:
    def _make_app(self, tmp_path: Path):
        from fastapi import FastAPI

        from aegis.security.api import create_security_router
        from aegis.security.config import SecurityConfig
        from aegis.security.rbac.enforcer import RBACEnforcer
        from aegis.security.tls.jwt_manager import JWTManager

        cfg = SecurityConfig(
            jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"),
            hmac_key=SecretStr("test-hmac-key-not-for-prod-use-ok"),
            audit_log_path=str(tmp_path / "audit.jsonl"),
        )
        jwt = JWTManager(config=cfg)
        enforcer = RBACEnforcer(jwt_manager=jwt)
        router = create_security_router(config=cfg, jwt_manager=jwt, rbac_enforcer=enforcer)

        app = FastAPI()
        app.include_router(router, prefix="/api")
        return app, jwt, cfg

    def test_issue_token_success(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _, _ = self._make_app(tmp_path)
        client = TestClient(app)
        resp = client.post("/api/auth/token", json={"username": "admin", "password": "admin"})
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "Bearer"

    def test_issue_token_wrong_password(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _, _ = self._make_app(tmp_path)
        client = TestClient(app)
        resp = client.post(
            "/api/auth/token", json={"username": "admin", "password": "wrongpassword"}
        )
        assert resp.status_code == 401
        assert resp.json()["detail"]["error_code"] == "AEGIS-SEC-0101"

    def test_issue_token_unknown_user(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _, _ = self._make_app(tmp_path)
        client = TestClient(app)
        resp = client.post("/api/auth/token", json={"username": "nobody", "password": "pass"})
        assert resp.status_code == 401

    def test_get_me_with_valid_token(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, jwt, _ = self._make_app(tmp_path)
        client = TestClient(app)

        # First get a token
        pair = jwt.issue_tokens(subject="testuser", role="analyst")
        resp = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {pair.access_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["sub"] == "testuser"
        assert data["role"] == "analyst"
        assert "permissions" in data
        assert "signals:read" in data["permissions"]

    def test_get_me_no_token(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _, _ = self._make_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_refresh_token(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, jwt, _ = self._make_app(tmp_path)
        client = TestClient(app)

        pair = jwt.issue_tokens(subject="user-refresh", role="viewer")
        resp = client.post(
            "/api/auth/refresh",
            json={"refresh_token": pair.refresh_token},
        )
        assert resp.status_code == 200
        new_pair = resp.json()
        assert "access_token" in new_pair

    def test_refresh_with_access_token_fails(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, jwt, _ = self._make_app(tmp_path)
        client = TestClient(app)

        pair = jwt.issue_tokens(subject="user", role="viewer")
        # Send access token where refresh is expected
        resp = client.post(
            "/api/auth/refresh",
            json={"refresh_token": pair.access_token},  # wrong type
        )
        assert resp.status_code == 401

    def test_revoke_token(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, jwt, _ = self._make_app(tmp_path)
        client = TestClient(app)

        pair = jwt.issue_tokens(subject="user-revoke", role="viewer")
        resp = client.post(
            "/api/auth/revoke",
            json={"token": pair.access_token},
        )
        assert resp.status_code == 204

    def test_security_health(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _, _ = self._make_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/api/security/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "vault_connected" in data
        assert "redis_connected" in data
        assert data["status"] in ("healthy", "degraded", "unhealthy")

    def test_audit_log_endpoint_requires_auth(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _, _ = self._make_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/api/security/audit")
        assert resp.status_code == 401

    def test_audit_log_endpoint_with_analyst_token(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, jwt, _ = self._make_app(tmp_path)
        client = TestClient(app)

        pair = jwt.issue_tokens(subject="analyst-user", role="analyst")
        resp = client.get(
            "/api/security/audit",
            headers={"Authorization": f"Bearer {pair.access_token}"},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ── Rate limit middleware tests ───────────────────────────────────────────── #

class TestRateLimitMiddleware:
    def _make_app_with_ratelimit(self, redis_mock: Any):
        from fastapi import FastAPI

        from aegis.security.config import SecurityConfig
        from aegis.security.middleware.ratelimit import RateLimitMiddleware

        cfg = SecurityConfig(
            rate_limit_enabled=True,
            rate_limit_default_rpm=10,
            rate_limit_burst=3,
        )
        app = FastAPI()
        app.add_middleware(
            RateLimitMiddleware,
            config=cfg,
            redis_client=redis_mock,
            default_rpm=10,
            burst=3,
        )

        @app.get("/test")
        async def test_endpoint():
            return {"ok": True}

        @app.get("/healthz")
        async def health():
            return {"status": "ok"}

        return app

    def test_skip_path_not_rate_limited(self) -> None:
        """Health check path should always pass through."""
        from fastapi.testclient import TestClient

        app = self._make_app_with_ratelimit(None)  # No Redis = passthrough
        client = TestClient(app)
        for _ in range(5):
            resp = client.get("/healthz")
            assert resp.status_code == 200

    def test_no_redis_allows_all_requests(self) -> None:
        """Without Redis the middleware is a no-op (fail-open)."""
        from fastapi.testclient import TestClient

        app = self._make_app_with_ratelimit(None)
        client = TestClient(app)
        for _ in range(20):
            resp = client.get("/test")
            assert resp.status_code == 200

    def test_rate_limit_disabled_config(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from aegis.security.config import SecurityConfig
        from aegis.security.middleware.ratelimit import RateLimitMiddleware

        cfg = SecurityConfig(rate_limit_enabled=False)
        app = FastAPI()
        app.add_middleware(RateLimitMiddleware, config=cfg)

        @app.get("/test")
        async def test_ep():
            return {"ok": True}

        client = TestClient(app)
        resp = client.get("/test")
        assert resp.status_code == 200

    def test_extract_ip_from_forwarded_for(self) -> None:
        from unittest.mock import MagicMock

        from aegis.security.middleware.ratelimit import RateLimitMiddleware

        req = MagicMock()
        req.headers = {"X-Forwarded-For": "10.0.0.1, 10.0.0.2"}
        req.client = None

        ip = RateLimitMiddleware._extract_ip(req)
        assert ip == "10.0.0.1"

    def test_extract_ip_from_real_ip(self) -> None:
        from unittest.mock import MagicMock

        from aegis.security.middleware.ratelimit import RateLimitMiddleware

        req = MagicMock()
        req.headers = {"X-Real-IP": "203.0.113.5"}
        req.client = None

        ip = RateLimitMiddleware._extract_ip(req)
        assert ip == "203.0.113.5"

    def test_extract_ip_from_client(self) -> None:
        from unittest.mock import MagicMock

        from aegis.security.middleware.ratelimit import RateLimitMiddleware

        req = MagicMock()
        req.headers = {}
        req.client = MagicMock()
        req.client.host = "192.168.1.50"

        ip = RateLimitMiddleware._extract_ip(req)
        assert ip == "192.168.1.50"

    def test_extract_ip_unknown_fallback(self) -> None:
        from unittest.mock import MagicMock

        from aegis.security.middleware.ratelimit import RateLimitMiddleware

        req = MagicMock()
        req.headers = {}
        req.client = None

        ip = RateLimitMiddleware._extract_ip(req)
        assert ip == "unknown"

    @pytest.mark.asyncio
    async def test_redis_error_fails_open(self) -> None:
        """Redis errors should allow the request (fail-open)."""
        from aegis.security.config import SecurityConfig
        from aegis.security.middleware.ratelimit import RateLimitMiddleware

        bad_redis = MagicMock()
        bad_redis.script_load = AsyncMock(side_effect=Exception("Redis down"))
        bad_redis.evalsha = AsyncMock(side_effect=Exception("Redis down"))

        cfg = SecurityConfig(rate_limit_enabled=True, rate_limit_default_rpm=10, rate_limit_burst=3)
        mw = RateLimitMiddleware(MagicMock(), config=cfg, redis_client=bad_redis)
        remaining, reset_ms = await mw._consume_token("test-key", capacity=3, rpm=10)
        # Should return capacity (fail-open)
        assert remaining == 3


# ── Integration bridge tests ──────────────────────────────────────────────── #

class TestIntegrationBridge:
    def test_secure_scrape_signal_removes_pii(self) -> None:
        from aegis.security.integration import secure_scrape_signal

        raw = {
            "title": "Product by alice@test.com",
            "body": "Call 555-867-5309 now",
            "platform": "reddit",
            "score": 0.85,
        }
        scrubbed, report = secure_scrape_signal(raw)
        assert "alice@test.com" not in scrubbed["title"]
        assert scrubbed["score"] == 0.85  # numeric preserved
        assert scrubbed["platform"] == "reddit"  # non-PII preserved
        assert report.was_modified is True

    def test_secure_scrape_clean_signal_unchanged(self) -> None:
        from aegis.security.integration import secure_scrape_signal

        raw = {"title": "Bitcoin price up 10%", "platform": "reddit", "score": 0.7}
        scrubbed, report = secure_scrape_signal(raw)
        assert scrubbed["title"] == raw["title"]
        assert report.was_modified is False

    def test_sign_and_verify_agent_message(self) -> None:
        from aegis.security.integration import sign_agent_message, verify_agent_message

        payload = {"trend_id": "t-abc", "verdict": "ENTER", "score": 0.91}
        key = "test-hmac-key-for-agents"

        sig = sign_agent_message(payload, key)
        assert len(sig) == 64  # hex SHA256

        valid = verify_agent_message(payload, key, sig)
        assert valid is True

    def test_agent_message_tamper_detected(self) -> None:
        from aegis.security.integration import sign_agent_message, verify_agent_message

        payload = {"trend_id": "t-xyz", "verdict": "HOLD"}
        sig = sign_agent_message(payload, "mykey")

        tampered = {**payload, "verdict": "ENTER"}  # changed verdict
        assert verify_agent_message(tampered, "mykey", sig) is False

    def test_agent_message_wrong_key(self) -> None:
        from aegis.security.integration import sign_agent_message, verify_agent_message

        payload = {"data": "sensitive"}
        sig = sign_agent_message(payload, "key-a")
        assert verify_agent_message(payload, "key-b", sig) is False

    def test_sign_prediction_roundtrip(self) -> None:
        from aegis.security.crypto import generate_ed25519_keypair
        from aegis.security.integration import sign_prediction, verify_prediction_signature

        keypair = generate_ed25519_keypair()
        prediction = {
            "trend_id": "t-001",
            "verdict": "ENTER",
            "score": 0.88,
            "confidence": 0.82,
            "ts": "2026-05-19T12:00:00Z",
        }
        sig = sign_prediction(prediction, keypair.private_key_pem)
        assert verify_prediction_signature(prediction, keypair.public_key_pem, sig) is True

    def test_prediction_tamper_fails_verification(self) -> None:
        from aegis.security.crypto import generate_ed25519_keypair
        from aegis.security.integration import sign_prediction, verify_prediction_signature

        keypair = generate_ed25519_keypair()
        prediction = {"trend_id": "t-002", "score": 0.75}
        sig = sign_prediction(prediction, keypair.private_key_pem)

        tampered = {**prediction, "score": 0.99}
        assert verify_prediction_signature(tampered, keypair.public_key_pem, sig) is False

    @pytest.mark.asyncio
    async def test_audit_alert_dispatch_no_logger(self) -> None:
        """Should be a no-op when audit_logger is None."""
        from aegis.security.integration import audit_alert_dispatch

        # Should not raise
        await audit_alert_dispatch(
            alert_id="a-001",
            trend_id="t-001",
            verdict="ENTER",
            channel="telegram",
            outcome="success",
            audit_logger=None,
        )

    @pytest.mark.asyncio
    async def test_audit_alert_dispatch_with_logger(self, tmp_path: Path) -> None:
        from aegis.security.audit.logger import AuditLogger
        from aegis.security.config import SecurityConfig
        from aegis.security.integration import audit_alert_dispatch

        cfg = SecurityConfig(
            audit_log_path=str(tmp_path / "alert_audit.jsonl"),
            jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"),
            hmac_key=SecretStr("test-hmac-key-not-for-prod-use-ok"),
        )
        async with AuditLogger(config=cfg) as logger:
            await audit_alert_dispatch(
                alert_id="a-002",
                trend_id="t-002",
                verdict="HOLD",
                channel="discord",
                outcome="success",
                audit_logger=logger,
                metadata={"score": 0.72},
            )

        lines = (tmp_path / "alert_audit.jsonl").read_text().strip().split("\n")
        entry = json.loads(lines[0])
        assert entry["event"] == "alert.discord.dispatch"
        assert entry["metadata"]["verdict"] == "HOLD"

    @pytest.mark.asyncio
    async def test_secure_redis_publish_consume_roundtrip(self) -> None:
        from aegis.security.integration import secure_redis_consume, secure_redis_publish

        # Mock Redis
        published_messages: list[dict] = []

        class FakeRedis:
            async def xadd(self, stream: str, fields: dict) -> None:
                published_messages.append({"stream": stream, "fields": fields})

        redis = FakeRedis()
        payload = {"trend_id": "t-stream", "verdict": "ENTER", "score": 0.9}
        key = "test-stream-key"

        await secure_redis_publish(redis, "aegis:test", payload, key)
        assert len(published_messages) == 1

        # Consume and verify
        body = published_messages[0]["fields"]["body"]
        result = await secure_redis_consume(body, key)
        assert result is not None
        assert result["trend_id"] == "t-stream"

    @pytest.mark.asyncio
    async def test_secure_redis_consume_bad_signature(self) -> None:
        from aegis.security.integration import secure_redis_consume, secure_redis_publish

        published_messages: list[dict] = []

        class FakeRedis:
            async def xadd(self, stream: str, fields: dict) -> None:
                published_messages.append(fields)

        redis = FakeRedis()
        payload = {"data": "important"}
        await secure_redis_publish(redis, "test", payload, "key-a")

        body = published_messages[0]["body"]
        # Verify with wrong key
        result = await secure_redis_consume(body, "key-b")
        assert result is None

    @pytest.mark.asyncio
    async def test_audit_db_write(self, tmp_path: Path) -> None:
        from aegis.security.audit.logger import AuditLogger
        from aegis.security.config import SecurityConfig
        from aegis.security.integration import audit_db_write

        cfg = SecurityConfig(
            audit_log_path=str(tmp_path / "db_audit.jsonl"),
            jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"),
            hmac_key=SecretStr("test-hmac-key-not-for-prod-use-ok"),
        )
        async with AuditLogger(config=cfg) as logger:
            await audit_db_write(
                table="signals",
                record_id="sig-123",
                actor="service:scraper",
                outcome="success",
                audit_logger=logger,
                metadata={"platform": "reddit"},
            )

        entry = json.loads((tmp_path / "db_audit.jsonl").read_text().strip())
        assert entry["event"] == "db.signals.write"
        assert entry["resource"] == "signals/sig-123"


# ── JWT token payload properties ──────────────────────────────────────────── #

class TestTokenPayloadProperties:
    def test_issued_at_is_datetime(self) -> None:
        from datetime import datetime

        from aegis.security.config import SecurityConfig
        from aegis.security.tls.jwt_manager import JWTManager

        cfg = SecurityConfig(jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"))
        mgr = JWTManager(config=cfg)
        pair = mgr.issue_tokens(subject="u1", role="viewer")
        payload = mgr.verify_access_token(pair.access_token)

        assert isinstance(payload.issued_at, datetime)
        assert isinstance(payload.expires_at, datetime)
        assert payload.is_expired is False

    def test_multiple_roles_all_valid(self) -> None:
        from aegis.security.config import SecurityConfig
        from aegis.security.tls.jwt_manager import JWTManager

        cfg = SecurityConfig(jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"))
        mgr = JWTManager(config=cfg)

        for role in ["viewer", "analyst", "operator", "admin", "service"]:
            pair = mgr.issue_tokens(subject="user", role=role)
            payload = mgr.verify_access_token(pair.access_token)
            assert payload.role == role


# ── RBAC require_role dependency ─────────────────────────────────────────── #

class TestRBACRequireRole:
    def test_require_role_allows_higher_rank(self, tmp_path: Path) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from aegis.security.config import SecurityConfig
        from aegis.security.rbac.enforcer import RBACEnforcer
        from aegis.security.tls.jwt_manager import JWTManager

        cfg = SecurityConfig(jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"))
        jwt = JWTManager(config=cfg)
        enforcer = RBACEnforcer(jwt_manager=jwt)

        app = FastAPI()

        @app.get("/ops")
        async def ops_endpoint(payload=enforcer.require_role("operator")):
            return {"role": payload.role}

        client = TestClient(app)

        # Admin should be allowed (rank > operator)
        pair = jwt.issue_tokens(subject="admin-user", role="admin")
        resp = client.get("/ops", headers={"Authorization": f"Bearer {pair.access_token}"})
        assert resp.status_code == 200

    def test_require_role_blocks_lower_rank(self, tmp_path: Path) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from aegis.security.config import SecurityConfig
        from aegis.security.rbac.enforcer import RBACEnforcer
        from aegis.security.tls.jwt_manager import JWTManager

        cfg = SecurityConfig(jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"))
        jwt = JWTManager(config=cfg)
        enforcer = RBACEnforcer(jwt_manager=jwt)

        app = FastAPI()

        @app.get("/admin-only")
        async def admin_endpoint(payload=enforcer.require_role("admin")):
            return {"ok": True}

        client = TestClient(app)

        # Viewer should be blocked
        pair = jwt.issue_tokens(subject="viewer-user", role="viewer")
        resp = client.get("/admin-only", headers={"Authorization": f"Bearer {pair.access_token}"})
        assert resp.status_code == 403

    def test_require_role_invalid_role_raises(self) -> None:
        from aegis.security.rbac.enforcer import RBACEnforcer

        enforcer = RBACEnforcer()
        with pytest.raises(ValueError, match="Unknown role"):
            enforcer.require_role("superadmin")


# ── Secrets manager edge cases ────────────────────────────────────────────── #

class TestSecretsManagerEdgeCases:
    @pytest.mark.asyncio
    async def test_get_secret_str_returns_secret_str(self) -> None:
        from pydantic import SecretStr

        from aegis.security.secrets.manager import SecretsManager

        async with SecretsManager() as mgr:
            val = await mgr.get_secret_str("no/such/key", default="fallback")
        assert isinstance(val, SecretStr)
        assert val.get_secret_value() == "fallback"

    @pytest.mark.asyncio
    async def test_fernet_encrypt_bytes_input(self) -> None:
        from aegis.security.secrets.manager import SecretsManager

        async with SecretsManager() as mgr:
            cipher = await mgr.encrypt(b"binary data \x00\x01\x02")
            plain = await mgr.decrypt(cipher)
        assert plain == b"binary data \x00\x01\x02"

    @pytest.mark.asyncio
    async def test_put_without_vault_raises(self) -> None:
        """put() raises when Vault is not connected (vault_client=None)."""
        from aegis.security.secrets.manager import SecretsManager

        # Pass vault_client=None explicitly so SecretsManager starts without Vault
        mgr = SecretsManager(vault_client=None)
        # Prevent auto-connect by patching start()
        mgr._vault = None
        mgr._vault_owned = False
        mgr._sops_loaded = True
        with pytest.raises(RuntimeError, match="AEGIS-SEC-0022"):
            await mgr.put("some/key", {"value": "data"})

    def test_to_env_key_special_chars(self) -> None:
        from aegis.security.secrets.manager import _to_env_key

        assert _to_env_key("llm/groq-api-key") == "LLM_GROQ_API_KEY"
        assert _to_env_key("phase3/model-v2/weight") == "PHASE3_MODEL_V2_WEIGHT"


# ── Audit logger concurrent safety ───────────────────────────────────────── #

class TestAuditLoggerConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_log_calls_sequential_seqs(self, tmp_path: Path) -> None:
        from aegis.security.audit.logger import AuditLogger
        from aegis.security.config import SecurityConfig

        cfg = SecurityConfig(
            audit_log_path=str(tmp_path / "concurrent_audit.jsonl"),
            jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"),
            hmac_key=SecretStr("test-hmac-key-not-for-prod-use-ok"),
        )
        logger = AuditLogger(config=cfg)
        async with logger:
            # Fire 10 concurrent log calls
            tasks = [
                logger.log(f"event.{i}", actor=f"user:{i}")
                for i in range(10)
            ]
            results = await asyncio.gather(*tasks)

        seqs = sorted(r["seq"] for r in results)
        # Should be 1..10 with no duplicates
        assert seqs == list(range(1, 11))

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self, tmp_path: Path) -> None:
        from aegis.security.audit.logger import AuditLogger
        from aegis.security.config import SecurityConfig

        cfg = SecurityConfig(
            audit_log_path=str(tmp_path / "stop_audit.jsonl"),
            jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"),
            hmac_key=SecretStr("test-hmac-key-not-for-prod-use-ok"),
        )
        logger = AuditLogger(config=cfg)
        await logger.start()
        await logger.log("event.one")
        await logger.stop()
        # Second stop should not raise
        await logger.stop()


# ── PII scrubber edge cases ───────────────────────────────────────────────── #

class TestPIIScrubberEdgeCases:
    def _make(self):
        from aegis.security.config import SecurityConfig
        from aegis.security.pii.scrubber import PIIScrubber

        cfg = SecurityConfig()
        s = PIIScrubber(config=cfg)
        s._spacy_available = False
        return s

    def test_empty_string_unchanged(self) -> None:
        s = self._make()
        result, report = s.scrub_text("")
        assert result == ""
        assert not report.was_modified

    def test_list_field_scrubbed(self) -> None:
        s = self._make()
        data = {"tags": ["alice@test.com", "normal-tag", "bob@test.org"]}
        result, report = s.scrub_dict(data)
        assert "alice@test.com" not in result["tags"]
        assert "bob@test.org" not in result["tags"]
        assert "normal-tag" in result["tags"]

    def test_nested_list_not_recursed_into_dicts(self) -> None:
        """Non-string list items pass through unchanged."""
        s = self._make()
        data = {"scores": [0.1, 0.5, 0.9]}
        result, _ = s.scrub_dict(data)
        assert result["scores"] == [0.1, 0.5, 0.9]

    def test_pan_card_redacted(self) -> None:
        s = self._make()
        result, report = s.scrub_text("PAN: ABCDE1234F for tax purposes")
        assert "ABCDE1234F" not in result
        assert report.replacements.get("pan_in", 0) >= 1

    def test_scrub_report_properties(self) -> None:
        from aegis.security.pii.scrubber import ScrubReport

        report = ScrubReport(
            original_length=100,
            scrubbed_length=80,
            replacements={"email": 2, "phone_e164": 1},
            ner_entities_removed=1,
        )
        assert report.total_replacements == 3
        assert report.was_modified is True

    def test_scrub_report_unmodified(self) -> None:
        from aegis.security.pii.scrubber import ScrubReport

        report = ScrubReport(original_length=50, scrubbed_length=50)
        assert report.total_replacements == 0
        assert report.was_modified is False

    def test_scrub_dict_restricted_fields(self) -> None:
        """Only scrub fields in text_fields set."""
        s = self._make()
        data = {
            "title": "Contact alice@test.com",
            "url": "http://site.com?user=bob@test.org",
            "score": 0.9,
        }
        result, report = s.scrub_dict(data, text_fields={"title"})
        # title should be scrubbed
        assert "alice@test.com" not in result["title"]
        # url was not in text_fields - should be untouched
        assert result["url"] == data["url"]
