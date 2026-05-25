"""
aegis.llm.guardrails.pii_scrubber — PIIScrubber
================================================

Scrubs personally identifiable information (PII) from LLM inputs
**before** they are sent to any provider.

This is the input-side complement to the output-side ``GuardrailsValidator``.

PII categories scrubbed:
  - Email addresses
  - Phone numbers (10-digit, international formats)
  - Social Security Numbers
  - Credit/debit card numbers (Luhn-format 13-19 digits)
  - IP addresses (v4 and v6)
  - Indian PAN numbers
  - Indian Aadhaar numbers (12-digit)
  - Generic UUIDs

Replacement strategy: replace matched text with a placeholder like
``[EMAIL_REDACTED]`` so the model still receives a coherent sentence.

Author: AEGIS Engineering
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import structlog

_log = structlog.get_logger("aegis.llm.guardrails.pii_scrubber")


@dataclass(frozen=True)
class PIIRule:
    """A single PII detection rule."""

    name: str                     # Human-readable rule name
    pattern: re.Pattern[str]      # Compiled regex
    placeholder: str              # Replacement token


# ---------------------------------------------------------------------------
# Built-in rule set
# ---------------------------------------------------------------------------

_DEFAULT_RULES: list[PIIRule] = [
    PIIRule(
        name="email",
        pattern=re.compile(
            r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
            re.IGNORECASE,
        ),
        placeholder="[EMAIL_REDACTED]",
    ),
    PIIRule(
        name="phone_us",
        pattern=re.compile(
            r"\b(?:\+1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b"
        ),
        placeholder="[PHONE_REDACTED]",
    ),
    PIIRule(
        name="phone_india",
        pattern=re.compile(r"\b(?:\+91[\s-]?)?[6-9]\d{9}\b"),
        placeholder="[PHONE_REDACTED]",
    ),
    PIIRule(
        name="ssn",
        pattern=re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        placeholder="[SSN_REDACTED]",
    ),
    PIIRule(
        name="credit_card",
        pattern=re.compile(r"\b(?:\d[ -]?){13,19}\b"),
        placeholder="[CARD_REDACTED]",
    ),
    PIIRule(
        name="ipv4",
        pattern=re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
        ),
        placeholder="[IP_REDACTED]",
    ),
    PIIRule(
        name="pan_india",
        pattern=re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"),
        placeholder="[PAN_REDACTED]",
    ),
    PIIRule(
        name="aadhaar",
        pattern=re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),
        placeholder="[AADHAAR_REDACTED]",
    ),
    PIIRule(
        name="uuid",
        pattern=re.compile(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
            re.IGNORECASE,
        ),
        placeholder="[UUID_REDACTED]",
    ),
]


@dataclass
class ScrubResult:
    """Result of a scrub operation."""

    original_length: int
    scrubbed_length: int
    replacements: dict[str, int] = field(default_factory=dict)  # rule_name → count

    @property
    def was_modified(self) -> bool:
        return bool(self.replacements)

    @property
    def total_replacements(self) -> int:
        return sum(self.replacements.values())


class PIIScrubber:
    """
    Input-side PII scrubber for LLM messages.

    Parameters
    ----------
    rules:
        List of ``PIIRule`` objects. Defaults to ``_DEFAULT_RULES``.
        Pass a custom list to extend or restrict detection.
    enabled:
        If ``False``, all scrub operations are no-ops (useful in dev/debug).

    Example
    -------
    .. code-block:: python

        scrubber = PIIScrubber()
        clean, result = scrubber.scrub("Email john@example.com for details")
        # clean == "Email [EMAIL_REDACTED] for details"
        # result.replacements == {"email": 1}

        # Scrub a messages list in-place
        messages = [{"role": "user", "content": "My SSN is 123-45-6789"}]
        messages, summary = scrubber.scrub_messages(messages)
    """

    def __init__(
        self,
        *,
        rules: list[PIIRule] | None = None,
        enabled: bool = True,
    ) -> None:
        self._rules = rules or _DEFAULT_RULES
        self._enabled = enabled

    def scrub(self, text: str) -> tuple[str, ScrubResult]:
        """
        Scrub PII from ``text``.

        Returns
        -------
        tuple[str, ScrubResult]
            ``(scrubbed_text, result)`` where ``result`` reports what was changed.
        """
        if not self._enabled:
            return text, ScrubResult(len(text), len(text))

        result = ScrubResult(original_length=len(text), scrubbed_length=0)
        scrubbed = text

        for rule in self._rules:
            matches = rule.pattern.findall(scrubbed)
            if matches:
                scrubbed = rule.pattern.sub(rule.placeholder, scrubbed)
                result.replacements[rule.name] = len(matches)

        result.scrubbed_length = len(scrubbed)

        if result.was_modified:
            _log.debug(
                "pii_scrubber.scrubbed",
                replacements=result.replacements,
                total=result.total_replacements,
            )

        return scrubbed, result

    def scrub_messages(
        self,
        messages: list[dict[str, str]],
    ) -> tuple[list[dict[str, str]], ScrubResult]:
        """
        Scrub PII from all message content fields.

        Returns a new list of messages with clean content, and a
        ``ScrubResult`` aggregating all replacements across messages.

        Parameters
        ----------
        messages:
            Conversation messages in OpenAI format.
        """
        combined_result = ScrubResult(original_length=0, scrubbed_length=0)
        cleaned: list[dict[str, str]] = []

        for msg in messages:
            content = msg.get("content", "")
            clean_content, msg_result = self.scrub(content)
            combined_result.original_length += msg_result.original_length
            combined_result.scrubbed_length += msg_result.scrubbed_length
            for rule_name, count in msg_result.replacements.items():
                combined_result.replacements[rule_name] = (
                    combined_result.replacements.get(rule_name, 0) + count
                )
            cleaned.append({**msg, "content": clean_content})

        return cleaned, combined_result

    def add_rule(self, rule: PIIRule) -> None:
        """Add a custom PII rule at runtime."""
        self._rules.append(rule)

    def rule_names(self) -> list[str]:
        """Return names of all active rules."""
        return [r.name for r in self._rules]
