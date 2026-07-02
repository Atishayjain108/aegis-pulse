"""Cover the aegis.compliance.errors typed hierarchy (was 0% — never imported)."""

from __future__ import annotations

import pytest

from aegis.compliance import errors as e


@pytest.mark.parametrize(
    ("cls", "code"),
    [
        (e.TrademarkRiskError, "AEGIS-COMPLY-0001"),
        (e.PatentRiskError, "AEGIS-COMPLY-0002"),
        (e.FDAViolationError, "AEGIS-COMPLY-0003"),
        (e.CounterfeitRiskError, "AEGIS-COMPLY-0004"),
        (e.FTCViolationError, "AEGIS-COMPLY-0005"),
        (e.PrivacyRiskError, "AEGIS-COMPLY-0006"),
        (e.AMLRiskError, "AEGIS-COMPLY-0007"),
        (e.SanctionViolationError, "AEGIS-COMPLY-0008"),
        (e.ComplianceBlockError, "AEGIS-COMPLY-0010"),
        (e.ComplianceAPIError, "AEGIS-COMPLY-0020"),
        (e.ComplianceCacheError, "AEGIS-COMPLY-0030"),
    ],
)
def test_each_error_has_code_and_is_subclass(cls: type[e.AegisComplianceError], code: str) -> None:
    exc = cls("boom")
    assert isinstance(exc, e.AegisComplianceError)
    assert exc.code == code
    assert str(exc) == "boom"
    with pytest.raises(cls):
        raise exc


def test_base_default_code_and_override() -> None:
    base = e.AegisComplianceError("x")
    assert base.code == "AEGIS-COMPLY-0000"
    overridden = e.AegisComplianceError("y", code="AEGIS-COMPLY-9999")
    assert overridden.code == "AEGIS-COMPLY-9999"
