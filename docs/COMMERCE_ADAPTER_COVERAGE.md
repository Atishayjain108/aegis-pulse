# Commerce Adapter Coverage (updated 2026-06-24)

What each commerce adapter actually carries. **Price** = a real price field;
**Reviews** = rating and/or review count; **Demand** = a genuine
demand/competition proxy (sold/watch/favorites/BSR/listing-count, not just media
chatter).

| Adapter | Key needed | Price | Reviews | Demand signal | Reliability | Notes |
|---|---|---|---|---|---|---|
| **ebay** | eBay Client ID/Secret (free) | ✅ USD via FX | ➖ (seller feedback) | ✅ listing total (competition) | 🟢 official API | never WAF-blocked; OAuth client-creds |
| **bestbuy** | Best Buy API key (free) | ✅ USD | ✅ rating + review count | ✅ review count | 🟢 official API | US catalog |
| **etsy** | Etsy keystring (free) | ✅ USD via FX | ➖ | ✅ favorites + views | 🟢 official API | niche / handmade |
| **amazon_in** | none | ⚠️ search-card + /dp/ | ✅ rating + reviews (/dp/) | ✅ **BSR** (/dp/, FlareSolverr) | 🟡 WAF; degraded w/o FlareSolverr | BSR is the strongest free Amazon demand proxy |
| **snapdeal** | none | ✅ INR | ✅ rating + reviews | ➖ | 🟡 HTML | most complete free Indian source |
| **flipkart** | none | ✅ INR (`__INITIAL_STATE__`) | ✅ rating + reviews | ➖ | 🟡 WAF; needs FlareSolverr | now parses JS blob (stabler than CSS) |
| **myntra** | none | ⚠️ gateway JSON | ⚠️ partial | ➖ | 🔴 WAF-heavy | query relevance fixed (rawQuery); coverage thin |
| **amazon** (global) | none | ➖ ranking only | ➖ | rank | 🟡 | T3 search ranks, no prices |

Legend: ✅ present · ⚠️ partial/conditional · ➖ not available · 🟢 durable · 🟡 fragile · 🔴 unreliable

## Removed (WAF-gated, no free path) — 2026-06-24
`nykaa`, `ajio`, `meesho`, `indiamart` — deleted from the active registry/waves.
They only generated 403s, CAPTCHAs and (indiamart) 20 s hangs. Do not re-add
without a real free API.

## Practical guidance
- **Durable coverage = the three real APIs (ebay/bestbuy/etsy).** Get those keys
  first; they don't break and don't get blocked. See `FREE_COMMERCE_SETUP.md`.
- **Indian marketplaces (amazon_in/flipkart/myntra/snapdeal)** are best-effort
  and want **FlareSolverr** running for reliability. From a datacenter IP they
  frequently serve degraded pages — a residential IP or FlareSolverr is the real
  ceiling, not adapter code.
- **Demand**, not price, is the scarce signal. Prefer eBay listing-count, Best
  Buy/Etsy review/favorite counts, and Amazon BSR over media-chatter velocity.
