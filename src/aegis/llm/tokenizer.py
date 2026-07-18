"""
aegis.llm.tokenizer — Token count estimation
=============================================

Provides lightweight token count estimation without requiring tiktoken
or any model-specific library.

Two modes:
  1. **Heuristic** (always available) — rule-of-thumb: ~4 chars per token
     for English text, ~3 chars for code, ~2 chars for Chinese/Japanese.
     Accurate to ±20% which is sufficient for context-window pre-checks.

  2. **tiktoken** (optional, if installed) — exact BPE token count for
     OpenAI-compatible models.  Falls back to heuristic if not available.

Usage::

    from aegis.llm.tokenizer import count_tokens, count_messages

    n = count_tokens("Hello, world!")          # heuristic: 3
    n = count_messages(messages)               # total across all messages
    fits = fits_in_context(messages, limit=8192)

Author: AEGIS Engineering
"""

from __future__ import annotations

import re
from typing import Any

import structlog

_log = structlog.get_logger("aegis.llm.tokenizer")

# ---------------------------------------------------------------------------
# Attempt to import tiktoken for exact counts
# ---------------------------------------------------------------------------

try:
    import tiktoken as _tiktoken

    _TIKTOKEN_AVAILABLE = True
    _log.debug("tokenizer.tiktoken_available")
except ImportError:
    _tiktoken = None  # type: ignore[assignment]
    _TIKTOKEN_AVAILABLE = False

# Rough chars-per-token estimates by content type
_CHARS_PER_TOKEN_ENGLISH = 4.0
_CHARS_PER_TOKEN_CODE = 3.0
_CHARS_PER_TOKEN_CJK = 1.5   # Chinese / Japanese / Korean

# Message overhead: role + wrapper tokens (~4 per message)
_TOKENS_PER_MESSAGE = 4
_TOKENS_PER_REPLY_PRIMER = 3  # OpenAI's reply primer overhead


def _is_code(text: str) -> bool:
    """Heuristic: detect code-heavy text."""
    code_indicators = [
        r"def \w+\(",
        r"import \w+",
        r"class \w+",
        r"\{[\s\S]*\}",
        r"function\s+\w+\s*\(",
    ]
    return any(re.search(p, text) for p in code_indicators)


def _has_cjk(text: str) -> bool:
    """Detect CJK character presence."""
    cjk_pattern = re.compile(r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]")
    return bool(cjk_pattern.search(text))


def count_tokens(text: str, *, model: str = "gpt-4") -> int:
    """
    Estimate the number of tokens in ``text``.

    Uses tiktoken for exact counts when available; falls back to
    heuristic estimation.

    Parameters
    ----------
    text:
        Input text to count.
    model:
        Model name — used to select the correct tiktoken encoding.
        Ignored when falling back to heuristic.

    Returns
    -------
    int
        Estimated token count.
    """
    if not text:
        return 0

    if _TIKTOKEN_AVAILABLE and _tiktoken is not None:
        try:
            enc = _tiktoken.encoding_for_model(model)
            return len(enc.encode(text))
        except Exception as exc:
            _log.debug("tokenizer.tiktoken_encode_failed", model=model, error=str(exc))

    # Heuristic mode
    if _has_cjk(text):
        chars_per_token = _CHARS_PER_TOKEN_CJK
    elif _is_code(text):
        chars_per_token = _CHARS_PER_TOKEN_CODE
    else:
        chars_per_token = _CHARS_PER_TOKEN_ENGLISH

    return max(1, round(len(text) / chars_per_token))


def count_messages(
    messages: list[dict[str, str]],
    *,
    model: str = "gpt-4",
) -> int:
    """
    Estimate total token count for a list of chat messages.

    Includes message overhead tokens (role encoding, separators).

    Parameters
    ----------
    messages:
        List of ``{"role": str, "content": str}`` dicts.
    model:
        Model name for tiktoken encoding selection.

    Returns
    -------
    int
        Total estimated token count for the full message list.
    """
    total = _TOKENS_PER_REPLY_PRIMER
    for msg in messages:
        total += _TOKENS_PER_MESSAGE
        total += count_tokens(msg.get("content", ""), model=model)
        total += count_tokens(msg.get("role", ""), model=model)
    return total


