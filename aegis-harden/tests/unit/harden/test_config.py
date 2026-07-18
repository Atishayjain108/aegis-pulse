"""Tests for `aegis.harden.config`."""

from __future__ import annotations

import pytest

from aegis.harden.config import HardenSettings, clear_settings, get_settings


class TestSettings:
    def test_defaults(self) -> None:
        s = get_settings()
        assert s.default_profile == "standard"
        assert s.smoothing_default_samples > 0
        assert s.publish_to_redis is False

    def test_unknown_profile_rejected(self) -> None:
        with pytest.raises(ValueError):
            HardenSettings(default_profile="evil")  # type: ignore[arg-type]

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AEGIS_HARDEN_DEFAULT_PROFILE", "stealth")
        monkeypatch.setenv("AEGIS_HARDEN_PUBLISH_TO_REDIS", "true")
        clear_settings()
        s = get_settings()
        assert s.default_profile == "stealth"
        assert s.publish_to_redis is True

    def test_timeout_bounds(self) -> None:
        with pytest.raises(ValueError):
            HardenSettings(request_timeout_s=0.0)
        with pytest.raises(ValueError):
            HardenSettings(request_timeout_s=1000.0)
