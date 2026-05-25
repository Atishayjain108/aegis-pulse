"""tests/unit/llm/test_guardrails.py — Guardrails and PII scrubber tests"""

from __future__ import annotations
import pytest


class TestPIIScrubber:

    def test_scrubs_email(self):
        from aegis.llm.guardrails.pii_scrubber import PIIScrubber
        s = PIIScrubber()
        clean, result = s.scrub("Contact john.doe@example.com for details")
        assert "[EMAIL_REDACTED]" in clean
        assert result.replacements.get("email", 0) == 1

    def test_scrubs_ssn(self):
        from aegis.llm.guardrails.pii_scrubber import PIIScrubber
        s = PIIScrubber()
        clean, result = s.scrub("SSN: 123-45-6789")
        assert "[SSN_REDACTED]" in clean

    def test_scrubs_us_phone(self):
        from aegis.llm.guardrails.pii_scrubber import PIIScrubber
        s = PIIScrubber()
        clean, result = s.scrub("Call 555-867-5309 now")
        assert "[PHONE_REDACTED]" in clean

    def test_scrubs_ipv4(self):
        from aegis.llm.guardrails.pii_scrubber import PIIScrubber
        s = PIIScrubber()
        clean, result = s.scrub("Server at 192.168.1.100")
        assert "[IP_REDACTED]" in clean

    def test_clean_text_unchanged(self):
        from aegis.llm.guardrails.pii_scrubber import PIIScrubber
        s = PIIScrubber()
        text = "This is a clean business message about AI chips."
        clean, result = s.scrub(text)
        assert clean == text
        assert not result.was_modified

    def test_disabled_returns_original(self):
        from aegis.llm.guardrails.pii_scrubber import PIIScrubber
        s = PIIScrubber(enabled=False)
        text = "My SSN is 123-45-6789"
        clean, result = s.scrub(text)
        assert clean == text

    def test_scrub_messages_cleans_all(self):
        from aegis.llm.guardrails.pii_scrubber import PIIScrubber
        s = PIIScrubber()
        messages = [
            {"role": "user", "content": "Email me at test@test.com"},
            {"role": "assistant", "content": "Clean reply"},
        ]
        cleaned, result = s.scrub_messages(messages)
        assert "[EMAIL_REDACTED]" in cleaned[0]["content"]
        assert cleaned[1]["content"] == "Clean reply"
        assert result.total_replacements == 1

    def test_rule_names_returned(self):
        from aegis.llm.guardrails.pii_scrubber import PIIScrubber
        s = PIIScrubber()
        names = s.rule_names()
        assert "email" in names
        assert "ssn" in names

    def test_add_custom_rule(self):
        import re
        from aegis.llm.guardrails.pii_scrubber import PIIRule, PIIScrubber
        s = PIIScrubber()
        rule = PIIRule(
            name="custom_id",
            pattern=re.compile(r"\bAEGIS-\d{6}\b"),
            placeholder="[ID_REDACTED]",
        )
        s.add_rule(rule)
        clean, result = s.scrub("Reference AEGIS-123456 here")
        assert "[ID_REDACTED]" in clean


class TestGuardrailsValidatorExtended:
    """Additional edge-case tests for GuardrailsValidator."""

    def test_empty_output_passes(self):
        from aegis.llm.guardrails.validator import GuardrailsValidator
        v = GuardrailsValidator()
        v.validate("")  # Should not raise

    def test_multiple_custom_validators_all_run(self):
        from aegis.llm.guardrails.validator import GuardrailsValidator, ValidationResult

        call_log = []

        def v1(output, ctx):
            call_log.append("v1")
            return ValidationResult(passed=True, rule="v1")

        def v2(output, ctx):
            call_log.append("v2")
            return ValidationResult(passed=True, rule="v2")

        v = GuardrailsValidator(custom_validators=[v1, v2])
        v.validate("hello")
        assert "v1" in call_log
        assert "v2" in call_log