def fits_in_context(
    messages: list[dict[str, str]],
    *,
    context_limit: int,
    max_output_tokens: int = 2048,
    model: str = "gpt-4",
    safety_margin: float = 0.9,
) -> bool:
    """
    Check whether ``messages`` fit within a provider's context window.

    Applies a safety margin to account for estimation inaccuracy and
    to leave room for the model's output.

    Parameters
    ----------
    messages:
        Chat messages to check.
    context_limit:
        Provider context window in tokens.
    max_output_tokens:
        Reserved tokens for model output.
    model:
        Model name for tiktoken encoding.
    safety_margin:
        Fraction of context_limit to use (default: 0.9 = 90%).

    Returns
    -------
    bool
        ``True`` if the messages fit with room for output.
    """
    input_tokens = count_messages(messages, model=model)
    effective_limit = int(context_limit * safety_margin) - max_output_tokens
    return input_tokens <= effective_limit


def truncate_messages(
    messages: list[dict[str, str]],
    *,
    context_limit: int,
    max_output_tokens: int = 2048,
    model: str = "gpt-4",
    safety_margin: float = 0.9,
    preserve_system: bool = True,
    preserve_last_n: int = 2,
) -> list[dict[str, str]]:
    """
    Truncate a message list to fit within the context window.

    Removes messages from the middle of the conversation, preserving:
    - System messages (when ``preserve_system=True``)
    - The last ``preserve_last_n`` messages

    Parameters
    ----------
    messages:
        Input messages to truncate.
    context_limit:
        Provider context window in tokens.
    max_output_tokens:
        Reserved tokens for model output.
    model:
        Tokeniser model.
    safety_margin:
        Fraction of context limit to use.
    preserve_system:
        Always keep system messages.
    preserve_last_n:
        Always keep the last N messages regardless of truncation.

    Returns
    -------
    list[dict[str, str]]
        Truncated message list that fits in the context.
    """
    if fits_in_context(
        messages,
        context_limit=context_limit,
        max_output_tokens=max_output_tokens,
        model=model,
        safety_margin=safety_margin,
    ):
        return messages

    # Split into system, middle, and tail
    system_msgs = [m for m in messages if m.get("role") == "system"] if preserve_system else []
    non_system = [m for m in messages if m.get("role") != "system"]
    tail = non_system[-preserve_last_n:] if preserve_last_n else []
    middle = non_system[:-preserve_last_n] if preserve_last_n else non_system

    effective_limit = int(context_limit * safety_margin) - max_output_tokens
    result = system_msgs + tail
    current_tokens = count_messages(result, model=model)

    # Add middle messages from newest to oldest until we hit the limit
    for msg in reversed(middle):
        msg_tokens = count_tokens(msg.get("content", ""), model=model) + _TOKENS_PER_MESSAGE
        if current_tokens + msg_tokens > effective_limit:
            break
        result.insert(len(system_msgs), msg)
        current_tokens += msg_tokens

    _log.debug(
        "tokenizer.truncated",
        original_count=len(messages),
        truncated_count=len(result),
        estimated_tokens=current_tokens,
    )
    return result


def token_stats(text: str) -> dict[str, Any]:
    """Return a diagnostic dict with token count and detection method."""
    heuristic = max(1, round(len(text) / _CHARS_PER_TOKEN_ENGLISH))
    tiktoken_count = None
    if _TIKTOKEN_AVAILABLE and _tiktoken is not None:
        try:
            enc = _tiktoken.encoding_for_model("gpt-4")
            tiktoken_count = len(enc.encode(text))
        except Exception as exc:
            _log.debug("tokenizer.tiktoken_encode_failed", model="gpt-4", error=str(exc))
    return {
        "char_count": len(text),
        "heuristic_tokens": heuristic,
        "tiktoken_tokens": tiktoken_count,
        "method": "tiktoken" if tiktoken_count is not None else "heuristic",
        "tiktoken_available": _TIKTOKEN_AVAILABLE,
    }
