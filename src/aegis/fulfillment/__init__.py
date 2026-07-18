"""AEGIS Pulse fulfillment integrations — Phase 6.

Provides async clients for third-party fulfillment backends:
  - printful: Print-on-demand (Printful API v2).
  - cjdropshipping: Dropshipping (CJ API).
  - shopify: Shopify draft-order creation.

All clients degrade gracefully when API keys are absent: they return empty
order-ID lists and log a warning rather than raising. This keeps the engine
functional in advisory/staging mode without real credentials.
"""

from __future__ import annotations

from typing import Final

__version__: Final[str] = "6.0.0"

__all__: Final = ["__version__"]
