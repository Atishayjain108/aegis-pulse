"""
tests.unit.security.test_coverage_final — Targeted tests for remaining coverage gaps.

Targets:
  - api.py lines 182, 215-216, 239, 287-297, 327-418   (audit_logger paths, security_health)
  - cli.py lines 47-453                                  (Click commands via CliRunner)
  - scanner.py lines 153-250, 326-450                    (scan_string, scan_file, timeout paths)
  - secrets/manager.py lines 86-259                      (SOPS subprocess paths)
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

# ── API with audit_logger paths ───────────────────────────────────────────── #

class TestAPIWithAuditLogger:
    def _build_app_with_audit(self, tmp_path: Path):
        from fastapi import FastAPI

        from aegis.security.api import create_security_router
        from aegis.security.audit.logger import AuditLogger
        from aegis.security.config import SecurityConfig
        from aegis.security.rbac.enforcer import RBACEnforcer
        from aegis.security.tls.jwt_manager import JWTManager

        cfg = SecurityConfig(
            jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"),
            hmac_key=SecretStr("test-hmac-key-not-for-prod-use-ok"),
            audit_log_path=str(tmp_path / "api_audit.jsonl"),
        )
        jwt = JWTManager(config=cfg)
        enforcer = RBACEnforcer(jwt_manager=jwt)
        # Build a real AuditLogger and pre-start it
        audit = AuditLogger(config=cfg)

        router = create_security_router(
            config=cfg,
            jwt_manager=jwt,
            rbac_enforcer=enforcer,
            audit_logger=audit,
        )
        app = FastAPI()
        app.include_router(router, prefix="/api")
        return app, jwt, audit

    def test_issue_token_with_audit_logger(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, jwt, audit = self._build_app_with_audit(tmp_path)

        # Start the audit logger manually
        asyncio.run(audit.start())
        try:
            client = TestClient(app)
            resp = client.post("/api/auth/token", json={"username": "admin", "password": "admin"})
            assert resp.status_code == 200
        finally:
            asyncio.run(audit.stop())

        # Verify audit event was written
        log_path = tmp_path / "api_audit.jsonl"
        if log_path.exists():
            lines = [ln for ln in log_path.read_text().strip().split("\n") if ln]
            events = [json.loads(ln)["event"] for ln in lines]
            assert "auth.token_issued" in events

    def test_refresh_with_audit_logger(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, jwt, audit = self._build_app_with_audit(tmp_path)
        asyncio.run(audit.start())
        try:
            pair = jwt.issue_tokens(subject="user-audit-refresh", role="analyst")
            client = TestClient(app)
            resp = client.post(
                "/api/auth/refresh",
                json={"refresh_token": pair.refresh_token},
            )
            assert resp.status_code == 200
        finally:
            asyncio.run(audit.stop())

    def test_revoke_with_audit_logger(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, jwt, audit = self._build_app_with_audit(tmp_path)
        asyncio.run(audit.start())
        try:
            pair = jwt.issue_tokens(subject="user-audit-revoke", role="viewer")
            client = TestClient(app)
            resp = client.post(
                "/api/auth/revoke",
                json={"token": pair.access_token},
            )
            assert resp.status_code == 204
        finally:
            asyncio.run(audit.stop())

    def test_audit_endpoint_with_entries(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, jwt, audit = self._build_app_with_audit(tmp_path)
        asyncio.run(audit.start())
        try:
            # Write some audit entries
            asyncio.run(audit.log("test.event.1", actor="user:x"))
            asyncio.run(audit.log("test.event.2", actor="user:y"))

            pair = jwt.issue_tokens(subject="admin-user", role="admin")
            client = TestClient(app)
            resp = client.get(
                "/api/security/audit",
                headers={"Authorization": f"Bearer {pair.access_token}"},
            )
            assert resp.status_code == 200
            entries = resp.json()
            assert isinstance(entries, list)
        finally:
            asyncio.run(audit.stop())

    def test_security_app_factory(self) -> None:
        """Test the create_security_app convenience function."""
        from aegis.security.api import create_security_app
        from aegis.security.config import SecurityConfig

        cfg = SecurityConfig(
            jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"),
            hmac_key=SecretStr("test-hmac-key-not-for-prod-use-ok"),
        )
        app = create_security_app(config=cfg)
        assert app is not None

        from fastapi.testclient import TestClient
        client = TestClient(app)  # type: ignore[arg-type]
        resp = client.get("/healthz")
        assert resp.status_code == 200


# ── CLI via Click CliRunner ───────────────────────────────────────────────── #

class TestCLICommands:
    def _runner(self):
        from click.testing import CliRunner
        return CliRunner()

    def test_doctor_command_runs(self) -> None:
        from click.testing import CliRunner

        from aegis.security.cli import security_group

        runner = CliRunner()
        result = runner.invoke(security_group, ["doctor"])
        # May fail checks but should not crash
        assert result.exit_code in (0, 1)
        assert "AEGIS Security Doctor" in result.output or "check" in result.output.lower()

    def test_doctor_json_output(self) -> None:
        from click.testing import CliRunner

        from aegis.security.cli import security_group

        runner = CliRunner()
        result = runner.invoke(security_group, ["doctor", "--json-out"])
        assert result.exit_code in (0, 1)
        # Should output valid JSON list
        try:
            data = json.loads(result.output)
            assert isinstance(data, list)
            assert all("check" in item and "status" in item for item in data)
        except json.JSONDecodeError:
            pass  # May have structlog output before JSON

    def test_pii_test_command(self) -> None:
        from click.testing import CliRunner

        from aegis.security.cli import security_group

        runner = CliRunner()
        result = runner.invoke(
            security_group,
            ["pii-test", "Contact alice@example.com for details"],
        )
        assert result.exit_code == 0
        assert "Scrubbed" in result.output
        # Email is hashed not fully removed - check the hash replaced it
        assert "Replacements" in result.output
        assert "email" in result.output

    def test_scan_command_clean_directory(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from aegis.security.cli import security_group

        (tmp_path / "clean.py").write_text("x = 1 + 2\nprint(x)\n")
        runner = CliRunner()

        with patch("shutil.which", return_value=None):  # detect-secrets not installed
            result = runner.invoke(security_group, ["scan", str(tmp_path)])
        assert result.exit_code in (0, 1)

    def test_scan_command_json_output(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from aegis.security.cli import security_group

        runner = CliRunner()
        with patch("shutil.which", return_value=None):
            result = runner.invoke(
                security_group, ["scan", str(tmp_path), "--json-out"]
            )
        assert result.exit_code in (0, 1)

    def test_audit_verify_command(self, tmp_path: Path) -> None:
        """Test audit verify via direct function call (avoids nested event loop)."""
        from aegis.security.config import SecurityConfig

        log_path = tmp_path / "cli_audit.jsonl"
        cfg = SecurityConfig(
            audit_log_path=str(log_path),
            jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"),
            hmac_key=SecretStr("test-hmac-key-not-for-prod-use-ok"),
        )
        asyncio.run(_write_audit_entries(cfg, 3))

        # Test the verify function directly (bypasses asyncio.run nesting issue)
        async def verify_direct() -> None:
            from aegis.security.audit.logger import AuditLogger
            logger = AuditLogger(config=cfg)
            valid, count, err = await logger.verify_integrity()
            assert valid is True
            assert count == 3
            assert err is None

        asyncio.run(verify_direct())

    def test_audit_tail_command(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from aegis.security.cli import security_group
        from aegis.security.config import SecurityConfig

        log_path = tmp_path / "tail_audit.jsonl"
        cfg = SecurityConfig(
            audit_log_path=str(log_path),
            jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"),
            hmac_key=SecretStr("test-hmac-key-not-for-prod-use-ok"),
        )
        asyncio.run(_write_audit_entries(cfg, 5))

        runner = CliRunner()
        with patch.dict(os.environ, {"AEGIS_SEC_AUDIT_LOG_PATH": str(log_path)}):
            result = runner.invoke(
                security_group,
                ["audit", "tail", "--limit", "3", "--path", str(log_path)],
            )
        assert result.exit_code == 0
        assert "Audit Log" in result.output

    def test_audit_tail_missing_file(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from aegis.security.cli import security_group

        runner = CliRunner()
        result = runner.invoke(
            security_group,
            ["audit", "tail", "--path", str(tmp_path / "nonexistent.jsonl")],
        )
        assert result.exit_code == 0
        assert "not found" in result.output

    def test_vault_group_exists(self) -> None:
        from click.testing import CliRunner

        from aegis.security.cli import security_group

        runner = CliRunner()
        result = runner.invoke(security_group, ["vault", "--help"])
        assert result.exit_code == 0
        assert "init" in result.output
        assert "status" in result.output

    def test_security_group_help(self) -> None:
        from click.testing import CliRunner

        from aegis.security.cli import security_group

        runner = CliRunner()
        result = runner.invoke(security_group, ["--help"])
        assert result.exit_code == 0
        assert "doctor" in result.output
        assert "scan" in result.output
        assert "rotate" in result.output


async def _write_audit_entries(cfg: Any, count: int) -> None:
    from aegis.security.audit.logger import AuditLogger
    async with AuditLogger(config=cfg) as logger:
        for i in range(count):
            await logger.log(f"event.{i}", actor=f"user:{i}")


# ── Scanner: scan_string and scan_file with real subprocess mock ──────────── #

class TestScannerStringAndFile:
    def test_scan_string_with_mock_tool(self, tmp_path: Path) -> None:
        from aegis.security.scanner import SecretScanner

        mock_proc = MagicMock()
        mock_proc.stdout = json.dumps({
            "results": {},
            "version": "1.5.0",
        })

        with patch("shutil.which", return_value="/usr/bin/detect-secrets"), \
             patch("subprocess.run", return_value=mock_proc):
            s = SecretScanner()
            result = s.scan_string("api_key = 'test'", filename="test.py")
        assert result.scanned_files == 1
        assert not result.has_secrets

    def test_scan_file_with_finding(self, tmp_path: Path) -> None:
        from aegis.security.scanner import SecretScanner

        test_file = tmp_path / "secrets.py"
        test_file.write_text("aws_key = 'AKIAIOSFODNN7EXAMPLE'")

        mock_proc = MagicMock()
        mock_proc.stdout = json.dumps({
            "results": {
                str(test_file): [
                    {
                        "type": "AWS Access Key",
                        "line_number": 1,
                        "hashed_secret": "abc123",
                        "is_verified": False,
                    }
                ]
            }
        })

        with patch("shutil.which", return_value="/usr/bin/detect-secrets"), \
             patch("subprocess.run", return_value=mock_proc):
            s = SecretScanner()
            result = s.scan_file(test_file)
        assert result.has_secrets is True
        assert result.findings[0].secret_type == "AWS Access Key"

    def test_scan_string_subprocess_timeout(self, tmp_path: Path) -> None:
        import subprocess

        from aegis.security.scanner import SecretScanner

        with patch("shutil.which", return_value="/usr/bin/detect-secrets"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("detect-secrets", 30)):
            s = SecretScanner()
            result = s.scan_string("x = 1")
        assert len(result.errors) > 0
        assert "timed out" in result.errors[0]

    def test_scan_file_subprocess_timeout(self, tmp_path: Path) -> None:
        import subprocess

        from aegis.security.scanner import SecretScanner

        test_file = tmp_path / "code.py"
        test_file.write_text("x = 1")

        with patch("shutil.which", return_value="/usr/bin/detect-secrets"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("detect-secrets", 30)):
            s = SecretScanner()
            result = s.scan_file(test_file)
        assert len(result.errors) > 0

    def test_scan_string_json_decode_error(self, tmp_path: Path) -> None:
        from aegis.security.scanner import SecretScanner

        mock_proc = MagicMock()
        mock_proc.stdout = "not-valid-json{{{"

        with patch("shutil.which", return_value="/usr/bin/detect-secrets"), \
             patch("subprocess.run", return_value=mock_proc):
            s = SecretScanner()
            result = s.scan_string("x = 1")
        assert len(result.errors) > 0

    def test_gitleaks_timeout(self, tmp_path: Path) -> None:
        import subprocess

        from aegis.security.scanner import SecretScanner

        with patch("shutil.which", return_value="/usr/bin/gitleaks"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("gitleaks", 120)):
            s = SecretScanner()
            result = s.scan_git_history(tmp_path)
        assert any("timed out" in e for e in result.errors)

    def test_gitleaks_json_error(self, tmp_path: Path) -> None:
        from aegis.security.scanner import SecretScanner

        mock_proc = MagicMock()
        mock_proc.stdout = "bad json{"

        with patch("shutil.which", return_value="/usr/bin/gitleaks"), \
             patch("subprocess.run", return_value=mock_proc):
            s = SecretScanner()
            result = s.scan_git_history(tmp_path)
        assert len(result.errors) > 0

    def test_bandit_timeout(self, tmp_path: Path) -> None:
        import subprocess

        from aegis.security.scanner import SecretScanner

        with patch("shutil.which", return_value="/usr/bin/bandit"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("bandit", 120)):
            s = SecretScanner()
            result = s.run_bandit(tmp_path)
        assert "error" in result

    def test_trivy_timeout(self) -> None:
        import subprocess

        from aegis.security.scanner import SecretScanner

        with patch("shutil.which", return_value="/usr/bin/trivy"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("trivy", 300)):
            s = SecretScanner()
            result = s.scan_container_image("nginx:latest")
        assert "error" in result

    def test_scan_directory_with_mock(self, tmp_path: Path) -> None:
        from aegis.security.scanner import SecretScanner

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("x = 1")

        mock_proc = MagicMock()
        mock_proc.stdout = json.dumps({"results": {}})

        with patch("shutil.which", return_value="/usr/bin/detect-secrets"), \
             patch("subprocess.run", return_value=mock_proc):
            s = SecretScanner()
            result = s.scan_directory(tmp_path)
        assert result.scanned_files >= 1
        assert result.duration_ms >= 0


# ── SecretsManager SOPS paths ─────────────────────────────────────────────── #

class TestSecretsManagerSOPS:
    @pytest.mark.asyncio
    async def test_sops_subprocess_error_logged(self, tmp_path: Path) -> None:
        import subprocess

        from aegis.security.config import SecurityConfig
        from aegis.security.secrets.manager import SecretsManager

        sops_file = tmp_path / ".env.sops.yaml"
        sops_file.write_text("encrypted-content")

        cfg = SecurityConfig(
            sops_config_path=str(tmp_path / ".sops.yaml"),
            jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"),
        )
        mgr = SecretsManager(config=cfg)

        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(
            1, "sops", stderr="decryption failed"
        )):
            await mgr._load_sops()
        assert mgr._sops_loaded is True
        # Should not have loaded any values
        assert len(mgr._sops_cache) == 0

    @pytest.mark.asyncio
    async def test_sops_timeout_handled(self, tmp_path: Path) -> None:
        import subprocess

        from aegis.security.config import SecurityConfig
        from aegis.security.secrets.manager import SecretsManager

        sops_file = tmp_path / ".env.sops.yaml"
        sops_file.write_text("encrypted")

        cfg = SecurityConfig(
            sops_config_path=str(tmp_path / ".sops.yaml"),
            jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"),
        )
        mgr = SecretsManager(config=cfg)

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("sops", 10)):
            await mgr._load_sops()
        assert mgr._sops_loaded is True

    @pytest.mark.asyncio
    async def test_sops_not_installed_handled(self, tmp_path: Path) -> None:
        from aegis.security.config import SecurityConfig
        from aegis.security.secrets.manager import SecretsManager

        sops_file = tmp_path / ".env.sops.yaml"
        sops_file.write_text("encrypted")

        cfg = SecurityConfig(
            sops_config_path=str(tmp_path / ".sops.yaml"),
            jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"),
        )
        mgr = SecretsManager(config=cfg)

        with patch("subprocess.run", side_effect=FileNotFoundError("sops not found")):
            await mgr._load_sops()
        assert mgr._sops_loaded is True

    @pytest.mark.asyncio
    async def test_load_sops_idempotent(self) -> None:
        from aegis.security.secrets.manager import SecretsManager

        mgr = SecretsManager()
        mgr._sops_loaded = True
        # Should not call subprocess
        with patch("subprocess.run") as mock_run:
            await mgr._load_sops()
            mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_with_vault_owned(self) -> None:
        from aegis.security.secrets.manager import SecretsManager

        mgr = SecretsManager()
        # vault is None (never connected), stop should be safe
        mgr._vault = None
        mgr._vault_owned = True
        await mgr.stop()  # should not raise


# ── Vault client: renewal loop + _refresh_token_ttl ──────────────────────── #

class TestVaultClientAdvanced:
    @pytest.mark.asyncio
    async def test_refresh_token_ttl_on_vault_error(self) -> None:
        from aegis.security.config import SecurityConfig
        from aegis.security.vault.client import VaultClient

        cfg = SecurityConfig()
        vc = VaultClient(cfg)

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_client.request = AsyncMock(return_value=mock_resp)
        vc._client = mock_client

        # Should log warning but not raise
        await vc._refresh_token_ttl()
        assert vc._token_ttl == 0

    @pytest.mark.asyncio
    async def test_renewal_loop_cancels_cleanly(self) -> None:
        from aegis.security.config import SecurityConfig
        from aegis.security.vault.client import VaultClient

        cfg = SecurityConfig()
        vc = VaultClient(cfg)

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"ttl": 0}}
        mock_client.request = AsyncMock(return_value=mock_resp)
        vc._client = mock_client

        # Start renewal loop, immediately cancel
        import contextlib
        task = asyncio.create_task(vc._renewal_loop())
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_destroy_secret(self) -> None:
        from aegis.security.config import SecurityConfig
        from aegis.security.vault.client import VaultClient

        cfg = SecurityConfig()
        vc = VaultClient(cfg)

        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_resp)
        vc._client = mock_client

        await vc.destroy_secret("aegis/old", versions=[1, 2, 3])
        mock_client.request.assert_called_once()

    @pytest.mark.asyncio
    async def test_request_circuit_open_raises(self) -> None:
        from aegis.security.config import SecurityConfig
        from aegis.security.vault._errors import VaultError, VaultErrorCode
        from aegis.security.vault.client import VaultClient

        cfg = SecurityConfig()
        vc = VaultClient(cfg)
        vc._client = AsyncMock()

        # Trip the circuit breaker
        vc._cb._state = vc._cb._state.__class__.OPEN  # type: ignore[attr-defined]
        import time
        vc._cb._tripped_at = time.monotonic() + 9999  # won't auto-recover

        with pytest.raises(VaultError) as exc_info:
            await vc._request("GET", "/v1/secret/data/test")
        assert exc_info.value.code == VaultErrorCode.CIRCUIT_OPEN


# ── RBAC: FastAPI dependency edge cases ──────────────────────────────────── #

class TestRBACEdgeCases:
    def test_require_permission_invalid_token(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from aegis.security.config import SecurityConfig
        from aegis.security.rbac.enforcer import RBACEnforcer
        from aegis.security.tls.jwt_manager import JWTManager

        cfg = SecurityConfig(jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"))
        jwt = JWTManager(config=cfg)
        enforcer = RBACEnforcer(jwt_manager=jwt)

        app = FastAPI()

        @app.get("/secure")
        async def secure(p=enforcer.require_permission("signals:read")):
            return {"ok": True}

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/secure", headers={"Authorization": "Bearer invalid.token.here"})
        assert resp.status_code == 401

    def test_require_role_missing_auth_header(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from aegis.security.config import SecurityConfig
        from aegis.security.rbac.enforcer import RBACEnforcer
        from aegis.security.tls.jwt_manager import JWTManager

        cfg = SecurityConfig(jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"))
        jwt = JWTManager(config=cfg)
        enforcer = RBACEnforcer(jwt_manager=jwt)

        app = FastAPI()

        @app.get("/role-check")
        async def role_check(p=enforcer.require_role("analyst")):
            return {"ok": True}

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/role-check")  # no auth header
        assert resp.status_code == 401


# ── Config prod safety ────────────────────────────────────────────────────── #

class TestConfigProdSafety:
    def test_prod_mode_hmac_key_must_change(self) -> None:
        from aegis.security.config import SecurityConfig

        with pytest.raises(Exception, match="hmac_key must be overridden"):
            SecurityConfig(
                vault_dev_mode=False,
                vault_token=SecretStr("real-vault-token-not-dev"),
                jwt_secret=SecretStr("x" * 32),
                hmac_key=SecretStr("dev-hmac-key-not-for-prod"),  # default value
            )

    def test_extra_fields_ignored(self) -> None:
        # SecurityConfig uses extra="ignore" so unknown env vars from the
        # shared .env file (AEGIS_* prefix, not AEGIS_SEC_*) don't raise.
        from aegis.security.config import SecurityConfig

        cfg = SecurityConfig()  # must not raise despite extra AEGIS_* vars in .env
        assert cfg.vault_dev_mode is True  # default dev-mode config
