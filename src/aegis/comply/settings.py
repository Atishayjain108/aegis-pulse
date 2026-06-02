"""Settings for the compliance engine (env prefix ``AEGIS_COMPLY_``).

Uses ``pydantic-settings`` when available; otherwise falls back to reading
environment variables directly so the package imports with zero extra deps.
"""

from __future__ import annotations

import os

import aegis.comply.constants as _constants

try:  # pragma: no cover - both branches exercised across environments
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class ComplySettings(BaseSettings):
        """Tunable thresholds and feature toggles for Phase 8."""

        model_config = SettingsConfigDict(env_prefix="AEGIS_COMPLY_", extra="ignore")

        flag_risk_threshold: float = _constants.FLAG_RISK_THRESHOLD
        block_risk_threshold: float = _constants.BLOCK_RISK_THRESHOLD
        trademark_similarity_threshold: float = _constants.TRADEMARK_SIMILARITY_THRESHOLD
        counterfeit_price_zscore_floor: float = _constants.COUNTERFEIT_PRICE_ZSCORE_FLOOR
        enable_live_trademark_lookup: bool = False
        enable_llm_augmentation: bool = True
        tenant_id: str = _constants.DEFAULT_TENANT_ID

except Exception:  # pragma: no cover - pydantic-settings not installed

    def _f(name: str, default: float) -> float:
        raw = os.environ.get(f"AEGIS_COMPLY_{name.upper()}")
        return float(raw) if raw is not None else default

    def _b(name: str, default: bool) -> bool:
        raw = os.environ.get(f"AEGIS_COMPLY_{name.upper()}")
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    class ComplySettings:  # type: ignore[no-redef]
        """Plain-dataclass-style fallback settings (no pydantic-settings)."""

        def __init__(self) -> None:
            self.flag_risk_threshold = _f("flag_risk_threshold", _constants.FLAG_RISK_THRESHOLD)
            self.block_risk_threshold = _f("block_risk_threshold", _constants.BLOCK_RISK_THRESHOLD)
            self.trademark_similarity_threshold = _f(
                "trademark_similarity_threshold", _constants.TRADEMARK_SIMILARITY_THRESHOLD
            )
            self.counterfeit_price_zscore_floor = _f(
                "counterfeit_price_zscore_floor", _constants.COUNTERFEIT_PRICE_ZSCORE_FLOOR
            )
            self.enable_live_trademark_lookup = _b("enable_live_trademark_lookup", False)
            self.enable_llm_augmentation = _b("enable_llm_augmentation", True)
            self.tenant_id = os.environ.get("AEGIS_COMPLY_TENANT_ID", _constants.DEFAULT_TENANT_ID)


def get_settings() -> ComplySettings:
    """Return a fresh ``ComplySettings`` instance (cheap; no caching by design)."""
    return ComplySettings()
