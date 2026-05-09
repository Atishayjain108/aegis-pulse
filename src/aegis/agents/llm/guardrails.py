"""
Output guardrails for LLM responses.

Models — especially smaller / local ones — are sloppy about JSON.
This module:
  1. Strips Markdown code fences.
  2. Locates the largest balanced `{...}` or `[...]` substring.
  3. Tolerates trailing commas (a common LLM bug) by re-parsing
     with a permissive scanner.
  4. Validates against a pydantic model before returning.

If parsing fails, the caller gets `None` (NOT an exception) — so the
agent can quietly fall back to its heuristic path.

Author: AEGIS Pulse core team
"""
from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*|\s*```\s*$", re.MULTILINE)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")

T = TypeVar("T", bound=BaseModel)


def _find_balanced(text: str, open_ch: str, close_ch: str) -> str | None:
    """Return the longest balanced span starting at first `open_ch`."""
    start = text.find(open_ch)
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    end = -1
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return None
    return text[start : end + 1]


def extract_json(text: str) -> str | None:
    """Return the JSON substring of `text`, or None if none looks valid."""
    if not text:
        return None
    cleaned = _FENCE_RE.sub("", text).strip()

    # Prefer the larger of the two candidate spans.
    obj = _find_balanced(cleaned, "{", "}")
    arr = _find_balanced(cleaned, "[", "]")
    return (obj if len(obj) >= len(arr) else arr) if obj and arr else obj or arr


def parse_json(text: str) -> dict | list | None:
    """Best-effort JSON parse. Returns None on failure (never raises)."""
    candidate = extract_json(text)
    if candidate is None:
        return None
    # First attempt — strict.
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    # Second attempt — strip trailing commas and retry.
    repaired = _TRAILING_COMMA_RE.sub(r"\1", candidate)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        return None


def parse_into(text: str, model_cls: type[T]) -> T | None:
    """Parse JSON and validate against a pydantic model. None on failure."""
    parsed = parse_json(text)
    if parsed is None:
        return None
    try:
        return model_cls.model_validate(parsed)
    except ValidationError:
        return None
