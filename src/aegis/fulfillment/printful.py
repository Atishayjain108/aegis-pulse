"""Printful print-on-demand fulfillment client.

Wraps the Printful REST API v2 to create individual print-on-demand orders.
Each call to `create_orders` returns a list of Printful order IDs.

Behaviour when `api_key` is empty or Printful returns an error:
  - Logs a warning with error details.
  - Returns an empty list (engine will record status="failed").

Rate limiting: Printful allows 120 requests/minute per key. This client
makes one API call per `create_orders()` invocation (batch not yet
supported by Printful v2); callers should keep `quantity` reasonable.

Ref: https://developers.printful.com/docs/
"""

from __future__ import annotations

import uuid
from typing import Final

import structlog

_log = structlog.get_logger(__name__)

_PRINTFUL_BASE_URL: Final[str] = "https://api.printful.com"
_DEFAULT_SHIPPING: Final[str] = "STANDARD"
_MOCK_PRODUCT_ID: Final[int] = 1  # generic T-shirt; replace with real variant ID in prod


class PrintfulClient:
    """Async Printful order client.

    Construct once per operation (no persistent connection); uses httpx
    internally. When `api_key` is empty the client logs at DEBUG and
    immediately returns empty results — no HTTP calls are made.
    """

    __slots__ = ("_api_key",)

    def __init__(self, *, api_key: str) -> None:
        self._api_key = api_key

    async def create_orders(
        self,
        *,
        product_ref: str,
        quantity: int,
        unit_price_usd: float,
    ) -> list[str]:
        """Create `quantity` POD orders and return their Printful order IDs.

        In staging/live mode this calls the real Printful API. Without an
        API key it silently returns an empty list.
        """
        if not self._api_key:
            _log.debug("fulfillment.printful.no_api_key", product_ref=product_ref)
            return []
        if quantity <= 0:
            return []

        try:
            import httpx
        except ImportError:
            _log.error("fulfillment.printful.httpx_missing")
            return []

        order_ids: list[str] = []
        try:
            async with httpx.AsyncClient(
                base_url=_PRINTFUL_BASE_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            ) as client:
                for i in range(quantity):
                    payload = {
                        "external_id": f"{product_ref}-{i}-{uuid.uuid4().hex[:8]}",
                        "shipping": _DEFAULT_SHIPPING,
                        "items": [
                            {
                                "product_id": _MOCK_PRODUCT_ID,
                                "quantity": 1,
                                "retail_price": str(round(unit_price_usd, 2)),
                            }
                        ],
                        "recipient": {
                            "name": "AEGIS Drop",
                            "address1": "TBD",
                            "city": "TBD",
                            "country_code": "US",
                            "zip": "00000",
                        },
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


__all__: Final = ["PrintfulClient"]
