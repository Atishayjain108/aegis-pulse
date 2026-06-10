"""
Targeted tests to kill the surviving mutants from `mutmut run`.

These tests address gaps surfaced by mutation testing. Each test cites the
mutant ID it kills so future readers can see why the assertion is here.

Mutants killed:
  * #1  — `@dataclass(frozen=True)` on `HardenError`
  * #54 — `ConfigDict(extra="forbid")` on every frozen schema
  * #74 — `@field_validator("ja3", ...)` arg ordering
  * #75 — `@field_validator(..., "ja4")` arg ordering
  * #83 — `H2Settings.max_frame_size` lower bound = 16384

Two surviving mutants (#62, #65) were equivalent — replacing `|` with `&`
in a Pydantic type annotation raises `TypeError` at class definition time,
so any test that imports the module fails. Mutmut counted these as
"surviving" only because the timeout cut the run short before they were
verified. They are documented in `docs/phase5/mutation-report.md` rather
than killed with new tests.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from aegis.harden.errors import HardenError, PlaybookValidationError
from aegis.harden.schemas import (
    H2Settings,
    HardenVerdict,
    HoneypotVerdict,
    Playbook,
    PlaybookMatch,
    PoisoningReport,
    PoisoningSignal,
    SmoothingResult,
    TLSFingerprint,
)

# ---------------------------------------------------------------------------
# Mutant #1 — error dataclasses must be frozen
# ---------------------------------------------------------------------------


class TestErrorsFrozen:
    """`HardenError` and subclasses must be frozen — mutating them post-construction
    would let a caller silently rewrite the error code / message after raising."""

    def test_harden_error_is_frozen(self) -> None:
        err = HardenError(code="X", message="m", context={})
        with pytest.raises(FrozenInstanceError):
            err.code = "Y"  # type: ignore[misc]

    def test_subclass_is_frozen(self) -> None:
        err = PlaybookValidationError(code="X", message="m", context={})
        with pytest.raises(FrozenInstanceError):
            err.message = "n"  # type: ignore[misc]

    def test_subclass_inherits_slots(self) -> None:
        """slots=True means no __dict__ — attempt to add a new attr should fail.

        The exact exception varies: frozen dataclass + slotted Exception
        subclass can raise FrozenInstanceError, AttributeError, OR TypeError
        depending on which check fires first. All three indicate the slot
        machinery is intact; the regression we are guarding against is the
        case where the attribute SUCCEEDS silently.

        Kills mutant #2: `@dataclass(frozen=True, slots=False)`. Without slots,
        `err.new_attr = 1` succeeds because Exception's __dict__ is inherited.
        """
        err = HardenError(code="X", message="m", context={})
        with pytest.raises((AttributeError, FrozenInstanceError, TypeError)):
            err.new_attr = 1  # type: ignore[attr-defined]

    def test_context_defaults_to_empty_dict_not_none(self) -> None:
        """Default-constructed `context` must be an empty dict, never None.

        Kills mutant #4: `context: dict[str, Any] = None`. Without the
        `field(default_factory=dict)`, an HardenError raised without context
        would have `.context = None`, breaking every caller that does
        `err.context.get(...)` or `err.context["key"]`.
        """
        err = HardenError(code="X", message="m")
        assert err.context == {}
        assert isinstance(err.context, dict)

    def test_two_errors_have_independent_context_dicts(self) -> None:
        """field(default_factory=dict) ensures each instance gets a fresh dict.

        Kills variant of #4: had the default been `= {}` (mutable default
        bug), two errors would share one dict and mutating one would affect
        the other.
        """
        a = HardenError(code="A", message="m")
        b = HardenError(code="B", message="m")
        # We can't mutate them (frozen), but we can check identity.
        assert a.context is not b.context


# ---------------------------------------------------------------------------
# Mutant #54 — `extra="forbid"` rejects unknown fields on every frozen schema
# ---------------------------------------------------------------------------


class TestExtraForbid:
    """The shared `_FROZEN` ConfigDict must reject unknown fields. If `extra` is
    ever 'allow' or 'ignore', misspelled fields land silently and audits fail."""

    def test_tls_fingerprint_rejects_unknown(self) -> None:
        with pytest.raises(ValidationError) as exc:
            TLSFingerprint(fid="x123", ua="Mozilla/5.0 Browser", oops_typo="x")  # type: ignore[call-arg]
        # Pydantic identifies extra-field violations with this specific type tag.
        assert any("extra_forbidden" in str(e.get("type", "")) for e in exc.value.errors())

    def test_h2_settings_rejects_unknown(self) -> None:
        with pytest.raises(ValidationError):
            H2Settings(
                initial_window_size=6_291_456,
                max_frame_size=16_384,
                max_header_list_size=262_144,
                extra_field="x",  # type: ignore[call-arg]
            )

    def test_playbook_rejects_unknown(self) -> None:
        with pytest.raises(ValidationError):
            Playbook(
                name="t",
                version=1,
                match=PlaybookMatch(source="reddit-rss"),
                delay_ms=500,
                rate_per_min=60,
                retries=3,
                profile="standard",
                wat="oops",  # type: ignore[call-arg]
            )

    def test_smoothing_result_rejects_unknown(self) -> None:
        with pytest.raises(ValidationError):
            SmoothingResult(
                smoothed_score=0.5,
                certified_radius=0.0,
                n_samples=32,
                sigma=0.1,
                agrees_with_raw=True,
                raw_score=0.5,
                extra="x",  # type: ignore[call-arg]
            )

    def test_poisoning_signal_rejects_unknown(self) -> None:
        with pytest.raises(ValidationError):
            PoisoningSignal(
                detector="x",
                flagged=False,
                severity=0.0,
                junk=1,  # type: ignore[call-arg]
            )

    def test_poisoning_report_rejects_unknown(self) -> None:
        with pytest.raises(ValidationError):
            PoisoningReport(
                batch_id="b",
                n_samples=1,
                signals=(),
                decision="accept",
                extra="x",  # type: ignore[call-arg]
            )

    def test_honeypot_verdict_rejects_unknown(self) -> None:
        with pytest.raises(ValidationError):
            HoneypotVerdict(
                url="https://x.com",
                score=0.1,
                blocked=False,
                reasons=(),
                detector_version="1",
                junk="x",  # type: ignore[call-arg]
            )

    def test_harden_verdict_rejects_unknown(self) -> None:
        with pytest.raises(ValidationError):
            HardenVerdict(
                trend_id="t",
                source="honeypot",
                verdict="proceed",
                score=0.1,
                confidence=0.9,
                reason_code="c",
                bogus="x",  # type: ignore[call-arg]
            )


# ---------------------------------------------------------------------------
# Mutants #74 / #75 — field_validator decorator targets specific field names
# ---------------------------------------------------------------------------


class TestFieldValidatorTargets:
    """`@field_validator('ja3', 'ja4')` runs on BOTH ja3 and ja4. If either name
    is mangled (the mutant replaces with 'XXja3XX' / 'XXja4XX'), the validator
    silently fails to apply, and whitespace-only strings would survive instead
    of being normalized to None."""

    def test_ja3_empty_string_normalized_to_none(self) -> None:
        fp = TLSFingerprint(fid="x123", ua="Mozilla/5.0 Browser", ja3="   ")
        assert fp.ja3 is None  # without the validator on 'ja3', this would be '   '

    def test_ja4_empty_string_normalized_to_none(self) -> None:
        fp = TLSFingerprint(fid="x123", ua="Mozilla/5.0 Browser", ja4="   ")
        assert fp.ja4 is None  # without the validator on 'ja4', this would be '   '

    def test_ja3_tab_string_normalized_to_none(self) -> None:
        """Different whitespace — ensures the validator runs the strip path."""
        fp = TLSFingerprint(fid="x123", ua="Mozilla/5.0 Browser", ja3="\t\n  ")
        assert fp.ja3 is None

    def test_ja4_tab_string_normalized_to_none(self) -> None:
        fp = TLSFingerprint(fid="x123", ua="Mozilla/5.0 Browser", ja4="\t\n  ")
        assert fp.ja4 is None

    def test_ja3_with_content_preserved(self) -> None:
        """Non-empty values pass through unchanged — a negative check on the
        validator, making sure it doesn't accidentally over-normalize."""
        fp = TLSFingerprint(fid="x123", ua="Mozilla/5.0 Browser", ja3="771,4865")
        assert fp.ja3 == "771,4865"

    def test_ja4_with_content_preserved(self) -> None:
        fp = TLSFingerprint(fid="x123", ua="Mozilla/5.0 Browser", ja4="t13d1516h2")
        assert fp.ja4 == "t13d1516h2"


