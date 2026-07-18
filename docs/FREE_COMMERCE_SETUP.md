# Free Commerce Data Setup

AEGIS pulls commerce data (price, listings, reviews, demand) through **free,
legitimate** channels. The whole pipeline **runs fail-open without any of these
keys** — every adapter logs a `*.no_credentials` warning and returns `[]` when
its key is missing, so nothing breaks. Add keys to widen coverage and unlock the
durable real-API sources that never get WAF-blocked.

All keys are read from the environment (put them in `.env` or export them).

---

## 1. eBay Browse API — **highest priority** (real listings + listing-count demand)

Free, official, never blocked. ~5,000 calls/day on the free tier.

1. Go to <https://developer.ebay.com/> and sign in (an ordinary eBay account works).
2. Open **Developer Program → Application Keysets** and create a **Production** keyset.
3. Copy the **App ID (Client ID)** and **Cert ID (Client Secret)**.
4. Add to your environment:
   ```bash
   export AEGIS_EBAY_CLIENT_ID=YourAppId-xxxx-xxxx
   export AEGIS_EBAY_CLIENT_SECRET=PRD-xxxxxxxxxxxx
   # optional, default EBAY_US:
   export AEGIS_EBAY_MARKETPLACE_ID=EBAY_GB   # or EBAY_DE, EBAY_AU, ...
   ```
The adapter handles the OAuth client-credentials token flow and caches the token
until ~60 s before expiry. Prices are converted to USD via the existing ECB FX.

## 2. Best Buy Products API (US catalog price + real review counts)

1. Register at <https://developer.bestbuy.com/>.
2. Click **Get API Key** and confirm via email.
3. ```bash
   export AEGIS_BESTBUY_API_KEY=xxxxxxxxxxxxxxxx
   ```
Returns sale price (USD), `customerReviewCount` (a real demand proxy) and rating.

## 3. Etsy Open API v3 (niche / handmade listings + favorites demand)

1. Sign in at <https://www.etsy.com/developers/> → **Create a New App**.
2. Fill in name + a one-line description (read-only access is approved instantly).
3. Copy the **Keystring**.
4. ```bash
   export AEGIS_ETSY_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxx
   ```
Returns price (→ USD via FX), `num_favorers` and `views` — genuine niche demand.

---

## No-key sources that already work

- **Amazon `/dp/` BSR enrichment** — no key. The `amazon_in` adapter now fetches
  the product page for the top hits and extracts **Best Sellers Rank** (the
  strongest free Amazon demand proxy), rating, review count and price. From a
  datacenter IP Amazon often serves a *degraded* page without the BSR section;
  run **FlareSolverr** (`docker compose up -d aegis-flaresolverr`) and the
  adapter retries through it to get the full page. Disable with
  `AEGIS_AMAZON_BSR=0`. The enrichment is hard time-boxed so it never delays the
  search results.
- **Google Trends** — no key. Buyer-demand interest/slope is fetched **once per
  query at harvest** and stamped onto the candidate (see `AEGIS_DISABLE_TRENDS`).
- **flipkart / myntra** — no key, but fragile (HTML/WAF). Best-effort; need
  FlareSolverr for reliability.

## Removed sources (do not re-add)

`nykaa`, `ajio`, `meesho`, `indiamart` were removed on 2026-06-24: all are
WAF-gated (Akamai/DataDome) with **no free side door**. They only produced 403s,
CAPTCHAs and (indiamart) 20 s hangs. There is no free path; a real key/API would
be required to bring them back.

---

## Quick verification

```bash
# eBay (needs the key):
uv run aegis scrape --source ebay --query "wireless earbuds" --limit 10
# Best Buy / Etsy:
uv run aegis scrape --source bestbuy --query "blender" --limit 10
uv run aegis scrape --source etsy --query "ceramic mug" --limit 10
```
With no key set, each prints a `no_credentials` warning and returns 0 rows — the
rest of the pipeline is unaffected.
