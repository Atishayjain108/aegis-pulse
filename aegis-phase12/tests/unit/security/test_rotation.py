"""
tests.unit.security.test_rotation — Unit tests for SecretRotationManager.
tests.unit.security.test_constants_errors — Tests for constants and errors modules.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr


class TestSecretRotationManager:
    def _make_vault_mock(self, *, existing_version: int = 2) -> Any:
        vault = MagicMock()
        vault._cfg = MagicMock()
        vault._cfg.vault_transit_key = "aegis-key"

        # read_secret returns a mock with version
        mock_secret = MagicMock()
        mock_secret.version = existing_version
        vault.read_secret = AsyncMock(return_value=mock_secret)

        # write_secret returns a new version
        vault.write_secret = AsyncMock(return_value=existing_version + 1)
        vault.delete_secret = AsyncMock(return_value=None)
        return vault

    @pytest.mark.asyncio
    async def test_rotate_secret_success(self) -> None:
        from aegis.security.secrets.rotation import SecretRotationManager

        vault = self._make_vault_mock(existing_version=3)
        mgr = SecretRotationManager(vault)

        record = await mgr.rotate_secret("aegis/jwt/secret", field="value", grace_period_s=0)
        assert record.old_version == 3
        assert record.new_version == 4
        assert record.path == "aegis/jwt/secret"
        vault.write_secret.assert_called_once()

    @pytest.mark.asyncio
    async def test_rotate_secret_new_path(self) -> None:
        """Rotation of a non-existent path (first write) — version 0."""
        from aegis.security.secrets.rotation import SecretRotationManager
        from aegis.security.vault._errors import VaultError, VaultErrorCode

        vault = self._make_vault_mock()
        vault.read_secret = AsyncMock(
            side_effect=VaultError(VaultErrorCode.NOT_FOUND, "missing", status=404)
        )
        vault.write_secret = AsyncMock(return_value=1)

        mgr = SecretRotationManager(vault)
        record = await mgr.rotate_secret("aegis/new/secret", grace_period_s=0)
        assert record.old_version == 0
        assert record.new_version == 1

    @pytest.mark.asyncio
    async def test_complete_rotation_deletes_old_version(self) -> None:
        from aegis.security.secrets.rotation import SecretRotationManager

        vault = self._make_vault_mock(existing_version=5)
        mgr = SecretRotationManager(vault)

        record = await mgr.rotate_secret("aegis/db/password", grace_period_s=9999)
        assert record.in_grace_period is True

        await mgr.complete_rotation("aegis/db/password")
        vault.delete_secret.assert_called_once_with(
            "aegis/db/password", versions=[5]
        )
        assert record.completed is True

    @pytest.mark.asyncio
    async def test_complete_rotation_noop_if_not_found(self) -> None:
        from aegis.security.secrets.rotation import SecretRotationManager

        vault = self._make_vault_mock()
        mgr = SecretRotationManager(vault)
        # Should not raise
        await mgr.complete_rotation("aegis/nonexistent")

    @pytest.mark.asyncio
    async def test_rotate_during_grace_period_raises(self) -> None:
        from aegis.security.secrets.rotation import SecretRotationManager

        vault = self._make_vault_mock()
        mgr = SecretRotationManager(vault)

        await mgr.rotate_secret("aegis/key", grace_period_s=9999)
        with pytest.raises(RuntimeError, match="AEGIS-SEC-0116"):
            await mgr.rotate_secret("aegis/key", grace_period_s=9999)

    @pytest.mark.asyncio
    async def test_list_in_progress(self) -> None:
        from aegis.security.secrets.rotation import SecretRotationManager

        vault = self._make_vault_mock()
        mgr = SecretRotationManager(vault)

        await mgr.rotate_secret("aegis/path/a", grace_period_s=9999)
        await mgr.rotate_secret("aegis/path/b", grace_period_s=9999)

        in_progress = await mgr.list_in_progress()
        paths = {r["path"] for r in in_progress}
        assert "aegis/path/a" in paths
        assert "aegis/path/b" in paths

    @pytest.mark.asyncio
    async def test_rotation_record_grace_period(self) -> None:

        from aegis.security.secrets.rotation import RotationRecord

        rec = RotationRecord("test/path", old_version=1, new_version=2, grace_period_s=300)
        assert rec.in_grace_period is True
        assert rec.grace_remaining_s > 250.0
        assert rec.completed is False

    @pytest.mark.asyncio
    async def test_rotation_record_to_dict(self) -> None:
        from aegis.security.secrets.rotation import RotationRecord

        rec = RotationRecord("aegis/jwt/secret", old_version=3, new_version=4, grace_period_s=60)
        d = rec.to_dict()
        assert d["path"] == "aegis/jwt/secret"
        assert d["old_version"] == 3
        assert d["new_version"] == 4
        assert d["completed"] is False
        assert "started_at" in d
        assert "grace_until" in d

    @pytest.mark.asyncio
    async def test_rotate_jwt_secret_convenience(self) -> None:
        from aegis.security.secrets.rotation import SecretRotationManager

        vault = self._make_vault_mock(existing_version=1)
        mgr = SecretRotationManager(vault)
        record = await mgr.rotate_jwt_secret(grace_period_s=0)
        assert record.new_version == 2

    @pytest.mark.asyncio
    async def test_rotate_hmac_key_convenience(self) -> None:
        from aegis.security.secrets.rotation import SecretRotationManager

        vault = self._make_vault_mock(existing_version=1)
        mgr = SecretRotationManager(vault)
        record = await mgr.rotate_hmac_key(grace_period_s=0)
        assert record.new_version == 2

    @pytest.mark.asyncio
    async def test_rotation_with_audit_logger(self, tmp_path: Path) -> None:
        from aegis.security.audit.logger import AuditLogger
        from aegis.security.config import SecurityConfig
        from aegis.security.secrets.rotation import SecretRotationManager

        cfg = SecurityConfig(
            audit_log_path=str(tmp_path / "rotation_audit.jsonl"),
            jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"),
            hmac_key=SecretStr("test-hmac-key-not-for-prod-use-ok"),
        )
        vault = self._make_vault_mock(existing_version=2)
        async with AuditLogger(config=cfg) as audit:
            mgr = SecretRotationManager(vault, audit_logger=audit)
            await mgr.rotate_secret("aegis/db/password", grace_period_s=0)

        import json
        lines = (tmp_path / "rotation_audit.jsonl").read_text().strip().split("\n")
        events = [json.loads(ln)["event"] for ln in lines if ln.strip()]
        assert "secret.rotated" in events


class TestConstants:
    def test_all_constants_importable(self) -> None:
        from aegis.security.constants import (
            AUDIT_GENESIS_HASH,
            ERROR_PREFIX,
            HMAC_SIGNATURE_HEX_LEN,
            HSTS_MAX_AGE_S,
            JWT_ALGORITHM,
            JWT_DEFAULT_ACCESS_TTL_MINUTES,
            JWT_MIN_SECRET_LEN,
            PII_DEFAULT_PLACEHOLDER,
            RATE_LIMIT_DEFAULT_RPM,
            VAULT_DEFAULT_TIMEOUT_S,
        )
        assert VAULT_DEFAULT_TIMEOUT_S == 5.0
        assert JWT_MIN_SECRET_LEN == 32
        assert JWT_DEFAULT_ACCESS_TTL_MINUTES == 15
        assert JWT_ALGORITHM == "HS256"
        assert RATE_LIMIT_DEFAULT_RPM == 60
        assert PII_DEFAULT_PLACEHOLDER == "[REDACTED]"
        assert AUDIT_GENESIS_HASH == "0" * 64
        assert HMAC_SIGNATURE_HEX_LEN == 64
        assert HSTS_MAX_AGE_S == 31_536_000
        assert ERROR_PREFIX == "AEGIS-SEC"

    def test_error_code_ranges_are_ordered(self) -> None:
        from aegis.security.constants import (
            ERROR_PII_MIN,
            ERROR_SECRETS_MAX,
            ERROR_SECRETS_MIN,
            ERROR_VAULT_MAX,
            ERROR_VAULT_MIN,
        )
        assert ERROR_VAULT_MIN < ERROR_VAULT_MAX
        assert ERROR_VAULT_MAX < ERROR_SECRETS_MIN
        assert ERROR_SECRETS_MAX < ERROR_PII_MIN

    def test_vault_cb_thresholds_reasonable(self) -> None:
        from aegis.security.constants import (
            VAULT_CB_ERROR_RATE_THRESHOLD,
            VAULT_CB_HALF_OPEN_AFTER_S,
            VAULT_CB_WINDOW_S,
        )
        assert 0 < VAULT_CB_ERROR_RATE_THRESHOLD < 1
        assert VAULT_CB_WINDOW_S > 0
        assert VAULT_CB_HALF_OPEN_AFTER_S > VAULT_CB_WINDOW_S


class TestErrors:
    def test_all_error_codes_importable(self) -> None:
        from aegis.security.errors import Codes

        # Spot check a few
        assert Codes.SECRET_NOT_FOUND.code == "AEGIS-SEC-0001"
        assert Codes.PERMISSION_DENIED.http_status == 403
        assert Codes.RATE_LIMIT_EXCEEDED.http_status == 429
        assert Codes.INVALID_CREDENTIALS.http_status == 401

    def test_error_code_format(self) -> None:
        from aegis.security.errors import Codes

        msg = Codes.SECRET_NOT_FOUND.format(path="aegis/db/password")
        assert "aegis/db/password" in msg

    def test_error_code_to_dict(self) -> None:
        from aegis.security.errors import Codes

        d = Codes.PERMISSION_DENIED_RBAC.to_dict(role="viewer", permission="killswitch:trip")
        assert d["error_code"] == "AEGIS-SEC-0053"
        assert d["http_status"] == 403
        assert "docs" in d
        assert "viewer" in d["message"]

    def test_error_code_docs_url(self) -> None:
        from aegis.security.errors import Codes

        url = Codes.AUDIT_HMAC_MISMATCH.docs_url
        assert "AEGIS-SEC-0043" in url

    def test_security_error_exception(self) -> None:
        from aegis.security.errors import Codes, SecurityError

        exc = SecurityError(Codes.SECRET_NOT_FOUND, path="aegis/llm/groq")
        assert "AEGIS-SEC-0001" in str(exc)
        assert exc.http_status == 404
        d = exc.to_dict()
        assert d["error_code"] == "AEGIS-SEC-0001"
        assert "aegis/llm/groq" in d["message"]

    def test_security_error_missing_context_graceful(self) -> None:
        from aegis.security.errors import Codes, SecurityError

        # Should not raise even if context key is missing from format string
        exc = SecurityError(Codes.SECRET_NOT_FOUND)  # missing 'path'
        assert "AEGIS-SEC-0001" in str(exc)

    def test_all_codes_have_unique_numbers(self) -> None:
        from aegis.security.errors import Codes

        seen = {}
        for attr in dir(Codes):
            val = getattr(Codes, attr)
            from aegis.security.errors import ErrorCode
            if isinstance(val, ErrorCode):
                assert val.code not in seen, f"Duplicate code: {val.code}"
                seen[val.code] = attr

    def test_all_codes_have_valid_http_status(self) -> None:
        from aegis.security.errors import Codes, ErrorCode

        for attr in dir(Codes):
            val = getattr(Codes, attr)
            if isinstance(val, ErrorCode):
                assert val.http_status in (
                    200, 400, 401, 403, 404, 409, 429, 500, 502, 503, 504
                ), f"{val.code} has unusual status {val.http_status}"
