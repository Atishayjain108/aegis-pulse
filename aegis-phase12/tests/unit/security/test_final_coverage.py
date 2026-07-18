"""
tests.unit.security.test_final_coverage — Final coverage targets.

Covers:
    - vault/bootstrap.py    : VaultBootstrap steps with mocked requests
    - audit/logger.py       : upload worker, load_state from existing file
    - cli.py                : key functions via direct invocation (not subprocess)
    - scanner.py            : create_baseline, directory exclude patterns
    - secrets/manager.py    : SOPS load path, vault ciphertext decrypt
    - middleware/ratelimit   : 429 response path
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

# ── Vault Bootstrap ───────────────────────────────────────────────────────── #

class TestVaultBootstrap:
    def _make_vault_mock(self) -> Any:
        vault = MagicMock()
        vault._cfg = MagicMock()
        vault._cfg.vault_transit_key = "aegis-key"
        vault._cfg.vault_mount_kv = "secret"
        vault._cfg.vault_mount_transit = "transit"
        vault._request = AsyncMock(return_value={})
        vault.read_secret = AsyncMock(side_effect=Exception("not found"))
        vault.write_secret = AsyncMock(return_value=1)
        return vault

    @pytest.mark.asyncio
    async def test_bootstrap_run_returns_results(self) -> None:
        from aegis.security.vault.bootstrap import VaultBootstrap

        vault = self._make_vault_mock()
        bs = VaultBootstrap(vault, overwrite_existing=True)
        results = await bs.run()
        assert isinstance(results, list)
        assert len(results) > 0
        steps = [r["step"] for r in results]
        assert "enable_kv_v2" in steps
        assert "enable_transit" in steps

    @pytest.mark.asyncio
    async def test_bootstrap_handles_already_enabled(self) -> None:
        from aegis.security.vault._errors import VaultError, VaultErrorCode
        from aegis.security.vault.bootstrap import VaultBootstrap

        vault = self._make_vault_mock()
        # Simulate "path is already in use" error for mount calls
        # but allow write_secret to succeed (overwrite_existing=True bypasses read_secret)
        vault._request = AsyncMock(
            side_effect=VaultError(VaultErrorCode.API_ERROR, "path is already in use", status=400)
        )
        vault.write_secret = AsyncMock(return_value=1)
        bs = VaultBootstrap(vault, overwrite_existing=True)  # skip read_secret check
        results = await bs.run()
        skipped = [r for r in results if r["status"] == "skipped"]
        assert len(skipped) >= 2  # kv and transit both skipped

    @pytest.mark.asyncio
    async def test_bootstrap_skips_existing_secrets(self) -> None:
        from aegis.security.vault.bootstrap import VaultBootstrap

        vault = self._make_vault_mock()
        # Simulate secrets already existing
        mock_secret = MagicMock()
        mock_secret.version = 1
        vault.read_secret = AsyncMock(return_value=mock_secret)
        vault._request = AsyncMock(return_value={})

        bs = VaultBootstrap(vault, overwrite_existing=False)
        results = await bs.run()
        skipped = [r for r in results if r["status"] == "skipped" and "secret" in r["step"]]
        assert len(skipped) > 0

    @pytest.mark.asyncio
    async def test_bootstrap_overwrite_writes_secrets(self) -> None:
        from aegis.security.vault.bootstrap import VaultBootstrap

        vault = self._make_vault_mock()
        vault._request = AsyncMock(return_value={})
        vault.write_secret = AsyncMock(return_value=2)

        bs = VaultBootstrap(vault, overwrite_existing=True)
        results = await bs.run()
        ok_secrets = [r for r in results if r["status"] == "ok" and "secret" in r["step"]]
        assert len(ok_secrets) > 0

    @pytest.mark.asyncio
    async def test_bootstrap_convenience_function(self) -> None:
        from aegis.security.vault.bootstrap import bootstrap_vault

        vault = self._make_vault_mock()
        vault._request = AsyncMock(return_value={})
        results = await bootstrap_vault(vault, overwrite_existing=True)
        assert isinstance(results, list)


# ── Audit Logger Upload + Load State ─────────────────────────────────────── #

class TestAuditLoggerAdvanced:
    @pytest.mark.asyncio
    async def test_load_state_from_existing_file(self, tmp_path: Path) -> None:
        """Logger resuming after restart should pick up seq from disk."""
        from aegis.security.audit.logger import AuditLogger
        from aegis.security.config import SecurityConfig

        cfg = SecurityConfig(
            audit_log_path=str(tmp_path / "resume.jsonl"),
            jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"),
            hmac_key=SecretStr("test-hmac-key-not-for-prod-use-ok"),
        )
        # First logger: write 3 entries
        async with AuditLogger(config=cfg) as logger1:
            for i in range(3):
                await logger1.log(f"event.{i}")

        # Second logger: should resume at seq=4
        logger2 = AuditLogger(config=cfg)
        await logger2.start()
        e = await logger2.log("event.resumed")
        await logger2.stop()

        assert e["seq"] == 4

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, tmp_path: Path) -> None:
        from aegis.security.audit.logger import AuditLogger
        from aegis.security.config import SecurityConfig

        cfg = SecurityConfig(
            audit_log_path=str(tmp_path / "idem.jsonl"),
            jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"),
            hmac_key=SecretStr("test-hmac-key-not-for-prod-use-ok"),
        )
        logger = AuditLogger(config=cfg)
        await logger.start()
        await logger.start()  # second start should be no-op
        await logger.log("event.x")
        await logger.stop()

    @pytest.mark.asyncio
    async def test_upload_queue_full_logs_warning(self, tmp_path: Path) -> None:
        """Queue full condition should not crash the logger."""
        from aegis.security.audit.logger import AuditLogger
        from aegis.security.config import SecurityConfig

        cfg = SecurityConfig(
            audit_log_path=str(tmp_path / "overflow.jsonl"),
            jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"),
            hmac_key=SecretStr("test-hmac-key-not-for-prod-use-ok"),
        )
        logger = AuditLogger(config=cfg)
        await logger.start()
        # Fill the queue to capacity and beyond
        logger._upload_queue = asyncio.Queue(maxsize=1)
        # These should not raise even when queue is full
        for _ in range(5):
            await logger.log("event.overflow")
        await logger.stop()


# ── Middleware Rate Limit 429 path ────────────────────────────────────────── #

class TestRateLimitMiddleware429:
    @pytest.mark.asyncio
    async def test_429_returned_when_tokens_exhausted(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from aegis.security.config import SecurityConfig
        from aegis.security.middleware.ratelimit import RateLimitMiddleware

        cfg = SecurityConfig(rate_limit_enabled=True, rate_limit_default_rpm=60, rate_limit_burst=3)

        # Mock Redis that always returns -1 (denied)
        redis_mock = AsyncMock()
        redis_mock.script_load = AsyncMock(return_value="sha_test")
        redis_mock.evalsha = AsyncMock(return_value=[-1, 3000])

        app = FastAPI()
        app.add_middleware(
            RateLimitMiddleware,
            config=cfg,
            redis_client=redis_mock,
        )

        @app.get("/limited")
        async def limited():
            return {"ok": True}

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/limited")
        assert resp.status_code == 429
        body = resp.json()
        assert body["error_code"] == "AEGIS-SEC-0081"
        assert "retry_after_s" in body
        assert "Retry-After" in resp.headers
        assert "X-RateLimit-Remaining" in resp.headers
        assert resp.headers["X-RateLimit-Remaining"] == "0"

    @pytest.mark.asyncio
    async def test_rate_limit_headers_on_allowed_request(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from aegis.security.config import SecurityConfig
        from aegis.security.middleware.ratelimit import RateLimitMiddleware

        cfg = SecurityConfig(rate_limit_enabled=True)
        redis_mock = AsyncMock()
        redis_mock.script_load = AsyncMock(return_value="sha")
        redis_mock.evalsha = AsyncMock(return_value=[59, 1000])

        app = FastAPI()
        app.add_middleware(RateLimitMiddleware, config=cfg, redis_client=redis_mock)

        @app.get("/ok")
        async def ok():
            return {"ok": True}

        client = TestClient(app)
        resp = client.get("/ok")
        assert resp.status_code == 200
        assert resp.headers["X-RateLimit-Remaining"] == "59"


# ── Secrets Manager SOPS + vault ciphertext paths ────────────────────────── #

class TestSecretsManagerAdvanced:
    @pytest.mark.asyncio
    async def test_vault_ciphertext_without_vault_raises(self) -> None:
        from aegis.security.secrets.manager import SecretsManager

        mgr = SecretsManager()
        mgr._vault = None
        mgr._vault_owned = False
        mgr._sops_loaded = True

        with pytest.raises(RuntimeError, match="AEGIS-SEC-0023"):
            await mgr.decrypt("vault:v1:someciphertext")

    @pytest.mark.asyncio
    async def test_sops_load_file_not_found(self, tmp_path: Path) -> None:
        """SOPS load should silently succeed when file doesn't exist."""
        from aegis.security.config import SecurityConfig
        from aegis.security.secrets.manager import SecretsManager

        cfg = SecurityConfig(sops_config_path=str(tmp_path / ".sops.yaml"))
        mgr = SecretsManager(config=cfg)
        # Should not raise
        await mgr._load_sops()
        assert mgr._sops_loaded is True

    @pytest.mark.asyncio
    async def test_sops_cache_hit(self, tmp_path: Path) -> None:
        """Values from SOPS cache should be returned before env fallback."""
        from aegis.security.secrets.manager import SecretsManager

        mgr = SecretsManager()
        mgr._sops_loaded = True
        mgr._sops_cache["MY_TEST_KEY"] = "sops-cached-value"
        mgr._vault = None
        mgr._vault_owned = False

        val = await mgr.get("my/test/key")
        assert val == "sops-cached-value"

    @pytest.mark.asyncio
    async def test_get_with_env_var_fallback(self, monkeypatch) -> None:
        from aegis.security.secrets.manager import SecretsManager

        monkeypatch.setenv("CUSTOM_SECRET_KEY", "env-value-123")
        mgr = SecretsManager()
        mgr._vault = None
        mgr._vault_owned = False
        mgr._sops_loaded = True

        val = await mgr.get("custom/secret/key")
        assert val == "env-value-123"


