"""
Jinja2 prompt registry.

Prompts live in `src/aegis/agents/prompts/<agent>.jinja2` so they
can be edited without touching code, version-controlled separately,
and hash-pinned for reproducibility. The registry caches compiled
templates and computes a stable SHA256 of the rendered text so
tracing systems (Langfuse) can group runs by prompt version.

Templates have two top-level variables conventionally:
   `system_block` — fixed framing
   `user_block`   — the agent's actual question for this trend

Author: AEGIS Pulse core team
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    Template,
    TemplateNotFound,
    select_autoescape,
)

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


class PromptNotFoundError(KeyError):
    """Raised when an agent asks for a template that doesn't exist."""


@lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_PROMPT_DIR)),
        autoescape=select_autoescape(default=False),  # plain text, not HTML
        undefined=StrictUndefined,  # missing var → loud error, not silent
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )


def _get_template(name: str) -> Template:
    try:
        return _env().get_template(f"{name}.jinja2")
    except TemplateNotFound as exc:
        raise PromptNotFoundError(name) from exc


def render(agent: str, /, **context: Any) -> tuple[str, str]:
    """Render the prompt for `agent` and return (text, version_hash).

    The version hash is the SHA256 of the rendered string truncated
    to 16 hex chars — short enough to be readable in logs, long
    enough to be collision-free in any realistic prompt corpus.
    """
    template = _get_template(agent)
    text = template.render(**context)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return text, digest


def list_available() -> list[str]:
    """List agents that have a prompt template installed."""
    if not _PROMPT_DIR.exists():
        return []
    return sorted(p.stem for p in _PROMPT_DIR.glob("*.jinja2"))
