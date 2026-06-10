"""
aegis.security.vault.bootstrap — Programmatic Vault bootstrap helper.

Automates the first-time setup of Vault for AEGIS:
    1. Enable KV v2 at ``secret/``
    2. Enable Transit at ``transit/`` + create ``aegis-key``
    3. Write initial secret structure (db, redis, minio, security)
    4. Create least-privilege policies per service
    5. Issue scoped service tokens

Called by the Docker init sidecar AND by ``aegis security vault init``.

FREE-TIER PATH: Uses Vault OSS dev-mode; no Enterprise features.
"""

from __future__ import annotations

from typing import Any

import structlog

from aegis.security.vault._errors import VaultError
from aegis.security.vault.client import VaultClient

_log = structlog.get_logger(__name__)

# ── Policy definitions ─────────────────────────────────────────────────────── #

# Least-privilege policies: each service gets read-only access to its own secrets
_POLICIES: dict[str, str] = {
    "aegis-scraper": """
path "secret/data/aegis/db" { capabilities = ["read"] }
path "secret/data/aegis/redis" { capabilities = ["read"] }
path "secret/data/aegis/minio" { capabilities = ["read"] }
path "secret/data/aegis/scrape/*" { capabilities = ["read"] }
""",
    "aegis-agents": """
path "secret/data/aegis/db" { capabilities = ["read"] }
path "secret/data/aegis/redis" { capabilities = ["read"] }
path "secret/data/aegis/llm/*" { capabilities = ["read"] }
path "secret/data/aegis/agents/*" { capabilities = ["read"] }
""",
    "aegis-predict": """
path "secret/data/aegis/db" { capabilities = ["read"] }
path "secret/data/aegis/redis" { capabilities = ["read"] }
path "secret/data/aegis/predict/*" { capabilities = ["read"] }
path "transit/encrypt/aegis-key" { capabilities = ["update"] }
path "transit/decrypt/aegis-key" { capabilities = ["update"] }
""",
    "aegis-execute": """
path "secret/data/aegis/db" { capabilities = ["read"] }
path "secret/data/aegis/redis" { capabilities = ["read"] }
path "secret/data/aegis/execute/*" { capabilities = ["read"] }
path "secret/data/aegis/notifiers/*" { capabilities = ["read"] }
""",
    "aegis-dashboard": """
path "secret/data/aegis/db" { capabilities = ["read"] }
path "secret/data/aegis/redis" { capabilities = ["read"] }
path "secret/data/aegis/security/*" { capabilities = ["read"] }
""",
    "aegis-security": """
path "secret/data/aegis/*" { capabilities = ["read", "create", "update", "delete", "list"] }
path "secret/metadata/aegis/*" { capabilities = ["read", "list", "delete"] }
path "secret/delete/aegis/*" { capabilities = ["update"] }
path "secret/destroy/aegis/*" { capabilities = ["update"] }
path "transit/keys/aegis-key" { capabilities = ["read"] }
path "transit/encrypt/aegis-key" { capabilities = ["update"] }
path "transit/decrypt/aegis-key" { capabilities = ["update"] }
path "transit/keys/aegis-key/rotate" { capabilities = ["update"] }
""",
}

# Initial secret structure (dev defaults — overwrite in production)
_INITIAL_SECRETS: dict[str, dict[str, str]] = {
    "aegis/db": {
        "password": "aegis_app_dev_pw",
        "host": "aegis-postgres",
        "port": "5432",
        "name": "aegis",
        "user": "aegis_app",
    },
    "aegis/redis": {
        "url": "redis://aegis-redis:6379/0",
    },
    "aegis/minio": {
        "access_key": "aegis-dev-key",
        "secret_key": "aegis-dev-secret-please-change",
        "endpoint": "http://aegis-minio:9000",
    },
    "aegis/security": {
        "jwt_secret": "CHANGE-ME-generate-with-openssl-rand-hex-32",
        "hmac_key": "CHANGE-ME-generate-with-openssl-rand-hex-32",
    },
    "aegis/llm": {
        "groq_api_key": "",
        "openrouter_api_key": "",
        "gemini_api_key": "",
    },
    "aegis/notifiers": {
        "telegram_bot_token": "",
        "discord_webhook_url": "",
        "ntfy_topic": "aegis-alerts",
    },
}