# ── CLI functions (direct call, not subprocess) ───────────────────────────── #

class TestCLIFunctions:
    def test_check_pii_function(self) -> None:
        from aegis.security.cli import _check_pii
        ok, detail = _check_pii()
        assert ok is True
        assert "detected" in detail.lower()

    def test_check_jwt_function(self) -> None:
        from aegis.security.cli import _check_jwt
        os.environ.setdefault("AEGIS_SEC_JWT_SECRET", "test-secret-key-at-least-32-chars!!")
        from aegis.security.config import get_security_config
        get_security_config.cache_clear()
        ok, detail = _check_jwt()
        assert ok is True
        assert "OK" in detail

    def test_check_rbac_function(self) -> None:
        from aegis.security.cli import _check_rbac
        ok, detail = _check_rbac()
        assert ok is True

    def test_check_tool_found(self) -> None:
        from aegis.security.cli import _check_tool
        ok, detail = _check_tool("python3")
        assert ok is True
        assert "python3" in detail or "Found" in detail

    def test_check_tool_not_found(self) -> None:
        from aegis.security.cli import _check_tool
        ok, detail = _check_tool("definitely-not-a-real-tool-xyz")
        assert ok is False

    def test_check_audit_log_writable(self, tmp_path: Path) -> None:
        from aegis.security.cli import _check_audit_log
        from aegis.security.config import get_security_config

        get_security_config.cache_clear()
        with patch.dict(os.environ, {"AEGIS_SEC_AUDIT_LOG_PATH": str(tmp_path / "audit.jsonl")}):
            get_security_config.cache_clear()
            ok, detail = _check_audit_log()
        assert ok is True
        get_security_config.cache_clear()

    def test_check_env_vars_missing(self) -> None:
        import os

        from aegis.security.cli import _check_env_vars

        saved_jwt = os.environ.pop("AEGIS_SEC_JWT_SECRET", None)
        saved_hmac = os.environ.pop("AEGIS_SEC_HMAC_KEY", None)
        try:
            ok, detail = _check_env_vars()
            assert ok is False
            assert "Missing" in detail
        finally:
            if saved_jwt:
                os.environ["AEGIS_SEC_JWT_SECRET"] = saved_jwt
            if saved_hmac:
                os.environ["AEGIS_SEC_HMAC_KEY"] = saved_hmac

    def test_check_env_vars_present(self) -> None:
        from aegis.security.cli import _check_env_vars

        with patch.dict(os.environ, {
            "AEGIS_SEC_JWT_SECRET": "a" * 32,
            "AEGIS_SEC_HMAC_KEY": "b" * 32,
        }):
            ok, detail = _check_env_vars()
        assert ok is True


