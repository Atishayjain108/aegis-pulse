# ADR-0007: Geospatial Intelligence & Cross-Market Arbitrage

**Status**: Accepted  
**Date**: 2026-06-01  
**Phase**: 7

---

## Context

AEGIS Pulse identifies arbitrage signals primarily within a single market.
A substantial class of opportunities involves geographic price/demand differentials:
a product can be cheaply manufactured or purchased in one region (e.g. India/China)
while commanding a premium in another (e.g. US/EU), provided that the net margin
after shipping, import duty, and platform fees remains positive.

### Example

| Item | Value |
|------|-------|
| Buy price (India) | INR 800 → USD 9.52 |
| Sell price (US) | USD 24.99 |
| Shipping (IN→US) | USD 6.50 |
| Import duty (US apparel 16.5%) | USD 1.57 |
| Platform fee (Amazon 15%) | USD 3.75 |
| **Gross margin** | **USD 5.65 (22.6%)** |

This opportunity is invisible to a single-market analyzer but clearly viable.

---

## Decision

### 1. Free-only data sources (no paid APIs required)

| Data | Source | Cost |
|------|--------|------|
| FX rates | Frankfurter.app (ECB) | Free, no key |
| WTO MFN tariff rates | Static schedule from WTO Tariff Profiles 2024 | Free (hardcoded) |
| Shipping costs | Published EMS/postal rate cards 2024 | Free (static matrix) |
| Product prices | Phase 1 signals DB (scraped) + category medians | Internal |
| Demand intensity | Phase 1 signals velocity by platform | Internal |
| Market size | World Bank Open Data 2024 (GDP, e-comm penetration) | Free (hardcoded) |
| Optional live shipping | ShipEngine free tier (AEGIS_GEO_SHIPENGINE_KEY) | Free tier |

### 2. FX Rate strategy

- Primary: Frankfurter API (`api.frankfurter.app/latest`) — ECB published rates,
  updated each business day, no API key required.
- Cache TTL: 3600 s in-process dict keyed by `(base, quote)`.
- Fallback: hardcoded ECB approximate midpoints as of 2024-12 (last-resort only).
- All rates expressed as "1 USD = X foreign_currency".  Cross rates computed
  via USD as denominator.

### 3. Tariff data

Tariff rates come from the `HS_TARIFF_SCHEDULE` static dictionary, built from:
- **US**: USITC Harmonized Tariff Schedule 2024
- **IN**: CBIC Customs Tariff 2024 (Basic Customs Duty)
- **EU**: EC TARIC / Combined Nomenclature 2024
- **UK**: HMRC UK Global Trade Tariff 2024
- **JP/CN/AU/BR**: WTO Tariff Profiles 2024 (MFN applied rates)

Covers 20 HS codes across major e-commerce categories (apparel, footwear,
electronics, beauty, toys, furniture, sports, jewelry, books, pets).

Unknown HS codes fall back to the WTO sector-average MFN rate for the destination
country.  Optional UN Comtrade API fallback (`use_comtrade_fallback=True`) is
disabled by default due to latency (~5–30 s per query).

### 4. Shipping cost matrix

Based on 2024 published rates for a **0.5 kg tracked economy parcel**:
- EMS International / India Post for IN-origin
- USPS International for US-origin
- Royal Mail for UK-origin
- China Post / ePacket for CN-origin (cheapest global option)

Matrix covers all 56 ordered pairs for the 8 supported regions.
A `$1.50/kg` weight surcharge applies beyond 0.5 kg.
ShipEngine API can be enabled for live quotes via `AEGIS_GEO_SHIPENGINE_KEY`.

### 5. Demand intensity

`RegionalDemandAnalyzer.get_demand()` queries the Phase 1 `signals` table:
```sql
SELECT platform, COUNT(*) AS signal_count,
       AVG(source_confidence) AS avg_confidence,
       SUM(views + likes + comments + shares) AS total_engagement
FROM signals
WHERE tenant_id = $1
  AND created_at > NOW() - INTERVAL '24 hours'
  AND platform = ANY($2::text[])
GROUP BY platform
```
Each `Region` maps to a set of platform names (e.g. `Region.IN` → flipkart,
amazon_in, meesho, myntra, etc.).  Intensity = `(signal_count / 500) × avg_confidence`,
capped at 1.0.

