"""
aegis.llm.prompts — Prompt template package
============================================

Public API for the prompt subsystem:

    from aegis.llm.prompts import get_registry, render

    rendered = render("scout_analysis", trend_title="AI chips", ...)
"""

from aegis.llm.prompts.loader import get_registry, render

__all__ = ["get_registry", "render"]
