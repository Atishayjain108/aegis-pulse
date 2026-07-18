"""Shared pytest fixtures for tests/unit/comply/."""

from __future__ import annotations

import pytest

from aegis.comply.engine import ComplianceEngine
from aegis.comply.schemas import ComplianceRequest


@pytest.fixture()
def engine() -> ComplianceEngine:
    """A default ComplianceEngine with no external dependencies."""
    return ComplianceEngine()


@pytest.fixture()
def clean_request() -> ComplianceRequest:
    """A plain product request that should score CLEAR."""
    return ComplianceRequest(
        trend_id="clean-1",
        title="Plain Cotton T-Shirt",
        category="apparel",
        price=25.0,
    )


@pytest.fixture()
def health_claim_request() -> ComplianceRequest:
    """A request containing FTC health-claim violations → should score BLOCK."""
    return ComplianceRequest(
        trend_id="ftc-1",
        title="Miracle Weight Loss Supplement",
        description="FDA-Approved! Guaranteed to cure diabetes and cancer!",
        claims=("FDA-Approved", "cures cancer", "guaranteed weight loss"),
        category="health",
        price=49.99,
    )
