"""
aegis.security.vault.client — HashiCorp Vault integration.

Wraps the Vault HTTP API (v1) with:
    - KV v2 read/write/delete
    - Transit encrypt / decrypt
    - Token renewal background task
    - Circuit breaker (trip at >30% error rate / 60 s window)
    - Structured error codes: AEGIS-SEC-0001..0020

All network calls are async (httpx.AsyncClient).  The client is designed
to be used as an async context manager or injected as a FastAPI dependency.

FREE-TIER PATH: Uses the open-source Vault OSS edition in dev-mode
(single-node, in-memory, auto-unsealed).  No Vault Enterprise licence needed.

PAID PATH (OPTIONAL): Vault Enterprise adds namespaces, HSM auto-unseal,
performance replication, and FIPS 140-2 modules.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

import httpx
import structlog
from pydantic import BaseModel, Field

from aegis.security.config import SecurityConfig, get_security_config
from aegis.security.vault._circuit_breaker import CircuitBreaker
from aegis.security.vault._errors import VaultError, VaultErrorCode

_log = structlog.get_logger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────── #
_RETRY_BASE_S: float = 0.5
_RETRY_MAX_S: float = 16.0
_RENEW_BEFORE_EXPIRY_S: int = 60
_CB_ERROR_RATE_THRESHOLD: float = 0.30
_CB_WINDOW_S: int = 60
_CB_HALF_OPEN_AFTER_S: int = 120


class VaultSecret(BaseModel):
    """Typed wrapper for a KV v2 secret value."""

    path: str = Field(description="Vault KV path (without 'data/' prefix)")
    data: dict[str, Any] = Field(description="Secret key/value pairs")
    version: int = Field(default=1, description="KV v2 version number")
    created_time: str = Field(default="", description="ISO8601 creation timestamp")
    lease_id: str = Field(default="", description="Dynamic secret lease ID")
    lease_duration: int = Field(default=0, description="Lease duration in seconds")


class VaultClient:
    """Async Vault client providing KV v2, Transit, and token renewal.

    Usage::

        async with VaultClient() as vault:
            secret = await vault.read_secret("aegis/db/credentials")
            pg_password = secret.data["password"]
            ciphertext = await vault.encrypt("my plaintext")
            plaintext  = await vault.decrypt(ciphertext)

    Parameters
    ----------
    config:
        Injected ``SecurityConfig``; defaults to the global singleton.
    """

    def __init__(self, config: SecurityConfig | None = None) -> None:
        self._cfg: SecurityConfig = config or get_security_config()
        self._client: httpx.AsyncClient | None = None
        self._cb: CircuitBreaker = CircuitBreaker(
            error_rate_threshold=_CB_ERROR_RATE_THRESHOLD,
            window_s=_CB_WINDOW_S,
            half_open_after_s=_CB_HALF_OPEN_AFTER_S,
        )
        self._renewal_task: asyncio.Task[None] | None = None
        self._token_ttl: int = 0

    # ── Lifecycle ──────────────────────────────────────────────────────────── #

    async def __aenter__(self) -> VaultClient:
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def connect(self) -> None:
        """Open the HTTP connection pool and start token renewal."""
        headers: dict[str, str] = {
            "X-Vault-Token": self._cfg.vault_token.get_secret_value(),
            "Content-Type": "application/json",
        }
        if self._cfg.vault_namespace:
            headers["X-Vault-Namespace"] = self._cfg.vault_namespace

        self._client = httpx.AsyncClient(
            base_url=self._cfg.vault_addr,
            headers=headers,
            timeout=httpx.Timeout(self._cfg.vault_timeout_s),
            # Trust system CAs; override via HTTPX_SSL_VERIFY if needed
        )
        # Verify connectivity + get token metadata
        await self._refresh_token_ttl()
        # Start background renewal
        self._renewal_task = asyncio.create_task(
            self._renewal_loop(), name="vault-token-renewal"
        )
        _log.info(
            "vault.connected",
            addr=self._cfg.vault_addr,
            token_ttl=self._token_ttl,
        )

    async def close(self) -> None:
        """Cancel background tasks and close connection pool."""
        if self._renewal_task and not self._renewal_task.done():
            self._renewal_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._renewal_task
        if self._client:
            await self._client.aclose()
        _log.info("vault.disconnected")

    # ── KV v2 operations ───────────────────────────────────────────────────── #

    async def read_secret(
        self,
        path: str,
        *,
        version: int | None = None,
    ) -> VaultSecret:
        """Read a secret from the KV v2 mount.

        Parameters
        ----------
        path:
            Secret path relative to the KV mount, e.g. ``"aegis/db/password"``.
        version:
            Specific version to retrieve; ``None`` fetches the latest.

        Returns
        -------
        VaultSecret
            Decoded secret payload with metadata.

        Raises
        ------
        VaultError
            With code ``AEGIS-SEC-0001`` on 404, ``AEGIS-SEC-0002`` on 403,
            ``AEGIS-SEC-0003`` on network/timeout errors.
        """
        url = f"/v1/{self._cfg.vault_mount_kv}/data/{path}"
        params: dict[str, int] = {}
        if version is not None:
            params["version"] = version

        resp = await self._request("GET", url, params=params)
        payload = resp.get("data", {})
        meta = payload.get("metadata", {})
        return VaultSecret(
            path=path,
            data=payload.get("data", {}),
            version=meta.get("version", 1),
            created_time=meta.get("created_time", ""),
        )

    async def write_secret(
        self,
        path: str,
        data: dict[str, Any],
        *,
        cas: int | None = None,
    ) -> int:
        """Write (create or update) a secret in the KV v2 mount.

        Parameters
        ----------
        path:
            Destination path relative to the KV mount.
        data:
            Key/value pairs to store.
        cas:
            Check-and-set version; raises if the current version != ``cas``.

        Returns
        -------
        int
            New version number.
        """
        url = f"/v1/{self._cfg.vault_mount_kv}/data/{path}"
        body: dict[str, Any] = {"data": data}
        if cas is not None:
            body["options"] = {"cas": cas}

        resp = await self._request("POST", url, json_body=body)
        return int(resp.get("data", {}).get("version", 1))

    async def delete_secret(self, path: str, *, versions: list[int] | None = None) -> None:
        """Soft-delete specific versions or the latest version of a secret.

        A soft delete marks versions as deleted but retains metadata.
        Use ``destroy_secret`` for permanent removal.
        """
        if versions:
            url = f"/v1/{self._cfg.vault_mount_kv}/delete/{path}"
            await self._request("POST", url, json_body={"versions": versions})
        else:
            url = f"/v1/{self._cfg.vault_mount_kv}/data/{path}"
            await self._request("DELETE", url)

    async def destroy_secret(self, path: str, versions: list[int]) -> None:
        """Permanently destroy specific secret versions (irreversible)."""
        url = f"/v1/{self._cfg.vault_mount_kv}/destroy/{path}"
        await self._request("POST", url, json_body={"versions": versions})

    async def list_secrets(self, path: str) -> list[str]:
        """List keys under a KV v2 path prefix."""
        url = f"/v1/{self._cfg.vault_mount_kv}/metadata/{path}"
        resp = await self._request("LIST", url)
        return list(resp.get("data", {}).get("keys", []))

    # ── Transit encrypt / decrypt ──────────────────────────────────────────── #

    async def encrypt(self, plaintext: str | bytes) -> str:
        """Encrypt ``plaintext`` using the configured Transit key.

        Parameters
        ----------
        plaintext:
            Data to encrypt. Strings are UTF-8 encoded; bytes are used as-is.

        Returns
        -------
        str
            Vault-format ciphertext (``vault:v1:...``).
        """
        import base64

        if isinstance(plaintext, str):
            plaintext = plaintext.encode("utf-8")
        b64 = base64.b64encode(plaintext).decode()
        url = f"/v1/{self._cfg.vault_mount_transit}/encrypt/{self._cfg.vault_transit_key}"
        resp = await self._request("POST", url, json_body={"plaintext": b64})
        return str(resp["data"]["ciphertext"])

    async def decrypt(self, ciphertext: str) -> bytes:
        """Decrypt a Vault Transit ciphertext.

        Returns
        -------
        bytes
            Original plaintext bytes.
        """
        import base64

        url = f"/v1/{self._cfg.vault_mount_transit}/decrypt/{self._cfg.vault_transit_key}"
        resp = await self._request("POST", url, json_body={"ciphertext": ciphertext})
        b64 = resp["data"]["plaintext"]
        return base64.b64decode(b64)

    async def rotate_transit_key(self) -> None:
        """Rotate the Transit encryption key (new versions encrypt; old still decrypt)."""
        url = f"/v1/{self._cfg.vault_mount_transit}/keys/{self._cfg.vault_transit_key}/rotate"
        await self._request("POST", url)
        _log.info("vault.transit_key_rotated", key=self._cfg.vault_transit_key)

    # ── Health ─────────────────────────────────────────────────────────────── #

    async def health(self) -> dict[str, Any]:
        """Return Vault's health endpoint payload."""
        assert self._client is not None, "VaultClient not connected"
        try:
            resp = await self._client.get("/v1/sys/health", timeout=3.0)
            return dict(resp.json())
        except Exception as exc:
            return {"error": str(exc), "initialized": False}

    # ── Internal helpers ───────────────────────────────────────────────────── #

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a Vault API request with retry + circuit-breaker."""
        assert self._client is not None, "VaultClient not connected — call connect() first"

        if not self._cb.allow_request():
            raise VaultError(
                VaultErrorCode.CIRCUIT_OPEN,
                f"Vault circuit breaker is OPEN for {self._cfg.vault_addr}",
            )

        last_exc: Exception | None = None
        for attempt in range(self._cfg.vault_retries):
            try:
                r = await self._client.request(
                    method,
                    url,
                    json=json_body,
                    params=params,
                )
                if r.status_code == 200 or r.status_code == 204:
                    self._cb.record_success()
                    if r.status_code == 204:
                        return {}
                    return dict(r.json())
                elif r.status_code == 404:
                    self._cb.record_success()  # 404 is a valid response, not an error
                    raise VaultError(
                        VaultErrorCode.NOT_FOUND,
                        f"Secret not found: {url}",
                        status=404,
                    )
                elif r.status_code == 403:
                    self._cb.record_failure()
                    raise VaultError(
                        VaultErrorCode.PERMISSION_DENIED,
                        f"Vault permission denied: {url}",
                        status=403,
                    )
                else:
                    body = r.text[:300]
                    self._cb.record_failure()
                    raise VaultError(
                        VaultErrorCode.API_ERROR,
                        f"Vault returned {r.status_code}: {body}",
                        status=r.status_code,
                    )
            except VaultError:
                raise
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                self._cb.record_failure()
                last_exc = exc
                wait = min(_RETRY_BASE_S * (2**attempt), _RETRY_MAX_S)
                _log.warning(
                    "vault.request_retry",
                    attempt=attempt + 1,
                    url=url,
                    error=str(exc),
                    wait_s=wait,
                )
                await asyncio.sleep(wait)

        raise VaultError(
            VaultErrorCode.NETWORK_ERROR,
            f"Vault unreachable after {self._cfg.vault_retries} retries: {last_exc}",
        )

    async def _refresh_token_ttl(self) -> None:
        """Query the token's remaining TTL and store it."""
        try:
            resp = await self._request("GET", "/v1/auth/token/lookup-self")
            self._token_ttl = int(resp.get("data", {}).get("ttl", 0))
        except VaultError as exc:
            _log.warning("vault.token_lookup_failed", error=str(exc))
            self._token_ttl = 0

    async def _renewal_loop(self) -> None:
        """Background task: renew the Vault token before it expires."""
        while True:
            try:
                await self._refresh_token_ttl()
                if self._token_ttl > 0:
                    sleep_for = max(
                        self._token_ttl - _RENEW_BEFORE_EXPIRY_S,
                        30,
                    )
                    await asyncio.sleep(sleep_for)
                    # Renew
                    resp = await self._request(
                        "POST",
                        "/v1/auth/token/renew-self",
                        json_body={"increment": "1h"},
                    )
                    new_ttl = int(
                        resp.get("auth", {}).get("lease_duration", 3600)
                    )
                    self._token_ttl = new_ttl
                    _log.info("vault.token_renewed", new_ttl_s=new_ttl)
                else:
                    # Token never expires (root token in dev mode)
                    await asyncio.sleep(300)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                _log.error("vault.renewal_loop_error", error=str(exc))
                await asyncio.sleep(30)
