# ADR-0006: Capital Execution Engine

**Status:** Accepted  
**Date:** 2026-06-01  
**Authors:** AEGIS Pulse  

---

## Context

Phase 4 already emits `ExecutionIntent` records — advisory signals that say "an ENTER opportunity was detected." Those intents have never been acted on. Phase 6 closes the loop: it converts intents into real orders, sizes positions with Kelly Criterion, routes to the cheapest-risk fulfillment method, and settles PnL at end of day.

The key constraint is irreversibility. Unlike scraping or predictions, placing an order cannot be undone for free. One misconfiguration could waste capital or trigger fraudulent orders on third-party platforms.

---

## Decision

### 1. Three-tier execution model

| Mode | Behavior | Use case |
|------|----------|----------|
| `advisory` | Log everything, place nothing | CI, dev, default |
| `staging` | Real orders, mock/Stripe-test payment | Integration testing |
| `live` | Irreversible, full capital at risk | Production only |

`AEGIS_EXECUTE_MODE` defaults to `"advisory"`. Promoting to `"live"` requires an explicit env override. The killswitch (`aegis:execute:killswitch`) immediately halts all live dispatch.

### 2. Kelly Criterion with 0.25× safety fraction

Position size = fractional Kelly, never exceeding 10% of capital per position.

```
f* = (p × b − q) / b        where b = profit/loss ratio
f_safe = 0.25 × max(0, f*)  (25% of optimal → 4× safety margin)
```

Phase 4's `KellyAdvisor` is reused directly. `ExecutionEngine.create_plan()` never reads `CAPITAL_*` constants at module level — all limits come from `ExecuteSettings` so tests can inject different values without touching the environment.

### 3. Approval workflow for P0/P1 plans

Plans that `require_approval=True` (non-auto-execute ENTER intents) must be approved via Telegram before dispatch. `ApprovalBroker` sends an inline-button message and waits up to `approval_timeout_s` (default 300 s). On timeout → auto-reject (plan stays `PENDING`, never executed).

P0 auto-execution is gated behind `AEGIS_EXECUTE_AUTO_EXECUTE_P0=true` (default false). This flag should never be set in CI.

### 4. Fulfillment routing by quantity

| Units | Method | Risk | Notes |
|-------|--------|------|-------|
| ≤ 5 | Print-on-demand (Printful) | Low | No inventory held |
| 6–20 | Dropship (CJ Dropshipping) | Medium | Supplier holds stock |
| > 20 | Inventory + Shopify draft order | High | Capital locked in stock |

All fulfillment clients degrade gracefully when API keys are absent — they return empty order ID lists rather than raising. The engine records `status="failed"` for those orders rather than crashing.

### 5. Daily drawdown circuit breaker

`ExecutionEngine._drawdown_breached()` checks `daily_pnl < -capital_daily_loss_limit_usd`. If breached, all subsequent `execute_plan()` calls return `status="halted_drawdown"`. This is reset at the start of each trading day via `reset_daily_pnl()`.

### 6. Settlement and tax reporting

`SettlementManager` tracks in-memory `OrderOutcome` objects (revenue, refund, shipping, fee) and writes to `execution_orders` at EOD via `settle_daily()`. `export_tax_csv()` generates a CSV for accountant handoff.

### 7. DB schema

Migration `0007_capital_execution.sql` adds:
- `execution_plans` — Kelly-sized plans with lifecycle status
- `execution_orders` — Individual fulfillment orders with outcome tracking (TimescaleDB hypertable on `created_at`)
- `daily_settlements` — EOD PnL snapshots

All tables use `app.current_tenant` RLS, matching all other AEGIS tables.

---

## Consequences

**Good:**
- Closes the scrape → predict → execute loop; predictions can now have measurable outcomes.
- Zero capital at risk in CI (`advisory` mode is enforced by default).
- Killswitch integration means any runaway can be halted in under 1 second.
- Settlement provides the ground-truth PnL data needed to retrain Phase 3 models (future).

**Bad / Accepted:**
- Complexity: +7 new modules, +3 fulfillment backends, +1 DB migration.
- Latency: approval workflow adds up to 300 s per P0/P1 plan in live mode.
- External dependencies: Printful, CJ Dropshipping, Shopify APIs are third-party; outages degrade to empty order lists.
- Fake recipient addresses in POD/dropship orders must be replaced with real customer data before production use.

---

## Alternatives Rejected

**Single fulfillment backend only:** Rejected — lock-in to one supplier concentrates risk and eliminates the low-capital POD path.

**Synchronous approval (HTTP poll):** Rejected — Telegram inline-button callback is asynchronous and avoids polling. The `asyncio.Event` pattern is cleaner than a polling loop.

**Capital management in Phase 3:** Rejected — Phase 3 produces predictions, not orders. Sizing + fulfillment belong in the execution layer (separation of concerns).
