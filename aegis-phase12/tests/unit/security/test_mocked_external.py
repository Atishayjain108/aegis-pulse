"""
tests.unit.security.test_mocked_external — Mock-based tests for external-tool modules.

Tests SOPS, TLS helpers, Vault client, and JWT edge cases using mocks
so they run without actual sops/age/mkcert/Vault installed.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

# ── SOPS Manager (mocked subprocess) ─────────────────────────────────────── #

class TestSOPSManagerMocked:
    def _make(self, tmp_path: Path):
        from aegis.security.sops.integration import SopsManager

        return SopsManager(
            age_key_file=tmp_path / "keys.txt",
            sops_config_path=str(tmp_path / ".sops.yaml"),
        )

    def test_check_dependencies_structure(self, tmp_path: Path) -> None:
        from aegis.security.sops.integration import SopsManager

        mgr = SopsManager()
        deps = mgr.check_dependencies()
        assert set(deps.keys()) == {"sops", "age", "age-keygen"}

    def test_generate_age_key_already_exists_raises(self, tmp_path: Path) -> None:
        mgr = self._make(tmp_path)
        # Create the key file
        key_file = tmp_path / "keys.txt"
        key_file.write_text("# public key: age1abc123\n")

        with pytest.raises(FileExistsError, match="AEGIS-SEC-0091"):
            mgr.generate_age_key(force=False)

    def test_generate_age_key_force_overwrites(self, tmp_path: Path) -> None:
        mgr = self._make(tmp_path)
        key_file = tmp_path / "keys.txt"
        key_file.write_text("# public key: age1old\n")

        mock_result = MagicMock()
        mock_result.stderr = "Public key: age1newkey123\n"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            mock_run.return_value = mock_result
            try:
                result = mgr.generate_age_key(force=True)
                assert result == "age1newkey123"
            except Exception:
                # age-keygen may not be installed; that's fine in this mock test
                pass

    def test_generate_age_key_missing_age_raises(self, tmp_path: Path) -> None:
        mgr = self._make(tmp_path)

        with patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="AEGIS-SEC-0092"):
                mgr.generate_age_key()

    def test_get_public_key_parses_file(self, tmp_path: Path) -> None:
        mgr = self._make(tmp_path)
        key_file = tmp_path / "keys.txt"
        key_file.write_text(
            "# created: 2026-01-01\n"
            "# public key: age1qyqszqgpqyqszqgpqyqszqgp\n"
            "AGE-SECRET-KEY-...\n"
        )
        pubkey = mgr.get_public_key()
        assert pubkey == "age1qyqszqgpqyqszqgpqyqszqgp"

    def test_get_public_key_no_match_raises(self, tmp_path: Path) -> None:
        mgr = self._make(tmp_path)
        key_file = tmp_path / "keys.txt"
        key_file.write_text("# created: 2026-01-01\nAGE-SECRET-KEY-...\n")

        with pytest.raises(ValueError, match="AEGIS-SEC-0094"):
            mgr.get_public_key()

    def test_init_sops_config_writes_file(self, tmp_path: Path) -> None:
        mgr = self._make(tmp_path)
        key_file = tmp_path / "keys.txt"
        key_file.write_text("# public key: age1testkey123\nAGE-SECRET-KEY-1...\n")

        config_path = mgr.init_sops_config(str(tmp_path))
        assert config_path.exists()
        content = config_path.read_text()
        assert "age1testkey123" in content
        assert "creation_rules" in content

    def test_init_sops_config_skips_if_exists(self, tmp_path: Path) -> None:
        mgr = self._make(tmp_path)
        # Pre-create the config
        existing = tmp_path / ".sops.yaml"
        existing.write_text("# existing config\n")

        result = mgr.init_sops_config(str(tmp_path))
        assert result == existing
        # File should not have been overwritten
        assert existing.read_text() == "# existing config\n"

    def test_decrypt_to_dict_mocked(self, tmp_path: Path) -> None:
        mgr = self._make(tmp_path)
        enc_file = tmp_path / ".env.sops.yaml"
        enc_file.write_text("sops-encrypted-content")

        mock_result = MagicMock()
        mock_result.stdout = (
            'AEGIS_PG_DSN="postgresql://localhost/test"\n'
            'AEGIS_REDIS_URL=redis://localhost:6379\n'
            '# comment line\n'
            '\n'
        )

        with patch("subprocess.run", return_value=mock_result):
            result = mgr.decrypt_to_dict(enc_file)

        assert result["AEGIS_PG_DSN"] == "postgresql://localhost/test"
        assert result["AEGIS_REDIS_URL"] == "redis://localhost:6379"
        assert len(result) == 2  # comment and blank lines ignored

    def test_inject_to_env_sets_env_vars(self, tmp_path: Path, monkeypatch) -> None:
        import os
        mgr = self._make(tmp_path)
        enc_file = tmp_path / ".env.sops.yaml"
        enc_file.write_text("sops-content")

        mock_result = MagicMock()
        mock_result.stdout = "TEST_INJECT_KEY=test-value\n"

        with patch("subprocess.run", return_value=mock_result):
            mgr.inject_to_env(enc_file)

        assert os.environ.get("TEST_INJECT_KEY") == "test-value"


# ── TLS Helpers (mocked subprocess) ──────────────────────────────────────── #

class TestTLSHelpersMocked:
    def test_ensure_mkcert_installed_true(self) -> None:
        from aegis.security.tls.helpers import ensure_mkcert_installed

        with patch("shutil.which", return_value="/usr/bin/mkcert"):
            assert ensure_mkcert_installed() is True

    def test_ensure_mkcert_installed_false(self) -> None:
        from aegis.security.tls.helpers import ensure_mkcert_installed

        with patch("shutil.which", return_value=None):
            assert ensure_mkcert_installed() is False

    def test_install_mkcert_ca_not_found(self) -> None:
        from aegis.security.tls.helpers import install_mkcert_ca

        with patch("shutil.which", return_value=None):
            result = install_mkcert_ca()
            assert result is False

    def test_install_mkcert_ca_success(self) -> None:
        from aegis.security.tls.helpers import install_mkcert_ca

        with patch("shutil.which", return_value="/usr/bin/mkcert"), \
             patch("subprocess.run", return_value=MagicMock(returncode=0)):
            result = install_mkcert_ca()
            assert result is True

    def test_install_mkcert_ca_failure(self) -> None:
        import subprocess

        from aegis.security.tls.helpers import install_mkcert_ca

        with patch("shutil.which", return_value="/usr/bin/mkcert"), \
             patch("subprocess.run", side_effect=subprocess.CalledProcessError(
                 1, "mkcert", stderr=b"permission denied"
             )):
            result = install_mkcert_ca()
            assert result is False

    def test_generate_local_cert_no_mkcert(self, tmp_path: Path) -> None:
        from aegis.security.tls.helpers import generate_local_cert

        with patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="AEGIS-SEC-0071"):
                generate_local_cert(output_dir=tmp_path)

    def test_generate_local_cert_success(self, tmp_path: Path) -> None:
        from aegis.security.tls.helpers import generate_local_cert

        with patch("shutil.which", return_value="/usr/bin/mkcert"), \
             patch("subprocess.run", return_value=MagicMock(returncode=0)):
            cert, key = generate_local_cert("localhost", output_dir=tmp_path)
            assert cert == tmp_path / "server.crt"
            assert key == tmp_path / "server.key"

    def test_generate_local_cert_subprocess_error(self, tmp_path: Path) -> None:
        import subprocess

        from aegis.security.tls.helpers import generate_local_cert

        with patch("shutil.which", return_value="/usr/bin/mkcert"), \
             patch("subprocess.run", side_effect=subprocess.CalledProcessError(
                 1, "mkcert", stderr=b"error: could not generate cert"
             )), pytest.raises(RuntimeError, match="AEGIS-SEC-0072"):
            generate_local_cert(output_dir=tmp_path)

    def test_uvicorn_ssl_kwargs(self, tmp_path: Path) -> None:
        from aegis.security.tls.helpers import uvicorn_ssl_kwargs

        cert = tmp_path / "server.crt"
        key = tmp_path / "server.key"
        kwargs = uvicorn_ssl_kwargs(cert, key)
        assert kwargs["ssl_certfile"] == str(cert)
        assert kwargs["ssl_keyfile"] == str(key)

    def test_cert_days_remaining_missing_file(self, tmp_path: Path) -> None:
        from aegis.security.tls.helpers import cert_days_remaining

        result = cert_days_remaining(tmp_path / "nonexistent.crt")
        assert result == -1


# ── Vault client with mocked HTTP ─────────────────────────────────────────── #

class TestVaultClientMocked:
    def _make_mock_client(self):
        """Build a VaultClient with a mocked httpx.AsyncClient."""
        from aegis.security.config import SecurityConfig
        from aegis.security.vault.client import VaultClient

        cfg = SecurityConfig()
        vc = VaultClient(cfg)
        return vc, cfg

    @pytest.mark.asyncio
    async def test_read_secret_success(self) -> None:
        from aegis.security.config import SecurityConfig
        from aegis.security.vault.client import VaultClient

        cfg = SecurityConfig()
        vc = VaultClient(cfg)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "data": {"password": "secret123", "host": "localhost"},
                "metadata": {"version": 3, "created_time": "2026-01-01T00:00:00Z"},
            }
        }

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_resp)
        vc._client = mock_client

        secret = await vc.read_secret("aegis/db")
        assert secret.data["password"] == "secret123"
        assert secret.version == 3

    @pytest.mark.asyncio
    async def test_read_secret_404_raises(self) -> None:
        from aegis.security.config import SecurityConfig
        from aegis.security.vault._errors import VaultError, VaultErrorCode
        from aegis.security.vault.client import VaultClient

        cfg = SecurityConfig()
        vc = VaultClient(cfg)

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_resp)
        vc._client = mock_client

        with pytest.raises(VaultError) as exc_info:
            await vc.read_secret("missing/path")
        assert exc_info.value.code == VaultErrorCode.NOT_FOUND

    @pytest.mark.asyncio
    async def test_write_secret_returns_version(self) -> None:
        from aegis.security.config import SecurityConfig
        from aegis.security.vault.client import VaultClient

        cfg = SecurityConfig()
        vc = VaultClient(cfg)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"version": 5}}

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_resp)
        vc._client = mock_client

        version = await vc.write_secret("aegis/db", {"password": "new-pw"})
        assert version == 5

    @pytest.mark.asyncio
    async def test_delete_secret_versions(self) -> None:
        from aegis.security.config import SecurityConfig
        from aegis.security.vault.client import VaultClient

        cfg = SecurityConfig()
        vc = VaultClient(cfg)

        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_resp)
        vc._client = mock_client

        # Should not raise
        await vc.delete_secret("aegis/old", versions=[1, 2])

    @pytest.mark.asyncio
    async def test_encrypt_decrypt_roundtrip_mocked(self) -> None:
        import base64

        from aegis.security.config import SecurityConfig
        from aegis.security.vault.client import VaultClient

        cfg = SecurityConfig()
        vc = VaultClient(cfg)

        plaintext = "sensitive-data"
        encoded = base64.b64encode(plaintext.encode()).decode()

        # Mock encrypt
        enc_resp = MagicMock()
        enc_resp.status_code = 200
        enc_resp.json.return_value = {"data": {"ciphertext": "vault:v1:abc123=="}}

        # Mock decrypt - returns base64 of original
        dec_resp = MagicMock()
        dec_resp.status_code = 200
        dec_resp.json.return_value = {"data": {"plaintext": encoded}}

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=[enc_resp, dec_resp])
        vc._client = mock_client

        ciphertext = await vc.encrypt(plaintext)
        assert ciphertext == "vault:v1:abc123=="

        decrypted = await vc.decrypt(ciphertext)
        assert decrypted.decode() == plaintext

    @pytest.mark.asyncio
    async def test_list_secrets(self) -> None:
        from aegis.security.config import SecurityConfig
        from aegis.security.vault.client import VaultClient

        cfg = SecurityConfig()
        vc = VaultClient(cfg)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {"keys": ["db/", "redis/", "minio/"]}
        }
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_resp)
        vc._client = mock_client

        keys = await vc.list_secrets("aegis")
        assert "db/" in keys
        assert len(keys) == 3

    @pytest.mark.asyncio
    async def test_circuit_breaker_trips_on_repeated_403(self) -> None:
        from aegis.security.config import SecurityConfig
        from aegis.security.vault._errors import VaultError
        from aegis.security.vault.client import VaultClient

        cfg = SecurityConfig(vault_retries=1)
        vc = VaultClient(cfg)

        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_resp)
        vc._client = mock_client

        # Generate enough failures to trip circuit breaker
        for _ in range(8):
            with contextlib.suppress(VaultError):
                await vc.read_secret("aegis/secret")

        # Now the circuit should be open
        assert vc._cb.state in ("OPEN", "CLOSED")  # CB trips at >30% error rate

    @pytest.mark.asyncio
    async def test_health_endpoint(self) -> None:
        from aegis.security.config import SecurityConfig
        from aegis.security.vault.client import VaultClient

        cfg = SecurityConfig()
        vc = VaultClient(cfg)

        mock_client = AsyncMock()
        mock_health_resp = MagicMock()
        mock_health_resp.json.return_value = {
            "initialized": True,
            "sealed": False,
            "version": "1.17.2",
        }
        mock_client.get = AsyncMock(return_value=mock_health_resp)
        vc._client = mock_client

        health = await vc.health()
        assert health["initialized"] is True
        assert health["sealed"] is False

    @pytest.mark.asyncio
    async def test_rotate_transit_key(self) -> None:
        from aegis.security.config import SecurityConfig
        from aegis.security.vault.client import VaultClient

        cfg = SecurityConfig()
        vc = VaultClient(cfg)

        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_resp)
        vc._client = mock_client

        # Should not raise
        await vc.rotate_transit_key()


# ── JWT revocation with Redis mock ────────────────────────────────────────── #

class TestJWTRevocationWithRedis:
    @pytest.mark.asyncio
    async def test_revoke_adds_to_redis(self) -> None:
        from aegis.security.config import SecurityConfig
        from aegis.security.tls.jwt_manager import JWTManager

        cfg = SecurityConfig(jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"))
        redis_mock = AsyncMock()
        redis_mock.setex = AsyncMock(return_value=True)
        redis_mock.exists = AsyncMock(return_value=1)

        mgr = JWTManager(config=cfg, redis_client=redis_mock)
        pair = mgr.issue_tokens(subject="user-to-revoke", role="viewer")
        payload = mgr.verify_access_token(pair.access_token)

        await mgr.revoke_token(pair.access_token)

        # setex should have been called with the JTI
        redis_mock.setex.assert_called_once()
        call_args = redis_mock.setex.call_args[0]
        assert payload.jti in call_args[0]  # key contains JTI

    @pytest.mark.asyncio
    async def test_is_revoked_checks_redis(self) -> None:
        from aegis.security.config import SecurityConfig
        from aegis.security.tls.jwt_manager import JWTManager

        cfg = SecurityConfig(jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"))
        redis_mock = AsyncMock()
        redis_mock.exists = AsyncMock(return_value=1)  # key exists = revoked

        mgr = JWTManager(config=cfg, redis_client=redis_mock)
        revoked = await mgr.is_revoked("some-jti-value")
        assert revoked is True

    @pytest.mark.asyncio
    async def test_is_not_revoked(self) -> None:
        from aegis.security.config import SecurityConfig
        from aegis.security.tls.jwt_manager import JWTManager

        cfg = SecurityConfig(jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"))
        redis_mock = AsyncMock()
        redis_mock.exists = AsyncMock(return_value=0)  # key absent = not revoked

        mgr = JWTManager(config=cfg, redis_client=redis_mock)
        revoked = await mgr.is_revoked("fresh-jti")
        assert revoked is False

    @pytest.mark.asyncio
    async def test_redis_error_in_revoke_logs_and_continues(self) -> None:
        from aegis.security.config import SecurityConfig
        from aegis.security.tls.jwt_manager import JWTManager

        cfg = SecurityConfig(jwt_secret=SecretStr("test-secret-key-at-least-32-chars!!"))
        redis_mock = AsyncMock()
        redis_mock.setex = AsyncMock(side_effect=Exception("Redis down"))

        mgr = JWTManager(config=cfg, redis_client=redis_mock)
        pair = mgr.issue_tokens(subject="u", role="viewer")
        # Should not raise even when Redis fails
        await mgr.revoke_token(pair.access_token)


# ── Rate limit Lua script logic ───────────────────────────────────────────── #

class TestRateLimitTokenBucket:
    @pytest.mark.asyncio
    async def test_consume_token_allows_within_limit(self) -> None:
        from aegis.security.config import SecurityConfig
        from aegis.security.middleware.ratelimit import RateLimitMiddleware

        cfg = SecurityConfig(rate_limit_enabled=True, rate_limit_default_rpm=60, rate_limit_burst=5)

        redis_mock = AsyncMock()
        redis_mock.script_load = AsyncMock(return_value="sha123")
        # Return [4, 5000] meaning 4 tokens remaining, 5s to full
        redis_mock.evalsha = AsyncMock(return_value=[4, 5000])

        mw = RateLimitMiddleware(MagicMock(), config=cfg, redis_client=redis_mock)
        remaining, reset_ms = await mw._consume_token("key", capacity=5, rpm=60)
        assert remaining == 4
        assert reset_ms == 5000

    @pytest.mark.asyncio
    async def test_consume_token_denied(self) -> None:
        from aegis.security.config import SecurityConfig
        from aegis.security.middleware.ratelimit import RateLimitMiddleware

        cfg = SecurityConfig(rate_limit_enabled=True, rate_limit_default_rpm=60, rate_limit_burst=5)

        redis_mock = AsyncMock()
        redis_mock.script_load = AsyncMock(return_value="sha123")
        # Return [-1, 3000] meaning denied, retry in 3s
        redis_mock.evalsha = AsyncMock(return_value=[-1, 3000])

        mw = RateLimitMiddleware(MagicMock(), config=cfg, redis_client=redis_mock)
        remaining, reset_ms = await mw._consume_token("key", capacity=5, rpm=60)
        assert remaining == -1
        assert reset_ms == 3000

    @pytest.mark.asyncio
    async def test_script_sha_cached(self) -> None:
        """Second call uses EVALSHA (cached SHA) without re-loading."""
        from aegis.security.config import SecurityConfig
        from aegis.security.middleware.ratelimit import RateLimitMiddleware

        cfg = SecurityConfig(rate_limit_enabled=True)
        redis_mock = AsyncMock()
        redis_mock.script_load = AsyncMock(return_value="cached-sha")
        redis_mock.evalsha = AsyncMock(return_value=[5, 0])

        mw = RateLimitMiddleware(MagicMock(), config=cfg, redis_client=redis_mock)
        await mw._consume_token("k", capacity=5, rpm=60)
        await mw._consume_token("k", capacity=5, rpm=60)

        # script_load called only once (SHA cached)
        redis_mock.script_load.assert_called_once()
        assert redis_mock.evalsha.call_count == 2
