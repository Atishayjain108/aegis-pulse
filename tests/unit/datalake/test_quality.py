"""Quality gate tests — schema + predicate validation."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from aegis.datalake.errors import QualityRejectThresholdExceeded
from aegis.datalake.quality.gate import NamedPredicate, QualityGate


class Person(BaseModel):
    name: str
    age: int = Field(..., ge=0, le=150)


class TestSchemaValidation:
    def test_all_valid_passes(self) -> None:
        gate = QualityGate(schema=Person)
        rows = [{"name": "Ada", "age": 30}, {"name": "Bob", "age": 50}]
        report = gate.validate(rows)
        assert report.passed
        assert report.total_rows == 2
        assert report.accepted_count == 2
        assert report.reject_count == 0
        assert report.reject_fraction == 0.0

    def test_invalid_row_is_rejected(self) -> None:
        gate = QualityGate(schema=Person)
        rows = [
            {"name": "Ada", "age": 30},
            {"name": "Bob", "age": -1},
        ]
        report = gate.validate(rows)
        assert report.total_rows == 2
        assert report.accepted_count == 1
        assert report.reject_count == 1

    def test_validate_or_raise_raises_when_failing(self) -> None:
        gate = QualityGate(schema=Person, max_reject_fraction=0.1)
        rows = [{"name": "A", "age": 200}]
        with pytest.raises(QualityRejectThresholdExceeded):
            gate.validate_or_raise(rows)

    def test_validate_or_raise_returns_when_passing(self) -> None:
        gate = QualityGate(schema=Person)
        rows = [{"name": "A", "age": 30}]
        report = gate.validate_or_raise(rows)
        assert report.passed


def _raise_if_neg_age(r):
    if r.get("age", -1) < 0:
        raise ValueError("age must be non-negative")


def _raise_if_no_name(r):
    if not r.get("name"):
        raise ValueError("name must be non-empty")


class TestPredicateValidation:
    def test_predicate_rejects_bad_rows(self) -> None:
        gate = QualityGate(
            predicates=[NamedPredicate(name="age_pos", fn=_raise_if_neg_age)]
        )
        rows = [{"age": 5}, {"age": -1}, {"age": 7}]
        report = gate.validate(rows)
        assert report.total_rows == 3
        assert report.accepted_count == 2

    def test_multiple_predicates_all_must_pass(self) -> None:
        gate = QualityGate(
            predicates=[
                NamedPredicate(name="age_pos", fn=_raise_if_neg_age),
                NamedPredicate(name="has_name", fn=_raise_if_no_name),
            ]
        )
        rows = [
            {"age": 5, "name": "A"},
            {"age": -1, "name": "B"},
            {"age": 5, "name": ""},
        ]
        report = gate.validate(rows)
        assert report.accepted_count == 1


class TestEmpty:
    def test_empty_rows(self) -> None:
        gate = QualityGate(schema=Person)
        report = gate.validate([])
        assert report.total_rows == 0
        assert report.accepted_count == 0
        assert report.reject_fraction == 0.0
        assert report.passed


class TestRejectedRowsCarryReasons:
    def test_rejected_payload_includes_row_index_and_reason(self) -> None:
        gate = QualityGate(schema=Person)
        rows = [{"name": "A", "age": 999}]
        report = gate.validate(rows)
        assert len(report.rejected) == 1
        rej = report.rejected[0]
        assert rej.row_index == 0
        assert rej.reason
        assert rej.row == {"name": "A", "age": 999}
