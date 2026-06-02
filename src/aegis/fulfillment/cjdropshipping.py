"""CJ Dropshipping fulfillment client.

Wraps the CJ Open Platform API to create dropship orders. Each call to
`create_orders` returns a list of CJ order IDs.

Behaviour when `api_key` is empty or the API returns an error:
  - Logs a warning with error details.
  - Returns an empty list.

CJ API documentation: https://developers.cjdropshipping.com/

Note: CJ uses a two-step auth flow (get access token from API key, then use
token for order calls). This client caches the token for the session lifetime.
"""

from __future__ import annotations

import uuid
from typing import Final

import structlog

_log = structlog.get_logger(__name__)

_CJ_BASE_URL: Final[str] = "https://developers.cjdropshipping.com/api2.0"
_CJ_AUTH_URL: Final[str] = "https://developers.cjdropshipping.com/api2.0/v1/authentication/getAccessToken"
_MOCK_PRODUCT_SKU: Final[str] = "CJ-PLACEHOLDER-001"  # replace with real CJ product SKU


class CJDropshipClient:
    """Async CJ Dropshipping order client.

    Caches the access token across calls within a single instance lifetime.
    Without an `api_key`, returns empty results immediately.
    """

    __slots__ = ("_api_key", "_access_token")

    def __init__(self, *, api_key: str) -> None:
        self._api_key = api_key
        self._access_token: str | None = None

    async def create_orders(
        self,
        *,
        product_ref: str,
        quantity: int,
        unit_price_usd: float,
    ) -> list[str]:
        """Create `quantity` dropship orders and return their CJ order IDs."""
        if not self._api_key:
            _log.debug("fulfillment.cjdropship.no_api_key", product_ref=product_ref)
            return []
        if quantity <= 0:
            return []

        try:
            import httpx
        except ImportError:
            _log.error("fulfillment.cjdropship.httpx_missing")
            return []

        order_ids: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                token = await self._get_token(client)
                if not token:
                    return []

                for i in range(quantity):
                    payload = {
                        "orderNumber": f"AEGIS-{product_ref[:12]}-{i}-{uuid.uuid4().hex[:6]}",
                        "shippingZip": "00000",
                        "shippingCountryCode": "US",
                        "shippingCountry": "United States",
                        "shippingProvince": "CA",
                        "shippingCity": "TBD",
                        "shippingAddress": "TBD",
                        "shippingCustomerName": "AEGIS Drop",
                        "shippingPhone": "0000000000",
                        "products": [
                            {
                                "vid": _MOCK_PRODUCT_SKU,
                                "quantity": 1,
                                "shippingName": "CJPacket",
                                "price": str(round(unit_price_usd, 2)),
                            }
                        ],
                    }
                    resp = await client.post(
                        f"{_CJ_BASE_URL}/v1/shopping/order/createOrder",
                        json=payload,
                        headers={"CJ-Access-Token": token},
                    )
                    if resp.status_code not in (200, 201):
                        _log.warning(
                            "fulfillment.cjdropship.order_failed",
                            status=resp.status_code,
                            product_ref=product_ref,
                            index=i,
                        )
                        continue
                    data = resp.json()
                    if data.get("result"):
                        order_ids.append(str(data["data"]["orderId"]))
        except Exception as exc:
            _log.error("fulfillment.cjdropship.error", error=str(exc))

        _log.info(
            "fulfillment.cjdropship.created",
            product_ref=product_ref,
            requested=quantity,
            created=len(order_ids),
        )
        return order_ids

    async def _get_token(self, client: object) -> str | None:
        if self._access_token:
            return self._access_token
        try:
            resp = await client.post(  # type: ignore[union-attr]
                _CJ_AUTH_URL,
                json={"email": "aegis@local", "password": self._api_key},
            )
            if resp.status_code == 200:
                self._access_token = str(resp.json()["data"]["accessToken"])
                return self._access_token
        except Exception as exc:
            _log.error("fulfillment.cjdropship.auth_failed", error=str(exc))
        return None


__all__: Final = ["CJDropshipClient"]
