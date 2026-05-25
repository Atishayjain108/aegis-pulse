"""
aegis.llm.prompts.loader — Singleton PromptRegistry loader
===========================================================

Provides module-level ``get_registry()`` and ``render()`` functions that
lazy-initialise a single ``PromptRegistry`` instance per process.

This avoids re-reading templates on every agent call while remaining
testable (call ``reset_registry()`` between tests).

Author: AEGIS Engineering
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog

from aegis.llm.registry.prompt_registry import PromptRegistry

_log = structlog.get_logger("aegis.llm.prompts.loader")

_registry: PromptRegistry | None = None

# Default template directory — can be overridden via env var
_DEFAULT_TEMPLATE_DIR = Path(__file__).parent / "templates"


def get_registry(
    template_dir: str | Path | None = None,
    *,
    force_reload: bool = False,
) -> PromptRegistry:
    """
    Return the singleton ``PromptRegistry``, creating it on first call.

    Parameters
    ----------
    template_dir:
        Override the template directory.  Defaults to the ``templates/``
        subdirectory next to this file, or ``AEGIS_PROMPT_TEMPLATE_DIR``
        env var if set.
    force_reload:
        If ``True``, discard the cached instance and re-load from disk.
        Use after adding new template files at runtime.

    Returns
    -------
    PromptRegistry
        Loaded and ready-to-use registry.
    """
    global _registry  # noqa: PLW0603

    if _registry is not None and not force_reload:
        return _registry

    # Resolve template directory
    if template_dir is None:
        env_dir = os.environ.get("AEGIS_PROMPT_TEMPLATE_DIR")
        template_dir = Path(env_dir) if env_dir else _DEFAULT_TEMPLATE_DIR

    _registry = PromptRegistry(template_dir)
    count = _registry.load_all()
    _log.info("prompts.loader.initialised", template_count=count, dir=str(template_dir))
    return _registry


def render(template_name: str, **variables: Any) -> str:
    """
    Render a named prompt template with the given variables.

    Convenience wrapper around ``get_registry().render()``.

    Parameters
    ----------
    template_name:
        Name field from the template's YAML front-matter.
    **variables:
        Template variables — all ``required_vars`` must be provided.

    Returns
    -------
    str
        Rendered prompt string.

    Raises
    ------
    InvalidPrompt
        When the template is not found or required vars are missing.

    Example
    -------
    .. code-block:: python

        from aegis.llm.prompts import render

        prompt = render(
            "scout_analysis",
            trend_title="AI chips",
            signal_count=850,
            platforms=["reddit", "tiktok"],
            velocity_1h=42.5,
            sentiment=0.72,
            commercial_intent=0.81,
        )
    """
    return get_registry().render(template_name, **variables)


def reset_registry() -> None:
    """
    Reset the singleton registry.

    Call between tests or when template files change on disk.
    """
    global _registry  # noqa: PLW0603
    _registry = None
