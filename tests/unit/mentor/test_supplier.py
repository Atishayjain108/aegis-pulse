"""Tests for the M3 SupplierOp (source + verify reliable suppliers)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from aegis.mentor.operators import OperatorContext, OperatorResult, SupplierOp
from aegis.mentor.schemas import Intent, UserProfile


def _profile(**kw: Any) -> UserProfile:
    base = {"sector": "d2c_india", "sector_raw": "wireless earbuds", "intent": Intent.INCOME}
    base.update(kw)
    return UserProfile(**base)


# --- Fakes ----------------------------------------------------------------


def _score(
    name: str, *, verified: bool, trust: float | None = None, verification_rate: float | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        supplier_name=name,
        trust=trust,
        verification_rate=verification_rate,
        delay_rate=0.0,
        cancellation_rate=0.0,
        n_fulfillments=5 if verified else 0,
        is_verified=verified,
    )


class _FakeIntel:
    """Stand-in for SupplierIntel — returns canned trust scores by name."""

    def __init__(self, scores: dict[str, SimpleNamespace]) -> None:
        self._scores = scores
        self.calls: list[str] = []

    async def trust_score(self, name: str) -> SimpleNamespace:
        self.calls.append(name)
        return self._scores.get(
            name, _score(name, verified=False)
        )


def _settings(**kw: Any) -> SimpleNamespace:
    base = {
        "printful_api_key": "",
        "cjdropship_api_key": "",
        "shopify_access_token": "",
        "shopify_shop_domain": "",
    }
    base.update(kw)
    return SimpleNamespace(**base)


# --- Tests ----------------------------------------------------------------


class TestSupplierOp:
    @pytest.mark.asyncio
    async def test_ranks_verified_above_unverified(self) -> None:
        intel = _FakeIntel(
            {
                "printful": _score("printful", verified=True, trust=0.6),
                "cjdropshipping": _score("cjdropshipping", verified=True, trust=0.95),
                "shopify": _score("shopify", verified=False, verification_rate=0.5),
            }
        )
        op = SupplierOp(
            intel=intel,
            printful=None,  # no catalog probe
            settings=_settings(
                printful_api_key="k",
                cjdropship_api_key="k",
                shopify_access_token="t",
                shopify_shop_domain="shop.myshopify.com",
            ),
        )
        res = await op.run(_profile(), "find me a supplier", OperatorContext(pool=object()))

        assert isinstance(res, OperatorResult)
        assert res.operator == "supplier"
        suppliers = res.data["suppliers"]
        # Highest-trust verified supplier ranks first; unverified ranks last.
        assert suppliers[0]["name"] == "cjdropshipping"
        assert suppliers[-1]["name"] == "shopify"
        assert res.data["verified_count"] == 2
        assert res.data["unverified_count"] == 1
        assert res.confidence > 0.5
        assert "execution_intel:supplier_reliability" in res.sources

    @pytest.mark.asyncio
    async def test_never_marks_unverified_as_reliable(self) -> None:
        intel = _FakeIntel(
            {"printful": _score("printful", verified=False, verification_rate=0.8)}
        )
        op = SupplierOp(intel=intel, settings=_settings(printful_api_key="k"))
        res = await op.run(_profile(), "supplier", OperatorContext(pool=object()))

        rows = res.data["suppliers"]
        assert all(r["verified"] is False for r in rows)
        # The supplier is surfaced but explicitly flagged unverified.
        assert any(f.startswith("(unverified)") for f in res.findings)
        assert not any("Verified supplier:" in f for f in res.findings)
        # A verification rate alone (no settled fulfillment) is NOT reliability.
        assert res.confidence <= 0.3
        assert res.data["verified_count"] == 0

    @pytest.mark.asyncio
    async def test_graceful_when_nothing_configured(self) -> None:
        # No keys, no pool, no candidate source → empty grounded result.
        op = SupplierOp(intel=None, settings=_settings())
        res = await op.run(_profile(), "find a supplier", OperatorContext())
        assert res.confidence == 0.0
        assert not res.has_signal
        assert res.data == {}

    @pytest.mark.asyncio
    async def test_no_pool_means_all_unverified(self) -> None:
        # Channels configured but no DB pool → cannot verify → all unverified.
        op = SupplierOp(settings=_settings(cjdropship_api_key="k"))
        res = await op.run(_profile(), "sourcing", OperatorContext())
        rows = res.data["suppliers"]
        assert rows and all(r["verified"] is False for r in rows)
        assert "execution_intel:supplier_reliability" not in res.sources

    @pytest.mark.asyncio
    async def test_printful_catalog_probe_records_availability(self) -> None:
        class _FakePrintful:
            async def find_matching_product(self, category: str, keywords: list[str]) -> Any:
                return SimpleNamespace(name="Unisex Tee", variant_id=4012)

        intel = _FakeIntel({"printful": _score("printful", verified=True, trust=0.7)})
        op = SupplierOp(
            intel=intel,
            printful=_FakePrintful(),
            settings=_settings(printful_api_key="k"),
        )
        res = await op.run(_profile(), "source earbuds", OperatorContext(pool=object()))
        printful_row = next(r for r in res.data["suppliers"] if r["name"] == "printful")
        assert printful_row["catalog_available"] is True
        assert printful_row["catalog_product"] == "Unisex Tee"

    @pytest.mark.asyncio
    async def test_injected_candidate_source_with_price_lead_ranking(self) -> None:
        async def _source(profile: UserProfile, request: str, ctx: OperatorContext) -> list[dict]:
            return [
                {"name": "vendor_a", "channel": "wholesale", "price": 120.0, "lead_time_days": 14,
                 "source": "indiamart"},
                {"name": "vendor_b", "channel": "wholesale", "price": 90.0, "lead_time_days": 21,
                 "source": "indiamart"},
            ]

        # Both unverified (no intel) → tie broken by cheaper price.
        op = SupplierOp(intel=None, settings=_settings(), candidate_source=_source)
        res = await op.run(_profile(), "wholesale vendors", OperatorContext())
        rows = res.data["suppliers"]
        assert rows[0]["name"] == "vendor_b"  # cheaper ranks first among unverified
        assert "indiamart" in res.sources

    @pytest.mark.asyncio
    async def test_candidate_dedup_by_name(self) -> None:
        async def _source(profile: UserProfile, request: str, ctx: OperatorContext) -> list[dict]:
            return [{"name": "Printful", "channel": "pod"}]

        op = SupplierOp(
            intel=None,
            settings=_settings(printful_api_key="k"),
            candidate_source=_source,
        )
        res = await op.run(_profile(), "supplier", OperatorContext())
        names = [r["name"] for r in res.data["suppliers"]]
        assert names.count("printful") == 1
