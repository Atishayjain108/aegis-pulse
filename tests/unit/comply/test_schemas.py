"""Tests for schema invariants: frozen models + stable content_id."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from aegis.comply.schemas import (
    ComplianceRequest,
    ComplianceVerdict,
    ComplianceVerdictResult,
    Jurisdiction,
)


def _result(**overrides):
    base = {
        "trend_id": "t",
        "verdict": ComplianceVerdict.FLAG,
        "risk_score": 0.4,
        "confidence": 0.7,
    }
    base.update(overrides)
    return ComplianceVerdictResult(**base)


def test_request_is_frozen():
    req = ComplianceRequest(trend_id="t", title="x")
    with pytest.raises(ValidationError):
        req.title = "y"  # type: ignore[misc]


def test_request_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ComplianceRequest(trend_id="t", title="x", bogus_field=1)  # type: ignore[call-arg]


def test_empty_jurisdiction_defaults_to_us():
    req = ComplianceRequest(trend_id="t", title="x", target_jurisdictions=())
    assert req.target_jurisdictions == (Jurisdiction.US,)


def test_text_property_lowercases_and_joins():
    req = ComplianceRequest(
        trend_id="t", title="HELLO", description="World", claims=("Buy NOW",)
    )
    assert req.text == "hello world buy now"


def test_content_id_is_stable_across_timestamps():
    ts1 = datetime(2026, 1, 1, tzinfo=UTC)
    ts2 = datetime(2026, 2, 2, tzinfo=UTC)
    a = _result(checked_at=ts1)
    b = _result(checked_at=ts2)
    assert a.content_id == b.content_id  # timestamp excluded from hash


def test_content_id_changes_with_verdict():
    a = _result(verdict=ComplianceVerdict.FLAG)
    b = _result(verdict=ComplianceVerdict.BLOCK)
    assert a.content_id != b.content_id


def test_content_id_is_sha256_hex():
    cid = _result().content_id
    assert len(cid) == 64
    int(cid, 16)  # parses as hex


# ---------------------------------------------------------------------------
# Package-level __getattr__ lazy-import paths (comply/__init__.py lines 87-106)
# Tests use module attribute access (aegis.comply.X) to trigger __getattr__,
# unlike submodule imports which bypass it.
# ---------------------------------------------------------------------------

import aegis.comply as _comply_mod  # noqa: E402


def test_lazy_getattr_compliance_engine():
    cls = _comply_mod.ComplianceEngine
    assert cls is not None
    from aegis.comply.engine import ComplianceEngine
    assert cls is ComplianceEngine


def test_lazy_getattr_schema_type():
    req_cls = _comply_mod.ComplianceRequest
    assert req_cls is not None
    verdict_cls = _comply_mod.ComplianceVerdict
    assert verdict_cls is not None
    result_cls = _comply_mod.ComplianceVerdictResult
    assert result_cls is not None


def test_lazy_getattr_evaluate_for_compliance_node():
    fn = _comply_mod.evaluate_for_compliance_node
    assert callable(fn)


def test_lazy_getattr_unknown_raises_attribute_error():
    with pytest.raises(AttributeError, match="has no attribute"):
        _ = _comply_mod.no_such_attribute_xyzzy_comply
