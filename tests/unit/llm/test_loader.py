"""Unit tests for aegis.llm.prompts.loader."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from aegis.llm.prompts.loader import get_registry, render, reset_registry
from aegis.llm.registry.prompt_registry import PromptRegistry


@pytest.fixture(autouse=True)
def _reset_between_tests() -> None:
    """Ensure singleton is cleared before and after every test."""
    reset_registry()
    yield
    reset_registry()


class TestGetRegistry:
    def test_returns_prompt_registry_instance(self, tmp_path: Path) -> None:
        reg = get_registry(template_dir=tmp_path)
        assert isinstance(reg, PromptRegistry)

    def test_returns_singleton_on_second_call(self, tmp_path: Path) -> None:
        r1 = get_registry(template_dir=tmp_path)
        r2 = get_registry()
        assert r1 is r2

    def test_force_reload_creates_new_instance(self, tmp_path: Path) -> None:
        r1 = get_registry(template_dir=tmp_path)
        r2 = get_registry(template_dir=tmp_path, force_reload=True)
        assert r1 is not r2

    def test_env_var_overrides_template_dir(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("AEGIS_PROMPT_TEMPLATE_DIR", str(tmp_path))
        reg = get_registry()
        assert isinstance(reg, PromptRegistry)
        # env var path was used — no exception raised for non-existent template dir

    def test_default_template_dir_used_when_no_arg(self) -> None:
        # Should not raise even if called without args (uses built-in templates dir)
        reg = get_registry()
        assert isinstance(reg, PromptRegistry)

    def test_string_path_accepted(self, tmp_path: Path) -> None:
        reg = get_registry(template_dir=str(tmp_path))
        assert isinstance(reg, PromptRegistry)


class TestResetRegistry:
    def test_reset_clears_singleton(self, tmp_path: Path) -> None:
        r1 = get_registry(template_dir=tmp_path)
        reset_registry()
        r2 = get_registry(template_dir=tmp_path)
        assert r1 is not r2

    def test_reset_idempotent_when_none(self) -> None:
        # registry already None after autouse fixture
        reset_registry()  # should not raise
        reset_registry()


class TestRender:
    def test_render_delegates_to_registry(self, tmp_path: Path) -> None:
        # Patch PromptRegistry.render to avoid needing real templates
        with patch.object(PromptRegistry, "render", return_value="rendered text") as mock_render:
            result = render("scout_analysis", trend_title="AI chips", signal_count=10)
            mock_render.assert_called_once()
            assert result == "rendered text"

    def test_render_initialises_singleton_if_needed(self, tmp_path: Path) -> None:
        # After reset, render() should lazily initialise the registry
        with patch.object(PromptRegistry, "render", return_value="ok"):
            result = render("any_template")
        assert result == "ok"