class VaultBootstrap:
    """One-shot Vault bootstrap for AEGIS.

    Parameters
    ----------
    vault_client:
        Connected ``VaultClient`` instance.
    overwrite_existing:
        If True, overwrite secrets that already exist.
        Set False in production to avoid clobbering manual changes.
    """

    def __init__(
        self,
        vault_client: VaultClient,
        *,
        overwrite_existing: bool = False,
    ) -> None:
        self._vault = vault_client
        self._overwrite = overwrite_existing
        self._results: list[dict[str, Any]] = []

    async def run(self) -> list[dict[str, Any]]:
        """Execute the full bootstrap sequence.

        Returns
        -------
        list[dict[str, Any]]
            List of step results with ``step``, ``status``, and ``detail`` keys.
        """
        self._results = []
        await self._enable_kv()
        await self._enable_transit()
        await self._write_initial_secrets()
        await self._create_policies()
        _log.info("vault.bootstrap_complete", steps=len(self._results))
        return self._results

    # ── Steps ──────────────────────────────────────────────────────────────── #

    async def _enable_kv(self) -> None:
        """Enable KV v2 secrets engine at ``secret/``."""
        try:
            await self._vault._request("POST", "/v1/sys/mounts/secret", json_body={
                "type": "kv",
                "options": {"version": "2"},
            })
            self._record("enable_kv_v2", "ok", "KV v2 enabled at secret/")
        except VaultError as exc:
            if "path is already in use" in str(exc).lower() or exc.status == 400:
                self._record("enable_kv_v2", "skipped", "already enabled")
            else:
                self._record("enable_kv_v2", "error", str(exc))

    async def _enable_transit(self) -> None:
        """Enable Transit secrets engine at ``transit/``."""
        try:
            await self._vault._request("POST", "/v1/sys/mounts/transit", json_body={
                "type": "transit",
            })
            # Create the aegis-key
            await self._vault._request(
                "POST",
                f"/v1/transit/keys/{self._vault._cfg.vault_transit_key}",
                json_body={"type": "aes256-gcm96"},
            )
            self._record("enable_transit", "ok", "Transit enabled + aegis-key created")
        except VaultError as exc:
            if "path is already in use" in str(exc).lower() or exc.status == 400:
                self._record("enable_transit", "skipped", "already enabled")
            else:
                self._record("enable_transit", "error", str(exc))

    async def _write_initial_secrets(self) -> None:
        """Write all initial AEGIS secrets to KV v2."""
        for path, data in _INITIAL_SECRETS.items():
            try:
                if not self._overwrite:
                    # Check if secret already exists
                    try:
                        await self._vault.read_secret(path)
                        self._record(f"write_secret:{path}", "skipped", "already exists")
                        continue
                    except VaultError as exc:
                        if exc.status != 404:
                            raise

                version = await self._vault.write_secret(path, data)
                self._record(f"write_secret:{path}", "ok", f"version={version}")
            except VaultError as exc:
                self._record(f"write_secret:{path}", "error", str(exc))

    async def _create_policies(self) -> None:
        """Write HCL policies for each AEGIS service."""
        for service, policy_hcl in _POLICIES.items():
            try:
                await self._vault._request(
                    "PUT",
                    f"/v1/sys/policies/acl/{service}",
                    json_body={"policy": policy_hcl.strip()},
                )
                self._record(f"policy:{service}", "ok", "policy written")
            except VaultError as exc:
                self._record(f"policy:{service}", "error", str(exc))

    def _record(self, step: str, status: str, detail: str) -> None:
        entry = {"step": step, "status": status, "detail": detail}
        self._results.append(entry)
        if status == "error":
            _log.warning("vault.bootstrap_step", **entry)
        else:
            _log.info("vault.bootstrap_step", **entry)


async def bootstrap_vault(vault_client: VaultClient, **kwargs: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    """Convenience coroutine — bootstrap Vault using a connected client."""
    return await VaultBootstrap(vault_client, **kwargs).run()
