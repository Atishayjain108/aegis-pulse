"""
aegis.security.pii.scrubber — PII detection and redaction pipeline.

Runs on every raw signal *before* any database persistence.  Two-pass:

    Pass 1 — Regex patterns (email, phone, IP, credit card, SSN, UUID, etc.)
    Pass 2 — spaCy NER (PERSON, ORG, GPE, LOC, DATE where applicable)

Configurable behaviour per field:
    - ``redact``   — replace with ``[REDACTED]``
    - ``hash``     — replace with ``sha256(value)[:16]``
    - ``mask``     — replace with ``***...***`` (preserves length class)
    - ``drop``     — remove field entirely

Error codes: AEGIS-SEC-0031..0040
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

from aegis.security.config import SecurityConfig, get_security_config

_log = structlog.get_logger(__name__)


class PIIAction(str, Enum):
    """What to do when PII is found."""

    REDACT = "redact"
    HASH = "hash"
    MASK = "mask"
    DROP = "drop"


@dataclass(frozen=True)
class PIIPattern:
    """A compiled regex pattern with its associated action and label."""

    label: str
    pattern: re.Pattern[str]
    action: PIIAction = PIIAction.REDACT

    def apply(self, text: str, placeholder: str) -> tuple[str, int]:
        """Apply redaction to *text*.

        Returns
        -------
        tuple[str, int]
            (modified text, count of replacements made)
        """
        count = 0

        def _replace(m: re.Match[str]) -> str:
            nonlocal count
            count += 1
            value = m.group(0)
            if self.action == PIIAction.HASH:
                return hashlib.sha256(value.encode()).hexdigest()[:16]
            if self.action == PIIAction.MASK:
                return "*" * min(len(value), 8)
            if self.action == PIIAction.DROP:
                return ""
            return placeholder

        result = self.pattern.sub(_replace, text)
        return result, count


# ── Built-in PII patterns ─────────────────────────────────────────────────── #
_PATTERNS: list[PIIPattern] = [
    PIIPattern(
        label="email",
        pattern=re.compile(
            r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
            re.IGNORECASE,
        ),
        action=PIIAction.HASH,  # hash instead of full redact (useful for dedup)
    ),
    PIIPattern(
        label="phone_e164",
        # Must start with + or have specific phone format; not match IPs
        pattern=re.compile(
            r"(?<![\d.])(?:\+1[\s\-.]?)?(?:\(?\d{3}\)?[\s\-.]){1,2}\d{4}(?![\d.])"
        ),
        action=PIIAction.REDACT,
    ),
    PIIPattern(
        label="credit_card",
        pattern=re.compile(r"\b(?:\d[ -]?){13,16}\b"),
        action=PIIAction.REDACT,
    ),
    PIIPattern(
        label="ssn_us",
        pattern=re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        action=PIIAction.REDACT,
    ),
    PIIPattern(
        label="ipv4",
        pattern=re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
            r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
        ),
        action=PIIAction.MASK,
    ),
    PIIPattern(
        label="ipv6",
        pattern=re.compile(
            r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"
        ),
        action=PIIAction.MASK,
    ),
    PIIPattern(
        label="aadhaar_in",  # Indian national ID
        pattern=re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),
        action=PIIAction.REDACT,
    ),
    PIIPattern(
        label="pan_in",  # Indian PAN card
        pattern=re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"),
        action=PIIAction.REDACT,
    ),
    PIIPattern(
        label="passport",
        pattern=re.compile(r"\b[A-Z]{1,2}[0-9]{6,9}\b"),
        action=PIIAction.REDACT,
    ),
    PIIPattern(
        label="jwt_token",
        pattern=re.compile(r"eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
        action=PIIAction.REDACT,
    ),
    PIIPattern(
        label="api_key_generic",
        pattern=re.compile(
            r"(?i)(?:api[_\-]?key|secret|token|password|passwd|pwd)"
            r"[\s:=]+[\"']?([A-Za-z0-9_\-./+]{16,})[\"']?"
        ),
        action=PIIAction.REDACT,
    ),
    PIIPattern(
        label="aws_access_key",
        pattern=re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        action=PIIAction.REDACT,
    ),
    PIIPattern(
        label="aws_secret_key",
        pattern=re.compile(r"(?i)aws.{0,20}secret.{0,20}[=:][\"']?[A-Za-z0-9+/]{40}"),
        action=PIIAction.REDACT,
    ),
]


@dataclass
class ScrubReport:
    """Summary of a single scrub operation."""

    original_length: int = 0
    scrubbed_length: int = 0
    replacements: dict[str, int] = field(default_factory=dict)
    ner_entities_removed: int = 0

    @property
    def total_replacements(self) -> int:
        return sum(self.replacements.values())

    @property
    def was_modified(self) -> bool:
        return self.total_replacements > 0 or self.ner_entities_removed > 0


class PIIScrubber:
    """Stateful PII scrubbing pipeline.

    Initialisation loads the spaCy model lazily on first call so import
    of this module is cheap even if spaCy is not installed.

    Parameters
    ----------
    config:
        Injected config; defaults to global singleton.
    extra_patterns:
        Additional ``PIIPattern`` instances to add to the built-in set.
    """

    def __init__(
        self,
        config: SecurityConfig | None = None,
        extra_patterns: list[PIIPattern] | None = None,
    ) -> None:
        self._cfg = config or get_security_config()
        self._patterns: list[PIIPattern] = list(_PATTERNS)
        if extra_patterns:
            self._patterns.extend(extra_patterns)
        self._nlp: Any = None  # spaCy model, loaded lazily
        self._spacy_available: bool | None = None  # None = unknown

    # ── Public API ─────────────────────────────────────────────────────────── #

    def scrub_text(self, text: str) -> tuple[str, ScrubReport]:
        """Scrub a single string value.

        Parameters
        ----------
        text:
            Input text that may contain PII.

        Returns
        -------
        tuple[str, ScrubReport]
            ``(scrubbed_text, report)`` — the report records what was changed.
        """
        if not self._cfg.pii_enabled or not text:
            return text, ScrubReport(original_length=len(text), scrubbed_length=len(text))

        report = ScrubReport(original_length=len(text))
        result = text

        # Pass 1: Regex
        for pat in self._patterns:
            result, count = pat.apply(result, self._cfg.pii_redaction_placeholder)
            if count:
                report.replacements[pat.label] = (
                    report.replacements.get(pat.label, 0) + count
                )

        # Pass 2: spaCy NER
        if self._spacy_available is not False:
            result, ner_count = self._ner_scrub(result)
            report.ner_entities_removed = ner_count

        report.scrubbed_length = len(result)
        if report.was_modified:
            _log.debug(
                "pii.scrubbed",
                replacements=report.replacements,
                ner_removed=report.ner_entities_removed,
            )
        return result, report

    def scrub_dict(
        self,
        data: dict[str, Any],
        *,
        text_fields: set[str] | None = None,
    ) -> tuple[dict[str, Any], ScrubReport]:
        """Scrub string values in a dict (recursively).

        Parameters
        ----------
        data:
            Input dictionary (e.g. a signal's ``raw_json``).
        text_fields:
            Restrict scrubbing to these specific keys; if ``None`` all
            string fields are scrubbed.

        Returns
        -------
        tuple[dict[str, Any], ScrubReport]
        """
        total_report = ScrubReport()
        result: dict[str, Any] = {}

        for k, v in data.items():
            if isinstance(v, str):
                if text_fields is None or k in text_fields:
                    scrubbed, rep = self.scrub_text(v)
                    result[k] = scrubbed
                    total_report.original_length += rep.original_length
                    total_report.scrubbed_length += rep.scrubbed_length
                    for label, count in rep.replacements.items():
                        total_report.replacements[label] = (
                            total_report.replacements.get(label, 0) + count
                        )
                    total_report.ner_entities_removed += rep.ner_entities_removed
                else:
                    result[k] = v
            elif isinstance(v, dict):
                scrubbed_nested, rep = self.scrub_dict(v, text_fields=text_fields)
                result[k] = scrubbed_nested
                total_report.original_length += rep.original_length
                total_report.scrubbed_length += rep.scrubbed_length
                for label, count in rep.replacements.items():
                    total_report.replacements[label] = (
                        total_report.replacements.get(label, 0) + count
                    )
                total_report.ner_entities_removed += rep.ner_entities_removed
            elif isinstance(v, list):
                result[k] = [
                    self.scrub_text(item)[0] if isinstance(item, str) else item
                    for item in v
                ]
            else:
                result[k] = v

        return result, total_report

    # ── spaCy NER ─────────────────────────────────────────────────────────── #

    def _ner_scrub(self, text: str) -> tuple[str, int]:
        """Use spaCy NER to remove PERSON names and similar entities."""
        nlp = self._get_nlp()
        if nlp is None:
            return text, 0

        doc = nlp(text)
        result = text
        count = 0
        # Process in reverse order to keep character offsets stable
        for ent in reversed(doc.ents):
            if ent.label_ in {"PERSON"}:
                placeholder = self._cfg.pii_redaction_placeholder
                result = result[: ent.start_char] + placeholder + result[ent.end_char :]
                count += 1
        return result, count

    def _get_nlp(self) -> Any:  # noqa: ANN401
        """Lazy-load spaCy model; returns None if unavailable."""
        if self._spacy_available is False:
            return None
        if self._nlp is not None:
            return self._nlp

        try:
            import spacy  # type: ignore[import-untyped]

            self._nlp = spacy.load(self._cfg.pii_spacy_model)
            self._spacy_available = True
            _log.info("pii.spacy_loaded", model=self._cfg.pii_spacy_model)
        except ImportError:
            _log.info("pii.spacy_not_installed", note="Install with: pip install spacy")
            self._spacy_available = False
        except OSError:
            _log.warning(
                "pii.spacy_model_missing",
                model=self._cfg.pii_spacy_model,
                note=f"Run: python -m spacy download {self._cfg.pii_spacy_model}",
            )
            self._spacy_available = False
        return self._nlp
