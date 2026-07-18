"""Quality gate — pre-write validation for the silver/gold layers.

Two validation styles are supported:

1. ``pydantic``  — validate each row against a pydantic model.
2. ``predicate`` — validate each row against a list of ``(name, callable)`` pairs.

Both styles produce a uniform :class:`QualityReport` so downstream code
does not need to special-case the validator type.

Failures are aggregated, not fatal — at most ``settings.quality_max_reject_fraction``
of rows may fail before the whole batch is rejected (matches Phase 5 doctrine
of *advisory by default, blocking only when truly broken*).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from aegis.datalake._logging import get_logger
from aegis.datalake.constants import QUALITY_ALWAYS_LOG_REJECTS, QUALITY_MAX_REJECT_FRACTION
from aegis.datalake.errors import QualityRejectThresholdExceeded

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class RowReject:
    """Why a single row failed validation."""

    row_index: int
    reason: str
    row: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class QualityReport:
    """Outcome of running a batch through the gate."""

    total_rows: int
    accepted_rows: tuple[dict[str, Any], ...]
    rejected: tuple[RowReject, ...]

    @property
    def reject_count(self) -> int:
        return len(self.rejected)

    @property
    def accepted_count(self) -> int:
        return len(self.accepted_rows)

    @property
    def reject_fraction(self) -> float:
        if self.total_rows == 0:
            return 0.0
        return self.reject_count / self.total_rows

    @property
    def passed(self) -> bool:
        """True iff reject_fraction is within tolerance."""
        return self.reject_fraction <= QUALITY_MAX_REJECT_FRACTION


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------
PredicateFn = Callable[[Mapping[str, Any]], None]
"""Raise to reject; return None to accept."""


@dataclass(frozen=True, slots=True)
class NamedPredicate:
    """A predicate with a human-readable name for reporting."""

    name: str
    fn: PredicateFn


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
class QualityGate:
    """Pluggable row-level validator.

    Example::

        gate = QualityGate(schema=SilverSignal)
        report = gate.validate(rows)
        if not report.passed:
            ...  # quarantine

        # ...or with predicates
        gate = QualityGate(predicates=[
            NamedPredicate("positive_views", lambda r: assert_(r["views"] >= 0, "views<0")),
        ])
    """

    def __init__(
        self,
        *,
        schema: type[BaseModel] | None = None,
        predicates: Iterable[NamedPredicate] = (),
        max_reject_fraction: float = QUALITY_MAX_REJECT_FRACTION,
        always_log_rejects: bool = QUALITY_ALWAYS_LOG_REJECTS,
    ) -> None:
        if schema is None and not predicates:
            raise ValueError("provide schema= or predicates= (or both)")
        self._schema = schema
        self._predicates = tuple(predicates)
        self._max_reject_fraction = max_reject_fraction
        self._always_log_rejects = always_log_rejects

    def validate(
        self, rows: Iterable[Mapping[str, Any]]
    ) -> QualityReport:
        rows_list = list(rows)
        accepted: list[dict[str, Any]] = []
        rejected: list[RowReject] = []

        for idx, row in enumerate(rows_list):
            reason = self._validate_one(row)
            if reason is None:
                # If schema is set, the schema's model_dump is canonical;
                # otherwise we keep the input as-is.
                if self._schema is not None:
                    try:
                        accepted.append(self._schema.model_validate(row).model_dump(mode="json"))
                    except Exception as exc:  # pragma: no cover — paranoia
                        rejected.append(RowReject(idx, f"unexpected: {exc}", row))
                else:
                    accepted.append(dict(row))
            else:
                if self._always_log_rejects:
                    _log.warning("quality.row.reject", index=idx, reason=reason)
                rejected.append(RowReject(idx, reason, row))

        return QualityReport(
            total_rows=len(rows_list),
            accepted_rows=tuple(accepted),
            rejected=tuple(rejected),
        )

    def validate_or_raise(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        context: str = "<unknown>",
    ) -> QualityReport:
        """Validate, raising :class:`QualityRejectThresholdExceeded` on failure."""
        report = self.validate(rows)
        if not report.passed:
            raise QualityRejectThresholdExceeded(
                f"quality gate failed for {context}: "
                f"{report.reject_count}/{report.total_rows} rejected "
                f"({report.reject_fraction:.2%} > {self._max_reject_fraction:.2%})",
                hint="inspect rejected rows; tighten upstream or raise threshold",
            )
        return report

    # ---- internals -------------------------------------------------------
    def _validate_one(self, row: Mapping[str, Any]) -> str | None:
        if self._schema is not None:
            try:
                self._schema.model_validate(row)
            except PydanticValidationError as exc:
                return f"schema: {exc.errors()[0]['msg']}"
        for pred in self._predicates:
            try:
                pred.fn(row)
            except Exception as exc:
                return f"{pred.name}: {exc}"
        return None


__all__ = [
    "NamedPredicate",
    "PredicateFn",
    "QualityGate",
    "QualityReport",
    "RowReject",
]