When no DB pool is available (tests, CLI preview), synthetic demand uses
`RegionConfig.market_size_score × 0.6` as proxy.

### 6. Opportunity scoring

```
opportunity_score = gross_margin_pct × demand_intensity × market_size_score
```

- `gross_margin_pct`: `(dest_price_usd - total_landed_cost_usd) / dest_price_usd × 100`
- `total_landed_cost_usd`: `origin_price_usd + shipping_usd + duty_usd`
- Only opportunities with `gross_margin_pct ≥ 5%` are returned.

### 7. Phase 6 integration

`geo_opportunity_to_execution_intent()` converts a `GeoOpportunity` to a Phase 6
`ExecutionIntent`.  Priority mapping:
- `gross_margin_pct ≥ 40%` → P1 ENTER
- `gross_margin_pct ≥ 20%` → P2 ENTER
- `gross_margin_pct ≥ 5%` → P3 HOLD

Import of `aegis.execute.engine.ExecutionIntent` is guarded by try/except —
Phase 7 is usable without Phase 6 installed.

### 8. REST API

FastAPI router at `/geo/*`:
- `GET /geo/health` — liveness
- `POST /geo/analyze` — full cross-market analysis for a product
- `GET /geo/fx` — live FX rates
- `GET /geo/tariff/{hs_code}/{dest}` — duty lookup
- `GET /geo/shipping/{origin}/{dest}` — shipping quote

### 9. CLI

```bash
uv run aegis geo analyze <sku> <title> --category apparel --top-n 5
uv run aegis geo fx
uv run aegis geo tariff 610910 IN --value 100
uv run aegis geo shipping US IN
uv run aegis geo regions
```

---

## Consequences

### Positive
- Zero additional API cost in default configuration.
- Real WTO tariff data (not mock) — defensible margin estimates.
- Real ECB FX rates with 1-hour cache — sufficiently fresh for daily arbitrage.
- Demand intensity wired to live Phase 1 signals — improves as more data accumulates.
- Phase 6 integration means top geo opportunities can automatically queue for execution.

### Negative
- Tariff schedule is static and requires manual update when WTO rates change.
- Shipping costs are economy tier; express courier margin will differ.
- Product prices fall back to category medians when the SKU has no scraped history.
- FX cache TTL of 1 hour means intra-day forex swings not captured.

### Neutral
- UN Comtrade API integration is stubbed but disabled (`use_comtrade_fallback=False`)
  to avoid 5–30 s latency in hot paths.  Enable for research/batch use cases.
- ShipEngine live quotes not enabled by default; set `AEGIS_GEO_SHIPENGINE_KEY` to activate.

---

## Module layout

```
src/aegis/geo/
  __init__.py        — exports CrossMarketAnalyzer, Region, GeoOpportunity, …
  config.py          — Region enum, RegionConfig, HS_TARIFF_SCHEDULE, SHIPPING_MATRIX_USD
  schemas.py         — GeoOpportunity, GeoArbitrageReport, TariffLookupResult (frozen Pydantic v2)
  fx.py              — FXRateFetcher (Frankfurter/ECB, in-process TTL cache)
  tariffs.py         — TariffEstimator (WTO MFN static + UN Comtrade optional fallback)
  shipping.py        — ShippingResolver (static matrix + optional ShipEngine)
  demand.py          — RegionalDemandAnalyzer (Phase 1 signals DB + synthetic fallback)
  arbitrage.py       — CrossMarketAnalyzer (full O×D enumeration + scoring)
  phase6_bridge.py   — geo_opportunity_to_execution_intent() → Phase 6 ExecutionIntent
  api.py             — FastAPI router /geo/*
  cli.py             — Click: analyze, fx, tariff, shipping, regions

db/migrations/
  0008_geo_intelligence.sql  — geo_opportunities, geo_price_snapshots, geo_fx_snapshots

tests/unit/geo/       — 60+ unit tests (all mocked, no live infra needed)
tests/integration/geo/ — live FX + tariff + shipping integration tests
```

---

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `AEGIS_GEO_SHIPENGINE_KEY` | `""` (disabled) | ShipEngine API key for live shipping quotes |

FX rates use no API key (Frankfurter is open).