# ── Scanner: create_baseline + directory excludes ─────────────────────────── #

class TestScannerAdvanced:
    def test_create_baseline_no_tool(self, tmp_path: Path) -> None:
        from aegis.security.scanner import SecretScanner

        s = SecretScanner()
        with patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="not installed"):
                s.create_baseline(tmp_path)

    def test_create_baseline_with_tool(self, tmp_path: Path) -> None:
        from aegis.security.scanner import SecretScanner

        (tmp_path / "code.py").write_text("x = 1")
        mock_proc = MagicMock()
        mock_proc.stdout = '{"results": {}, "version": "1.5.0"}'

        with patch("shutil.which", return_value="/usr/bin/detect-secrets"), \
             patch("subprocess.run", return_value=mock_proc):
            s = SecretScanner()
            baseline = s.create_baseline(tmp_path)
        assert baseline.exists()

    def test_directory_excludes_test_files(self, tmp_path: Path) -> None:
        """Files matching exclude patterns should not be scanned."""
        import re

        from aegis.security.scanner import SecretScanner

        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "src" / "app.py").write_text("x = 1")
        (tmp_path / "tests" / "test_app.py").write_text("y = 2")

        s = SecretScanner()
        exclude_re = [re.compile(p) for p in s._exclude]

        all_files = list((tmp_path).rglob("*.py"))
        filtered = [
            f for f in all_files
            if not any(pat.search(str(f.relative_to(tmp_path))) for pat in exclude_re)
        ]
        # tests/ should be excluded
        assert any("app.py" in str(f) for f in filtered)
        assert not any("test_app.py" in str(f) for f in filtered)

    def test_run_bandit_success(self, tmp_path: Path) -> None:
        from aegis.security.scanner import SecretScanner

        mock_proc = MagicMock()
        mock_proc.stdout = json.dumps({"results": [], "metrics": {"_totals": {"SEVERITY.HIGH": 0}}})

        with patch("shutil.which", return_value="/usr/bin/bandit"), \
             patch("subprocess.run", return_value=mock_proc):
            s = SecretScanner()
            result = s.run_bandit(tmp_path)
        assert "results" in result

    def test_scan_container_image_success(self) -> None:
        from aegis.security.scanner import SecretScanner

        mock_proc = MagicMock()
        mock_proc.stdout = json.dumps({"Results": [], "SchemaVersion": 2})

        with patch("shutil.which", return_value="/usr/bin/trivy"), \
             patch("subprocess.run", return_value=mock_proc):
            s = SecretScanner()
            result = s.scan_container_image("python:3.12-slim")
        assert "Results" in result


