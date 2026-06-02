"""Phase 4 configuration object.

Extends the global `aegis.config.Settings` with execute-specific knobs.
Uses pydantic-settings so values can come from env, .env, or .env.test.

All required keys have defaults that allow the system to start in pure
advisory + log-only mode. Channels are auto-disabled when their required
secrets are absent — never an error.
"""

from __future__ import annotations

from typing import Final

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from aegis.execute.constants import (
    ALLOWED_MODES,
    APPROVAL_TIMEOUT_S,
    CAPITAL_DAILY_LOSS_LIMIT_USD,
    CAPITAL_KELLY_FRACTION,
    CAPITAL_MAX_RISK_USD,
    KILLSWITCH_KEY_DEFAULT,
    MODE_ADVISORY,
    NOTIFY_TIMEOUT_S,
    OUTBOX_DRAIN_BATCH,
    OUTBOX_DRAIN_INTERVAL_S,
    OUTBOX_MAX_ATTEMPTS,
)


class ExecuteSettings(BaseSettings):
    """Execute-layer configuration.

    Read once at process start, passed by value to long-lived components.
    """

    model_config = SettingsConfigDict(
        env_prefix="AEGIS_EXECUTE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ----- Mode + bind -------------------------------------------------------
    mode: str = Field(default=MODE_ADVISORY, description="Execution mode.")
    api_host: str = Field(default="127.0.0.1")
    api_port: int = Field(default=8200, ge=1, le=65535)

    # ----- Drainer -----------------------------------------------------------
    drain_interval_s: float = Field(default=OUTBOX_DRAIN_INTERVAL_S, gt=0.0)
    drain_batch: int = Field(default=OUTBOX_DRAIN_BATCH, gt=0, le=500)
    notify_max_attempts: int = Field(default=OUTBOX_MAX_ATTEMPTS, ge=1, le=20)
    notify_timeout_s: float = Field(default=NOTIFY_TIMEOUT_S, gt=0.0)

    # ----- Kill-switch -------------------------------------------------------
    killswitch_key: str = Field(default=KILLSWITCH_KEY_DEFAULT)

    # ----- Dedup -------------------------------------------------------------
    dedup_ttl_s: int = Field(default=3600, gt=0)

    # ----- HMAC --------------------------------------------------------------
    hmac_key: str = Field(default="")  # required only if webhook channel used

    # ----- Channels (empty = disabled) ---------------------------------------
    ntfy_topic: str = Field(default="")
    ntfy_base_url: str = Field(default="https://ntfy.sh")

    telegram_token: str = Field(default="")
    telegram_chat_id: str = Field(default="")

    discord_webhook_url: str = Field(default="")

    generic_webhook_url: str = Field(default="")

    # ----- B2B / Vyapar integration ------------------------------------------
    vyapar_webhook_url: str = Field(
        default="",
        description="Vyapar Intelligent Order webhook URL. Empty = disabled.",
    )

    # ----- Phase 6: Capital Execution Engine ---------------------------------
    capital_max_risk_usd: float = Field(
        default=CAPITAL_MAX_RISK_USD, gt=0.0,
        description="Maximum USD at risk per execution plan.",
    )
    capital_daily_loss_limit_usd: float = Field(
        default=CAPITAL_DAILY_LOSS_LIMIT_USD, gt=0.0,
        description="Daily drawdown ceiling; engine halts if breached.",
    )
    capital_kelly_fraction: float = Field(
        default=CAPITAL_KELLY_FRACTION, gt=0.0, le=1.0,
        description="Fractional Kelly safety factor (0.25 = 25% of optimal).",
    )
    approval_timeout_s: int = Field(
        default=APPROVAL_TIMEOUT_S, gt=0,
        description="Seconds to wait for operator approval before auto-reject.",
    )
    auto_execute_p0: bool = Field(
        default=False,
        description="Auto-execute P0 alerts without human approval.",
    )

    # Fulfillment API keys (empty = disabled / mock mode).
    printful_api_key: str = Field(default="", description="Printful API key.")
    cjdropship_api_key: str = Field(default="", description="CJ Dropshipping API key.")
    shopify_shop_domain: str = Field(default="", description="Shopify store domain.")
    shopify_access_token: str = Field(default="", description="Shopify Admin API token.")

    # ----- Optional API auth -------------------------------------------------
    api_bearer_token: str = Field(default="")  # empty = no auth

    # ----- Validators --------------------------------------------------------
    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, v: str) -> str:
        if v not in ALLOWED_MODES:
            raise ValueError(
                f"AEGIS_EXECUTE_MODE must be one of {sorted(ALLOWED_MODES)}, got {v!r}"
            )
        return v


_singleton: ExecuteSettings | None = None


def get_execute_settings() -> ExecuteSettings:
    """Return the process-wide ExecuteSettings singleton."""
    global _singleton  # noqa: PLW0603
    if _singleton is None:
        _singleton = ExecuteSettings()
    return _singleton


def set_execute_settings(settings: ExecuteSettings) -> None:
    """Override the singleton (test-only).

    Production code never calls this. Tests use it to inject fixtures.
    """
    global _singleton  # noqa: PLW0603
    _singleton = settings


def reset_execute_settings() -> None:
    """Reset the singleton (test-only)."""
    global _singleton  # noqa: PLW0603
    _singleton = None


__all__: Final = [
    "ExecuteSettings",
    "get_execute_settings",
    "reset_execute_settings",
    "set_execute_settings",
]
