"""PROJECT OMEGA — falsifiable reality invariants (Remediation Pass 2026-06-17).

These tests assert REAL behaviour, not mock echoes. Each one is falsifiable: if
a future change re-introduces synthetic reality (fake addresses, placeholder
SKUs, live mode without a real recipient, or fabricated calibration), the
corresponding test MUST fail. They need no infrastructure — every assertion is
about a guard that fires BEFORE any network/DB call.
"""

from __future__ import annotations

import pytest

# --------------------------------------------------------------------------
# C — Execution reality: CJ Dropshipping must refuse fake reality.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cj_refuses_without_product_vid() -> None:
    """INVARIANT: no order is placed without a REAL CJ product variant."""
    from aegis.fulfillment.cjdropshipping import CJDropshipClient

    client = CJDropshipClient(api_key="present")  # key present → past first guard
    ids = await client.create_orders(
        product_ref="trend-x",
        quantity=3,
        unit_price_usd=12.0,
        product_vid=None,  # no real variant resolved
        recipient={
            "name": "A",
            "address1": "1 Real St",
            "city": "Pune",
            "country_code": "IN",
            "zip": "411001",
            "phone": "15555550100",
        },
    )
    assert ids == []  # refused, not shipped to a guessed SKU


@pytest.mark.asyncio
async def test_cj_refuses_incomplete_recipient() -> None:
    """INVARIANT: no order ships to a partial / placeholder address."""
    from aegis.fulfillment.cjdropshipping import CJDropshipClient

    client = CJDropshipClient(api_key="present")
    ids = await client.create_orders(
        product_ref="trend-x",
        quantity=1,
        unit_price_usd=12.0,
        product_vid="REAL-VID-123",
        recipient={"name": "A", "city": "Pune"},  # missing address1/zip/country/phone
    )
    assert ids == []


def test_cj_has_no_placeholder_constants() -> None:
    """INVARIANT: the mock SKU placeholder constant is gone for good, and no
    hardcoded 'TBD' shipping-field ASSIGNMENT remains in the module body."""
    from pathlib import Path

    import aegis.fulfillment.cjdropshipping as mod

    text = Path(mod.__file__).read_text(encoding="utf-8")
    assert "CJ-PLACEHOLDER-001" not in text
    assert "_MOCK_PRODUCT_SKU" not in text
    # The former placeholder assignments (e.g. 'shippingCity': 'TBD') are gone.
    assert '"shippingCity": "TBD"' not in text
    assert '"shippingAddress": "TBD"' not in text


# --------------------------------------------------------------------------
# C — Live mode is structurally blocked without a real recipient.
# --------------------------------------------------------------------------


def test_live_mode_requires_complete_recipient() -> None:
    """INVARIANT: a 'live'/'staging' plan cannot be configured with blank shipping."""
    from aegis.execute.config import ExecuteSettings

    with pytest.raises(Exception):  # pydantic ValidationError
        ExecuteSettings(mode="live")


def test_advisory_mode_allows_blank_recipient() -> None:
    """Advisory mode places no real orders, so the recipient stays optional."""
    from aegis.execute.config import ExecuteSettings

    s = ExecuteSettings(mode="advisory")
    assert s.mode == "advisory"


def test_live_mode_accepts_complete_recipient() -> None:
    from aegis.execute.config import ExecuteSettings

    s = ExecuteSettings(
        mode="live",
        fulfillment_recipient_name="Op",
        fulfillment_recipient_address1="1 Real St",
        fulfillment_recipient_city="Pune",
        fulfillment_recipient_zip="411001",
        fulfillment_recipient_country="IN",
        fulfillment_recipient_phone="15555550100",
    )
    assert s.mode == "live"


# --------------------------------------------------------------------------
# A — Calibration is fail-open and never fabricated.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calibration_fails_open_without_pool() -> None:
    """INVARIANT: with no shared pool, published confidence is the RAW value and
    is honestly labelled uncalibrated — never silently 'calibrated'."""
    from aegis.agents import runner

    value, calibrated = await runner._calibrate_confidence(
        0.97, "00000000-0000-0000-0000-000000000001"
    )
    assert value == 0.97
    assert calibrated is False


def test_identity_calibrator_has_no_knots() -> None:
    """INVARIANT: an unfitted (identity) calibrator carries no knots, so the
    runner helper will NOT treat it as a real fitted map."""
    from aegis.trust.calibrator import Calibrator

    assert Calibrator.identity().knots == []


# --------------------------------------------------------------------------
# F — Dormant systems are quarantined and labelled, not presented as live.
# --------------------------------------------------------------------------


def test_market_memory_is_marked_dormant() -> None:
    """INVARIANT: MarketMemory (zero production callers) carries the DORMANT
    marker so it cannot masquerade as a live capability. If a real reader+writer
    is added, the marker should be flipped/removed deliberately — this test
    forces that to be a conscious decision."""
    from aegis.memory import market

    assert getattr(market, "DORMANT", False) is True