# ── Integration module advanced paths ────────────────────────────────────── #

class TestIntegrationAdvanced:
    @pytest.mark.asyncio
    async def test_audit_db_write_no_logger(self) -> None:
        from aegis.security.integration import audit_db_write
        # Should be a no-op, not raise
        await audit_db_write(
            table="signals",
            record_id="abc-123",
            actor="service:test",
            audit_logger=None,
        )

    @pytest.mark.asyncio
    async def test_secure_redis_consume_no_verify(self) -> None:
        import json

        from aegis.security.integration import secure_redis_consume

        payload = {"data": "test", "signature": "ignored"}
        body = json.dumps(payload)
        result = await secure_redis_consume(body, "anykey", verify_signature=False)
        assert result is not None
        assert result["data"] == "test"

    @pytest.mark.asyncio
    async def test_secure_redis_consume_invalid_json(self) -> None:
        from aegis.security.integration import secure_redis_consume
        result = await secure_redis_consume("not-json{{{{", "key")
        assert result is None

    @pytest.mark.asyncio
    async def test_secure_redis_consume_missing_signature(self) -> None:
        import json

        from aegis.security.integration import secure_redis_consume

        body = json.dumps({"data": "no sig here"})
        result = await secure_redis_consume(body, "key", verify_signature=True)
        assert result is None