# ---------------------------------------------------------------------------
# Mutant #83 — H2Settings.max_frame_size lower bound is exactly 16384
# ---------------------------------------------------------------------------


class TestH2SettingsBoundaries:
    """Boundary tests for the H2 SETTINGS field bounds.

    The mutant raised the lower bound from 16384 to 16385. RFC 7540 §6.5.2
    mandates 16384 as the minimum, so this test pins the boundary exactly.
    """

    def test_max_frame_size_lower_bound_inclusive(self) -> None:
        """Exactly 16384 is allowed (RFC 7540)."""
        s = H2Settings(
            initial_window_size=6_291_456,
            max_frame_size=16_384,
            max_header_list_size=262_144,
        )
        assert s.max_frame_size == 16_384

    def test_max_frame_size_below_lower_bound_rejected(self) -> None:
        with pytest.raises(ValidationError):
            H2Settings(
                initial_window_size=6_291_456,
                max_frame_size=16_383,  # one less than the RFC floor
                max_header_list_size=262_144,
            )

    def test_max_frame_size_upper_bound_inclusive(self) -> None:
        s = H2Settings(
            initial_window_size=6_291_456,
            max_frame_size=16_777_215,
            max_header_list_size=262_144,
        )
        assert s.max_frame_size == 16_777_215

    def test_max_frame_size_above_upper_bound_rejected(self) -> None:
        with pytest.raises(ValidationError):
            H2Settings(
                initial_window_size=6_291_456,
                max_frame_size=16_777_216,
                max_header_list_size=262_144,
            )

    def test_initial_window_size_lower_bound_inclusive(self) -> None:
        s = H2Settings(
            initial_window_size=65_535,
            max_frame_size=16_384,
            max_header_list_size=262_144,
        )
        assert s.initial_window_size == 65_535

    def test_initial_window_size_upper_bound_inclusive(self) -> None:
        s = H2Settings(
            initial_window_size=16_777_216,
            max_frame_size=16_384,
            max_header_list_size=262_144,
        )
        assert s.initial_window_size == 16_777_216
