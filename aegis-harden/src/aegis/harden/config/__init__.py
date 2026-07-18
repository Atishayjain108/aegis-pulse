"""
Phase 5 settings singleton.

Reads `AEGIS_HARDEN_*` environment variables. Mirrors the pattern used by
`aegis.execute.config.ExecuteSettings` in Phase 4 — both projects intentionally
share the prefix convention `AEGIS_<PHASE>_<KEY>`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from aegis.harden.constants import (
    DEFAULT_HARDEN_PROFILE,
    DEFAULT_REQUEST_TIMEOUT_S,
    DEFAULT_RNG_SEED,
    HARDEN_VERDICT_STREAM,
    KNOWN_PROFILES,
    SMOOTHING_DEFAULT_SAMPLES,
    SMOOTHING_DEFAULT_SIGMA,
)


class HardenSettings(BaseSettings):
    """Runtime configuration. All defaults are safe for dev/test."""

    model_config = SettingsConfigDict(
        env_prefix="AEGIS_HARDEN_",
        env_file=None,
        extra="ignore",
        case_sensitive=False,
    )

    # --- Profile / fingerprints ---
    default_profile: str = Field(default=DEFAULT_HARDEN_PROFILE)
    ja3_pool_path: Path | None = Field(default=None)
    ja4_pool_path: Path | None = Field(default=None)

    # --- Playbook governance ---
    playbooks_dir: Path = Field(default=Path("playbooks"))
    reject_unknown_sources: bool = Field(default=False)
    default_playbook_name: str = Field(default="default")

    # --- ML defense ---
    smoothing_default_samples: int = Field(default=SMOOTHING_DEFAULT_SAMPLES)
    smoothing_default_sigma: float = Field(default=SMOOTHING_DEFAULT_SIGMA)

    # --- Streaming back to Phase 4 ---
    redis_url: str = Field(default="redis://localhost:6380/0")
    verdict_stream: str = Field(default=HARDEN_VERDICT_STREAM)
    publish_to_redis: bool = Field(default=False)

    # --- Misc ---
    request_timeout_s: float = Field(default=DEFAULT_REQUEST_TIMEOUT_S, gt=0.0, le=300.0)
    rng_seed: int = Field(default=DEFAULT_RNG_SEED)

    @field_validator("default_profile")
    @classmethod
    def _profile_known(cls, v: str) -> str:
        if v not in KNOWN_PROFILES:
            raise ValueError(f"default_profile={v} not in {KNOWN_PROFILES}")
        return v


@lru_cache(maxsize=1)
def get_settings() -> HardenSettings:
    """Process-wide settings singleton. Call `clear_settings()` in tests."""
    return HardenSettings()


def clear_settings() -> None:
    """Clear the cached settings — used by tests to force re-read of env."""
    get_settings.cache_clear()


__all__ = ["HardenSettings", "clear_settings", "get_settings"]
