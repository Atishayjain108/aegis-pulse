"""
aegis.execution_intel.taxonomy — Execution Intelligence vocabularies (Phase D).

Plain ``str`` enums so values persist directly to the TEXT columns in
``0021_execution_intel.sql`` and round-trip without conversion.

Reality First (Rule 1): ``UNVERIFIED`` is a first-class state, never a silent
assumption. ``AssumptionStatus.UNVERIFIED`` is the default for every assumption
a recommendation rests on until real evidence promotes it.
"""

from __future__ import annotations

from enum import Enum

# Module-level sentinel used by callers/CLI to render an honestly-unknown value
# (e.g. a supplier/buyer trust score with no settled outcomes behind it).
UNVERIFIED = "UNVERIFIED"


class ExecutionOutcome(str, Enum):
    """Settled truth of an execution plan (Rule 2)."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DELAYED = "delayed"
    UNVERIFIED = "unverified"  # outcome could not be tied to real settlement


class ExecutionFailureCategory(str, Enum):
    """Why an execution plan failed (Rule 5 / Rule 8). Reusable knowledge."""

    NONE = "none"
    NO_VERIFIED_SUPPLIER = "no_verified_supplier"
    INVENTORY_FAILURE = "inventory_failure"
    SUPPLIER_FAILURE = "supplier_failure"
    SHIPPING_DELAY = "shipping_delay"
    COMPLIANCE_ISSUE = "compliance_issue"
    PAYMENT_ISSUE = "payment_issue"
    DEMAND_COLLAPSE = "demand_collapse"
    MARGIN_EVAPORATED = "margin_evaporated"
    UNCLASSIFIED = "unclassified"


class AssumptionStatus(str, Enum):
    """Verification state of one assumption (Rule 1). Default is UNVERIFIED."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    FALSIFIED = "falsified"


class AssumptionKind(str, Enum):
    """The categories of thing a recommendation may assume (Rule 1)."""

    SUPPLIER_EXISTS = "supplier_exists"
    INVENTORY = "inventory"
    MARGIN = "margin"
    DEMAND = "demand"
    BUYER = "buyer"
    SHIPPING = "shipping"
    COMPLIANCE = "compliance"
