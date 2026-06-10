"""Tests for `aegis.harden.errors`."""

from __future__ import annotations

import pytest

from aegis.harden.errors import (
    CODES,
    FingerprintPoolError,
    HardenError,
    HoneypotBlocked,
    PlaybookValidationError,
    PoisoningDetected,
    SmoothingError,
    make,
)


class TestCodebook:
    def test_codes_present(self) -> None:
        # Sanity: at least the codes referenced from production modules exist.
        for code in (
            "AEGIS-HARDEN-0001",
            "AEGIS-HARDEN-0002",
            "AEGIS-HARDEN-0004",
            "AEGIS-HARDEN-0010",
            "AEGIS-HARDEN-0030",
            "AEGIS-HARDEN-0040",
        ):
            assert code in CODES

    def test_make_returns_tuple(self) -> None:
        code, msg, ctx = make("AEGIS-HARDEN-0001", path="x")
        assert code == "AEGIS-HARDEN-0001"
        assert ctx == {"path": "x"}

    def test_each_error_class_is_exception(self) -> None:
        for cls in (
            HardenError,
            PlaybookValidationError,
            FingerprintPoolError,
            HoneypotBlocked,
            SmoothingError,
            PoisoningDetected,
        ):
            assert issubclass(cls, Exception)


class TestRaising:
    def test_string_repr(self) -> None:
        err = PlaybookValidationError(*make("AEGIS-HARDEN-0001", path="x"))
        s = str(err)
        assert "AEGIS-HARDEN-0001" in s

    def test_context_preserved(self) -> None:
        err = SmoothingError(*make("AEGIS-HARDEN-0030", shape=(2, 3)))
        assert err.context == {"shape": (2, 3)}

    def test_raise_via_make(self) -> None:
        with pytest.raises(FingerprintPoolError) as exc:
            raise FingerprintPoolError(*make("AEGIS-HARDEN-0010", reason="test"))
        assert exc.value.code == "AEGIS-HARDEN-0010"
        assert exc.value.context["reason"] == "test"
