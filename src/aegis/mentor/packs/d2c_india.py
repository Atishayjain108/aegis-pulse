"""D2C / e-commerce in India — the first deep SectorPack (v1).

This is where AEGIS already has the most real machinery (swarm marketplace
adapters, geo arbitrage, Phase 6 fulfillment, supplier clients), so it reaches
genuine depth fastest. Region default: India / INR.

All numbers below are grounded reference ranges from Indian D2C / marketplace
reality (2024): marketplace take-rates, GST slabs, typical gross margins,
return rates, CAC. They are reference benchmarks the operators reason against —
never substitutes for a live measured number when one is available.
"""

from __future__ import annotations


class D2CIndiaPack:
    """Deep pack for direct-to-consumer e-commerce in India."""

    sector_tag = "d2c_india"
    display_name = "D2C / E-commerce (India)"
    region_default = "IN"
    is_deep = True

    def benchmarks(self) -> dict[str, float]:
        # Reference ranges expressed as representative midpoints (fractions/INR).
        return {
            # Marketplace commission (take-rate) — Amazon.in / Flipkart apparel.
            "marketplace_take_rate": 0.18,
            # Typical healthy gross margin for private-label D2C apparel.
            "target_gross_margin_pct": 0.55,
            # Below this, the unit economics rarely survive ad spend.
            "min_viable_gross_margin_pct": 0.40,
            # Apparel return/RTO rate on Indian marketplaces is notoriously high.
            "expected_return_rate": 0.25,
            # Blended customer acquisition cost (INR) for a small D2C brand.
            "typical_cac_inr": 250.0,
            # Default GST slab for apparel < ₹1000 (5%); most others 12–18%.
            "gst_rate_apparel": 0.05,
            "gst_rate_general": 0.18,
            # Shipping cost per order (INR) — economy surface, sub-0.5kg.
            "shipping_cost_per_order_inr": 70.0,
        }

    def knowledge(self) -> dict[str, object]:
        return {
            "tier": "deep",
            "marketplaces": [
                "amazon_in", "flipkart", "meesho", "myntra", "ajio", "nykaa", "snapdeal",
            ],
            "supplier_landscape": [
                "indiamart (B2B sourcing, MOQ-based)",
                "CJ Dropshipping (import POD)",
                "Printful (POD, higher cost, no MOQ)",
                "local manufacturers (Tirupur apparel, Surat textiles, Delhi accessories)",
            ],
            "regulations": [
                "GST registration mandatory to sell on marketplaces",
                "GST slabs: apparel <₹1000 → 5%, general → 12-18%",
                "FSSAI license required for food/supplements",
                "BIS/legal-metrology labelling for packaged goods",
            ],
            "failure_patterns": [
                "ignoring RTO/return rate kills margin (~25% on apparel)",
                "racing to the bottom on price in saturated marketplace niches",
                "no GST registration → blocked from marketplaces",
                "underestimating CAC — organic does not scale alone",
            ],
            "channel_playbooks": [
                "marketplace-first (Amazon/Flipkart) for demand validation",
                "Meesho for low-CAC reselling / tier-2/3 reach",
                "own-store (Shopify) only after marketplace product-market fit",
            ],
        }

    def operator_tuning(self) -> dict[str, object]:
        return {
            "research": {"prefer_marketplaces": ["amazon_in", "flipkart", "meesho"]},
            "supplier": {"prefer_sources": ["indiamart", "cj_dropship", "printful"]},
            "ops": {"min_margin_gate_pct": 0.40},
        }
