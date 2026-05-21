"""Tests for aegis.agents.llm.prompts — Jinja2 template registry."""
from __future__ import annotations

import pytest

from aegis.agents.llm.prompts import PromptNotFoundError, list_available, render


def test_list_available_returns_list() -> None:
    templates = list_available()
    assert isinstance(templates, list)
    assert len(templates) > 0
    assert "scout" in templates


def test_list_available_sorted() -> None:
    templates = list_available()
    assert templates == sorted(templates)


def test_get_template_not_found_raises() -> None:
    from aegis.agents.llm.prompts import _get_template  # type: ignore[attr-defined]
    with pytest.raises(PromptNotFoundError):
        _get_template("nonexistent_agent_xyzzy_that_will_never_exist")


def test_render_scout_returns_text_and_digest() -> None:
    text, digest = render(
        "scout",
        title="Test trend",
        summary="A short summary",
        representative_text="Sample post text",
        platforms="reddit, twitter",
        signal_count=42,
        breakout_class="strong",
        heuristic_score=0.82,
    )
    assert len(text) > 0
    assert len(digest) == 16
    assert all(c in "0123456789abcdef" for c in digest)


def test_render_unknown_agent_raises() -> None:
    with pytest.raises(PromptNotFoundError):
        render("definitely_no_such_agent_ever")


def test_list_available_returns_empty_when_prompt_dir_missing(monkeypatch) -> None:
    import pathlib

    from aegis.agents.llm import prompts

    monkeypatch.setattr(prompts, "_PROMPT_DIR", pathlib.Path("/nonexistent/path/xyzzy_aegis_test"))
    result = list_available()
    assert result == []
