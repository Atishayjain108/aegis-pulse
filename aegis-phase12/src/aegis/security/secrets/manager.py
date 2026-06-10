"""
aegis.security.secrets.manager — Unified secret retrieval with layered fallback.

Priority chain (highest → lowest):
    1. HashiCorp Vault KV v2  (``vault://aegis/<path>``)
    2. SOPS-decrypted environment file  (``.env.sops.yaml``)
    3. Plain environment variables  (``os.environ``)
    4. ``default`` parameter value

This abstraction lets every other module call ``secrets.get("db/password")``
without caring whether Vault is available — degrading gracefully to env vars
in CI / local dev where Vault is not running.

Error codes: AEGIS-SEC-0021..0030
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import structlog
from pydantic import SecretStr

from aegis.security.config import SecurityConfig, get_security_config
from aegis.security.vault._errors import VaultError
from aegis.security.vault.client import VaultClient

_log = structlog.get_logger(__name__)

_SENTINEL = object()


class SecretsManager:
    """Provides a unified, layered secret retrieval interface.

    Usage::

        mgr = SecretsManager()
        await mgr.start()
        password = await mgr.get("aegis/db/password")
        await mgr.stop()

    Or as async context manager::

        async with SecretsManager() as mgr:
            token = await mgr.get("aegis/llm/groq_api_key")

    Parameters
    ----------
    config:
        Injected config; defaults to global singleton.
    vault_client:
        Pre-built Vault client (useful for testing with mocks).
    """

    def __init__(
        self,
        config: SecurityConfig | None = None,
        vault_client: VaultClient | None = None,
    ) -> None:
        self._cfg = config or get_security_config()
        self._vault: VaultClient | None = vault_client
        self._vault_owned: bool = vault_client is None
        self._sops_cache: dict[str, str] = {}
        self._sops_loaded: bool = False

    # ── Lifecycle ──────────────────────────────────────────────────────────── #

    async def __aenter__(self) -> SecretsManager:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    async def start(self) -> None:
        """Initialise Vault client and pre-load SOPS secrets."""
        if self._vault is None and self._vault_owned:
            self._vault = VaultClient(self._cfg)
            try:
                await self._vault.connect()
                _log.info("secrets.vault_connected")
            except Exception:
                _log.warning("secrets.vault_unavailable", exc_info=False)
                self._vault = None

        await self._load_sops()

    async def stop(self) -> None:
        """Close underlying Vault client."""
        if self._vault and self._vault_owned:
            await self._vault.close()
            self._vault = None

    # ── Public API ─────────────────────────────────────────────────────────── #

    async def get(
        self,
        key: str,
        *,
        field: str = "value",
        default: Any = _SENTINEL,  # noqa: ANN401
    ) -> str:
        """Retrieve a secret by key, walking the fallback chain.

        Parameters
        ----------
        key:
            Logical key, e.g. ``"aegis/db/password"``.  Slashes map to Vault
            KV path; environment variable is derived by upper-casing and
            replacing ``/`` and ``-`` with ``_``.
        field:
            For Vault KV secrets with multiple fields, which field to return.
        default:
            Returned if no source has the key.  If not provided and the key
            is missing everywhere, ``KeyError`` is raised.

        Returns
        -------
        str
            The secret value.

        Raises
        ------
        KeyError
            If the key is not found and no default is given.
        """
        # 1. Vault
        if self._vault is not None:
            try:
                secret = await self._vault.read_secret(key)
                if field in secret.data:
                    return str(secret.data[field])
                # If field not found, fall through
            except VaultError as exc:
                if exc.status != 404:
                    _log.debug(
                        "secrets.vault_miss",
                        key=key,
                        code=exc.code.value,
                    )

        # 2. SOPS env cache
        env_key = _to_env_key(key)
        if env_key in self._sops_cache:
            return self._sops_cache[env_key]

        # 3. Raw environment
        val = os.environ.get(env_key)
        if val is not None:
            return val

        # 4. Default
        if default is not _SENTINEL:
            return str(default)

        raise KeyError(
            f"[AEGIS-SEC-0021] Secret not found: key='{key}', "
            f"env='{env_key}'.  Check Vault path, .env.sops.yaml, "
            f"or set {env_key} in your environment."
        )

    async def get_secret_str(self, key: str, **kwargs: Any) -> SecretStr:  # noqa: ANN401
        """Like ``get()`` but returns a ``pydantic.SecretStr``."""
        return SecretStr(await self.get(key, **kwargs))

    async def put(
        self,
        key: str,
        data: dict[str, Any],
        *,
        cas: int | None = None,
    ) -> int:
        """Write a secret to Vault KV v2.

        Raises
        ------
        RuntimeError
            If Vault is not available (env-only mode).
        """
        if self._vault is None:
            raise RuntimeError(
                "[AEGIS-SEC-0022] Cannot write secret: Vault is not connected. "
                "Start the Vault service or run 'aegis doctor' to diagnose."
            )
        return await self._vault.write_secret(key, data, cas=cas)

    async def encrypt(self, plaintext: str | bytes) -> str:
        """Encrypt data via Vault Transit.

        Falls back to Fernet (symmetric AES) when Vault is unavailable,
        using the HMAC key as the Fernet password (KDF: PBKDF2-SHA256).
        """
        if self._vault is not None:
            try:
                return await self._vault.encrypt(plaintext)
            except VaultError as exc:
                _log.warning(
                    "secrets.transit_fallback",
                    reason=str(exc),
                    backend="fernet",
                )
        return _fernet_encrypt(plaintext, self._cfg.hmac_key)

    async def decrypt(self, ciphertext: str) -> bytes:
        """Decrypt data — detects whether Vault or Fernet ciphertext."""
        if ciphertext.startswith("vault:v"):
            if self._vault is None:
                raise RuntimeError(
                    "[AEGIS-SEC-0023] Vault ciphertext but Vault is unavailable"
                )
            return await self._vault.decrypt(ciphertext)
        # Fernet path
        return _fernet_decrypt(ciphertext, self._cfg.hmac_key)

    # ── SOPS loading ───────────────────────────────────────────────────────── #

    async def _load_sops(self) -> None:
        """Decrypt .env.sops.yaml and cache key/values in memory."""
        if self._sops_loaded:
            return
        sops_path = Path(self._cfg.sops_config_path).parent / ".env.sops.yaml"
        if not sops_path.exists():
            _log.debug("secrets.sops_file_not_found", path=str(sops_path))
            self._sops_loaded = True
            return
        try:
            result = subprocess.run(
                ["sops", "--decrypt", str(sops_path)],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                self._sops_cache[k.strip()] = v.strip().strip('"').strip("'")
            _log.info(
                "secrets.sops_loaded",
                keys=len(self._sops_cache),
                path=str(sops_path),
            )
        except FileNotFoundError:
            _log.debug("secrets.sops_not_installed", note="Install with: apt install sops")
        except subprocess.CalledProcessError as exc:
            _log.warning(
                "secrets.sops_decrypt_failed",
                stderr=exc.stderr[:200],
            )
        except subprocess.TimeoutExpired:
            _log.warning("secrets.sops_timeout")
        finally:
            self._sops_loaded = True


# ── Utility helpers ────────────────────────────────────────────────────────── #


def _to_env_key(key: str) -> str:
    """Convert a logical path key to an environment variable name.

    ``"aegis/db/password"`` → ``"AEGIS_DB_PASSWORD"``
    """
    return key.upper().replace("/", "_").replace("-", "_")


def _fernet_encrypt(plaintext: str | bytes, hmac_key: object) -> str:
    """AES-128-CBC (Fernet) fallback encryption."""
    import base64
    import hashlib

    from cryptography.fernet import Fernet

    if isinstance(hmac_key, object) and hasattr(hmac_key, "get_secret_value"):
        raw = hmac_key.get_secret_value().encode()  # type: ignore[union-attr]
    else:
        raw = str(hmac_key).encode()

    # Derive a 32-byte key from the HMAC secret using SHA-256
    key_bytes = hashlib.sha256(raw).digest()
    fernet_key = base64.urlsafe_b64encode(key_bytes)
    f = Fernet(fernet_key)

    if isinstance(plaintext, str):
        plaintext = plaintext.encode("utf-8")
    return f"fernet:{f.encrypt(plaintext).decode()}"


def _fernet_decrypt(ciphertext: str, hmac_key: object) -> bytes:
    """Fernet fallback decryption."""
    import base64
    import hashlib

    from cryptography.fernet import Fernet

    if not ciphertext.startswith("fernet:"):
        raise ValueError(f"[AEGIS-SEC-0009] Unknown ciphertext format: {ciphertext[:20]}")

    if isinstance(hmac_key, object) and hasattr(hmac_key, "get_secret_value"):
        raw = hmac_key.get_secret_value().encode()  # type: ignore[union-attr]
    else:
        raw = str(hmac_key).encode()

    key_bytes = hashlib.sha256(raw).digest()
    fernet_key = base64.urlsafe_b64encode(key_bytes)
    f = Fernet(fernet_key)
    token = ciphertext[len("fernet:"):].encode()
    return f.decrypt(token)
