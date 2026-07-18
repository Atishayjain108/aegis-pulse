"""Shopify draft-order fulfillment client.

Creates Shopify draft orders via the Admin REST API for inventory-route
executions. Draft orders allow a human to review before payment is captured —
this is intentional for Phase 6 live mode.

Behaviour when credentials are absent: returns empty order-ID list silently.

Ref: https://shopify.dev/docs/api/admin-rest/2024-01/resources/draftorder
"""

from __future__ import annotations

import uuid
from typing import Final

import structlog

_log = structlog.get_logger(__name__)


class ShopifyClient:
    """Async Shopify Admin API client for draft-order creation.

    Construct with `shop_domain` (e.g. "my-store.myshopify.com") and a
    private-app access token. Without either, all calls return empty lists.
    """

    __slots__ = ("_shop_domain", "_access_token")

    def __init__(self, *, shop_domain: str, access_token: str) -> None:
        self._shop_domain = shop_domain
        self._access_token = access_token

    async def create_draft_orders(
        self,
        *,
        product_ref: str,
        quantity: int,
        unit_price_usd: float,
    ) -> list[str]:
        """Create `quantity` Shopify draft orders; return their IDs."""
        if not (self._shop_domain and self._access_token):
            _log.debug(
                "fulfillment.shopify.no_credentials", product_ref=product_ref
            )
            return []
        if quantity <= 0:
            return []

        try:
            import httpx
        except ImportError:
            _log.error("fulfillment.shopify.httpx_missing")
            return []

        url = f"https://{self._shop_domain}/admin/api/2024-01/draft_orders.json"
        headers = {
            "X-Shopify-Access-Token": self._access_token,
            "Content-Type": "application/json",
        }
        order_ids: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                for i in range(quantity):
                    payload = {
                        "draft_order": {
                            "note": f"AEGIS execution: {product_ref} unit {i}",
                            "line_items": [
                                {
                                    "title": product_ref,
                                    "price": str(round(unit_price_usd, 2)),
                                    "quantity": 1,
                                    "requires_shipping": True,
                                    "sku": f"AEGIS-{uuid.uuid4().hex[:8]}",
                                }
                            ],
                        }
                    }
                    resp = await client.post(url, json=payload, headers=headers)
                    if resp.status_code not in (200, 201):
                        _log.warning(
                            "fulfillment.shopify.order_failed",
                            status=resp.status_code,
                            product_ref=product_ref,
                        )
                        continue
                    data = resp.json()
                    order_ids.append(str(data["draft_order"]["id"]))
        except Exception as exc:
            _log.error("fulfillment.shopify.error", error=str(exc))

        _log.info(
            "fulfillment.shopify.created",
            product_ref=product_ref,
            requested=quantity,
            created=len(order_ids),
        )
        return order_ids


__all__: Final = ["ShopifyClient"]
