"""Printful print-on-demand fulfillment client.

Wraps the Printful REST API to (1) search the real product catalog for a
variant matching a trend, (2) get the real production + shipping cost for that
variant, (3) verify it is in stock, and (4) create real print-on-demand orders.

FIX-4 (forensic audit — mock supplier pipeline removed):
The previous version posted orders with a hardcoded ``_MOCK_PRODUCT_ID = 1``
("generic T-shirt") and a fake recipient address (``"TBD"`` / zip ``"00000"``).
No real money could ever flow through that, and any margin computed on top of it
was fiction. This client now:

  * resolves a REAL catalog variant via ``find_matching_product`` before any
    order is created;
  * derives unit cost from the REAL ``/orders/estimate`` endpoint, never from a
    ``margin × 2`` constant;
  * REFUSES to create an order unless it is given a concrete variant id AND a
    real recipient address — it never substitutes a placeholder.

Behaviour when ``api_key`` is empty, no variant/recipient is supplied, or
Printful returns an error:
  - Logs a warning with error details.
  - Returns an empty list (the engine records status="no_verified_supplier"
    or "failed" and never sizes a position on fictional data).

Rate limiting: Printful allows 120 requests/minute per key.

Ref: https://developers.printful.com/docs/
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Final

import structlog

_log = structlog.get_logger(__name__)

_PRINTFUL_BASE_URL: Final[str] = "https://api.printful.com"
_DEFAULT_SHIPPING: Final[str] = "STANDARD"


@dataclass(frozen=True, slots=True)
class PrintfulProduct:
    """A real Printful catalog product/variant resolved from a search."""

    product_id: int
    variant_id: int
    name: str
    currency: str = "USD"


class PrintfulClient:
    """Async Printful order client.

    Construct once per operation (no persistent connection); uses httpx
    internally. When ``api_key`` is empty the client logs at DEBUG and
    immediately returns empty results — no HTTP calls are made.
    """

    __slots__ = ("_api_key",)

    def __init__(self, *, api_key: str) -> None:
        self._api_key = api_key

    # ------------------------------------------------------------------
    # Internal HTTP helper
    # ------------------------------------------------------------------

    def _client(self) -> Any | None:
        try:
            import httpx
        except ImportError:
            _log.error("fulfillment.printful.httpx_missing")
            return None
        return httpx.AsyncClient(
            base_url=_PRINTFUL_BASE_URL,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    # ------------------------------------------------------------------
    # Real supplier verification (FIX-4)
    # ------------------------------------------------------------------

    async def find_matching_product(
        self, category: str, keywords: list[str]
    ) -> PrintfulProduct | None:
        """Search the Printful catalog for a real product matching the trend.

        Returns ``None`` when there is no API key or no match — the caller must
        then treat the opportunity as having no verified supplier rather than
        falling back to a placeholder product.
        """
        if not self._api_key:
            _log.debug("fulfillment.printful.no_api_key", category=category)
            return None

        query = " ".join([category, *keywords]).strip()
        client = self._client()
        if client is None:
            return None
        try:
            async with client:
                resp = await client.get(
                    "/products", params={"category_id": "", "search": query}
                )
                if resp.status_code != 200:
                    _log.warning(
                        "fulfillment.printful.catalog_search_failed",
                        status=resp.status_code,
                        query=query,
                    )
                    return None
                results = (resp.json() or {}).get("result") or []
                if not results:
                    _log.info("fulfillment.printful.no_catalog_match", query=query)
                    return None
                top = results[0]
                product_id = int(top["id"])

                # Resolve the first available variant for this product.
                detail = await client.get(f"/products/{product_id}")
                if detail.status_code != 200:
                    return None
                variants = (detail.json() or {}).get("result", {}).get("variants") or []
                if not variants:
                    return None
                variant_id = int(variants[0]["id"])
                return PrintfulProduct(
                    product_id=product_id,
                    variant_id=variant_id,
                    name=str(top.get("title", query)),
                )
        except Exception as exc:
            _log.error("fulfillment.printful.catalog_error", error=str(exc))
            return None

    async def get_real_cost(
        self, variant_id: int, recipient: dict[str, Any]
    ) -> float | None:
        """Return the REAL production + shipping cost (USD) for *variant_id*.

        Calls Printful's ``/orders/estimate-costs`` endpoint with the actual
        recipient. Returns ``None`` on any failure — never a derived constant.
        """
        if not self._api_key:
            return None
        client = self._client()
        if client is None:
            return None
        payload = {
            "recipient": recipient,
            "items": [{"variant_id": variant_id, "quantity": 1}],
        }
        try:
            async with client:
                resp = await client.post("/orders/estimate-costs", json=payload)
                if resp.status_code != 200:
                    _log.warning(
                        "fulfillment.printful.estimate_failed",
                        status=resp.status_code,
                        variant_id=variant_id,
                    )
                    return None
                costs = (resp.json() or {}).get("result", {}).get("costs", {})
                total = costs.get("total")
                return float(total) if total is not None else None
        except Exception as exc:
            _log.error("fulfillment.printful.estimate_error", error=str(exc))
            return None

    async def verify_inventory(self, product_id: int, variant_id: int) -> bool:
        """Return True only if *variant_id* is currently in stock."""
        if not self._api_key:
            return False
        client = self._client()
        if client is None:
            return False
        try:
            async with client:
                resp = await client.get(f"/products/{product_id}")
                if resp.status_code != 200:
                    return False
                variants = (resp.json() or {}).get("result", {}).get("variants") or []
                for v in variants:
                    if int(v.get("id", -1)) == variant_id:
                        # Printful marks discontinued/out-of-stock variants.
                        return bool(v.get("in_stock", True)) and not v.get(
                            "discontinued", False
                        )
                return False
        except Exception as exc:
            _log.error("fulfillment.printful.inventory_error", error=str(exc))
            return False

    # ------------------------------------------------------------------
    # Order creation (real recipient + variant required)
    # ------------------------------------------------------------------

    async def create_orders(
        self,
        *,
        product_ref: str,
        quantity: int,
        unit_price_usd: float,
        variant_id: int | None = None,
        recipient: dict[str, Any] | None = None,
    ) -> list[str]:
        """Create ``quantity`` POD orders and return their Printful order IDs.

        FIX-4: an order is created ONLY when both a concrete ``variant_id`` and a
        real ``recipient`` (with a non-placeholder address) are supplied. Without
        them the client refuses and returns an empty list — it never falls back
        to a mock product or a "TBD" address.
        """
        if not self._api_key:
            _log.debug("fulfillment.printful.no_api_key", product_ref=product_ref)
            return []
        if quantity <= 0:
            return []
        if variant_id is None or not _is_real_recipient(recipient):
            _log.warning(
                "fulfillment.printful.unverified_order_refused",
                product_ref=product_ref,
                has_variant=variant_id is not None,
                has_recipient=recipient is not None,
                reason="no verified variant/recipient — refusing to place a fictional order",
            )
            return []

        client = self._client()
        if client is None:
            return []

        order_ids: list[str] = []
        try:
            async with client:
                for i in range(quantity):
                    payload = {
                        "external_id": f"{product_ref}-{i}-{uuid.uuid4().hex[:8]}",
                        "shipping": _DEFAULT_SHIPPING,
                        "items": [
                            {
                                "variant_id": variant_id,
                                "quantity": 1,
                                "retail_price": str(round(unit_price_usd, 2)),
                            }
                        ],
                        "recipient": recipient,
                    }
                    resp = await client.post("/orders", json=payload)
                    if resp.status_code not in (200, 201):
                        _log.warning(
                            "fulfillment.printful.order_failed",
                            status=resp.status_code,
                            product_ref=product_ref,
                            index=i,
                        )
                        continue
                    data = resp.json()
                    order_ids.append(str(data["result"]["id"]))
        except Exception as exc:
            _log.error("fulfillment.printful.error", error=str(exc))

        _log.info(
            "fulfillment.printful.created",
            product_ref=product_ref,
            requested=quantity,
            created=len(order_ids),
        )
        return order_ids


def _is_real_recipient(recipient: dict[str, Any] | None) -> bool:
    """Reject empty/placeholder recipients (the old "TBD"/"00000" address)."""
    if not recipient:
        return False
    address1 = str(recipient.get("address1", "")).strip().upper()
    zip_code = str(recipient.get("zip", "")).strip()
    if not address1 or address1 in {"TBD", "N/A", "UNKNOWN"}:
        return False
    if not zip_code or zip_code in {"00000", "0"}:
        return False
    return bool(str(recipient.get("country_code", "")).strip())


__all__: Final = ["PrintfulClient", "PrintfulProduct"]
