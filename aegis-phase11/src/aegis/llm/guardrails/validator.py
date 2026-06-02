"""
aegis.llm.guardrails.validator — GuardrailsValidator
=====================================================

Validates LLM output against a configurable rule set **before** it is
returned to the caller.  Enforces:

1. Maximum output length guard.
2. PII leakage detection (regex scan on output).
3. Toxic content detection (keyword blocklist — lightweight, no model).
4. Custom user-supplied validators (callable hooks).

If any rule fires, ``GuardrailBlock`` is raised with the triggering rule
and the offending snippet (truncated, safe for logs).

Design note: guardrails run **synchronously** inside the async call
chain — they are CPU-bound regex / string ops that complete in < 1 ms
and do not justify a thread pool.

Author: AEGIS Engineering
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog

from aegis.llm.constants import (
    GUARDRAIL_MAX_OUTPUT_CHARS,
    GUARDRAIL_PII_PATTERNS,
)
from aegis.llm.errors import GuardrailBlock

_log = structlog.get_logger("aegis.llm.guardrails")

# ---------------------------------------------------------------------------
# Built-in toxic keyword blocklist (safe for logs)
# ---------------------------------------------------------------------------

_TOXIC_PATTERNS: list[str] = [
    # Financial fraud / manipulation signals — block if model hallucinates these
    r"\b(pump[- ]and[- ]dump|insider[- ]trading|market[- ]manipulation)\b",
    # Explicit harmful content markers
    r"\b(make[- ]a[- ]bomb|synthesize[- ]drugs)\b",
]


@dataclass
class ValidationResult:
    """Outcome of a guardrail validation pass."""

    passed: bool
    rule: str = ""
    snippet: str = ""
    detail: str = ""


ValidatorFn = Callable[[str, dict[str, Any]], ValidationResult]
"""
Type alias for a custom validator function.

Signature: ``(output: str, context: dict) -> ValidationResult``
"""


class GuardrailsValidator:
    """
    Configurable output validator for LLM responses.

    Parameters
    ----------
    max_output_chars:
        Hard limit on output length.  Outputs exceeding this are blocked
        (not truncated — truncation would silently discard content).
    pii_patterns:
        List of regex patterns to detect PII.
    toxic_patterns:
        List of regex patterns to detect toxic/harmful content.
    custom_validators:
        Additional ``ValidatorFn`` callables appended to the chain.
    strict:
        When ``True`` (default), any failed rule raises ``GuardrailBlock``.
        When ``False``, failures are logged but not raised (permissive mode
        useful for eval/debugging).

    Example
    -------
    .. code-block:: python

        validator = GuardrailsValidator()
        validator.validate("Hello, my SSN is 123-45-6789")
        # raises GuardrailBlock(rule="pii_detection")
    """

    def __init__(
        self,
        *,
        max_output_chars: int = GUARDRAIL_MAX_OUTPUT_CHARS,
        pii_patterns: list[str] | None = None,
        toxic_patterns: list[str] | None = None,
        custom_validators: list[ValidatorFn] | None = None,
        strict: bool = True,
    ) -> None:
        self._max_chars = max_output_chars
        self._pii_re = [
            re.compile(p, re.IGNORECASE)
            for p in (pii_patterns or GUARDRAIL_PII_PATTERNS)
        ]
        self._toxic_re = [
            re.compile(p, re.IGNORECASE)
            for p in (toxic_patterns or _TOXIC_PATTERNS)
        ]
        self._custom_validators: list[ValidatorFn] = custom_validators or []
        self._strict = strict

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(
        self,
        output: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """
        Run all validation rules against ``output``.

        Raises
        ------
        GuardrailBlock
            When any rule fails and ``strict=True``.
        """
        ctx = context or {}
        rules = [
            self._check_length,
            self._check_pii,
            self._check_toxic,
        ]

        for rule_fn in rules:
            result = rule_fn(output)
            self._handle(result)

        for custom_fn in self._custom_validators:
            result = custom_fn(output, ctx)
            self._handle(result)

    def add_validator(self, fn: ValidatorFn) -> None:
        """Register a custom validator at runtime."""
        self._custom_validators.append(fn)

    # ------------------------------------------------------------------
    # Built-in rule implementations
    # ------------------------------------------------------------------

    def _check_length(self, output: str) -> ValidationResult:
        if len(output) > self._max_chars:
            return ValidationResult(
                passed=False,
                rule="max_output_length",
                snippet=f"length={len(output)} > limit={self._max_chars}",
                detail=f"Output length {len(output)} exceeds {self._max_chars} chars",
            )
        return ValidationResult(passed=True, rule="max_output_length")

    def _check_pii(self, output: str) -> ValidationResult:
        for pattern in self._pii_re:
            match = pattern.search(output)
            if match:
                # Safe snippet: show pattern name, not the matched value
                return ValidationResult(
                    passed=False,
                    rule="pii_detection",
                    snippet=f"pattern={pattern.pattern[:60]}",
                    detail="PII pattern detected in LLM output",
                )
        return ValidationResult(passed=True, rule="pii_detection")

    def _check_toxic(self, output: str) -> ValidationResult:
        for pattern in self._toxic_re:
            match = pattern.search(output)
            if match:
                return ValidationResult(
                    passed=False,
                    rule="toxic_content",
                    snippet=f"pattern={pattern.pattern[:60]}",
                    detail="Toxic/harmful content pattern detected",
                )
        return ValidationResult(passed=True, rule="toxic_content")

    # ------------------------------------------------------------------
    # Handler
    # ------------------------------------------------------------------

    def _handle(self, result: ValidationResult) -> None:
        if result.passed:
            return
        _log.warning(
            "guardrail.block",
            rule=result.rule,
            snippet=result.snippet,
            detail=result.detail,
        )
        if self._strict:
            raise GuardrailBlock(
                result.detail,
                rule=result.rule,
            )
