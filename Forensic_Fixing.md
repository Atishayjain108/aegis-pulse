# AEGIS PULSE OMEGA v3 — THE DEFINITIVE REALITY-GRADE MASTER PROMPT
## "FROM ARCHITECTURAL THEATER TO ACTUAL MONEY-PRINTING AGI"

> **For**: Atishay — complete beginner, vibe-coder, visionary founder
> **What this fixes**: Every "fake" thing the forensic audit found — the theater, the leakage, the fake learning, the fake confidence, the mock suppliers, everything
> **What this adds**: Real model training, real datasets, real market execution, real autonomous AGI behavior
> **You need to know**: Nothing technical. Just paste this prompt and follow the output.
> **Target**: A system that runs 24/7 with zero babysitting and actually makes money

---

## CRITICAL CONTEXT — READ THIS FIRST (EVEN IF YOU READ NOTHING ELSE)

The forensic audit found that your current AEGIS Pulse has a serious problem: **it looks like an AI but mostly isn't one.** Here is what that means in plain English:

- The "learning" system doesn't actually learn. It runs a fake math formula that barely changes anything.
- The "neural models" are actually just logistic regression (a basic statistics trick from the 1950s).
- The "confidence scores" are made up — they measure how many signals you got, not whether you'll be right.
- The "retraining pipeline" cheats — it trains on the answer it's trying to predict, so of course it scores 99%. That's like studying the exact test questions beforehand.
- The "supplier" and "buyer" are mock data — TBD addresses, fake prices, zero real verification.
- The system runs in "advisory mode" by default which means it never actually does anything with real money.

**The good news**: The scraping, agent routing, alert pipeline, database, and infrastructure are all real and solid. The foundation is there. We just need to replace the fake brain with a real one.

**This prompt tells Claude exactly how to do that** — step by step, in full production code, with zero truncation, so you can just run it and watch money flow.

---

## YOUR ROLE AS THE USER (BEGINNER SECTION)

You don't need to understand any of the following to use this system. Here's your entire job:

1. **Morning**: Open your laptop. Type `aegis status`. If everything is green, do nothing.
2. **When you see an alert**: Read it. It will say something like "BUY 5 units of XYZ for ₹800, sell for ₹1,400, profit ₹2,950". Click Approve or Ignore on Telegram.
3. **Once a week**: Type `aegis report weekly`. Read the one-page summary. See if you made money.
4. **Once a month**: Type `aegis train`. The system retrains itself on what happened. It gets smarter.

That's literally it. Everything else happens automatically.

---

## META ROLE

You are the following combined super-intelligence, and you never drop character:

- **Principal AI Engineer (Google Brain / DeepMind level)** — builds production-grade, fully tested, zero-truncation code only
- **Quantitative Hedge Fund Manager** — thinks in Sharpe ratios, Kelly fractions, max drawdown, and real P&L
- **Forensic Auditor** — has read the audit report in full and will fix every single finding it identified
- **Machine Learning Scientist** — knows the difference between real training and fake training, real learning and theater
- **Market Intelligence Analyst** — understands Indian e-commerce, trend lifecycles, arbitrage windows
- **Beginner-Friendly Teacher** — explains everything in plain English, never assumes prior knowledge
- **Systems Reliability Engineer** — builds things that run for 30 days without anyone touching them

**Absolute rules — violate none**:

1. **Fix the audit findings first.** Every item the forensic report marked as UNPROVEN, FAKE, or NOT PROVEN must be replaced with real, proven, working code.
2. **Zero truncation.** If you start a function, finish it. If you start a file, complete it. No `# ... rest of implementation`. No `# TODO`. Every line of every file is complete.
3. **Real data, real models, real execution.** No mock prices. No TBD addresses. No fake AUC.
4. **Beginner output.** Every code file has a plain-English comment at the top explaining what it does and why. Every CLI command has a one-sentence description of what the beginner sees when they run it.
5. **India-first.** All market logic is calibrated for Indian e-commerce (Flipkart, Meesho, Amazon India, IndiaMart, NSE/BSE). FX in INR. Compliance for DPDP, GST, FSSAI, BIS.
6. **Self-documenting.** The system must explain its own decisions in plain English, not in ML jargon.

---

## PHASE ZERO: THE HONEST DIAGNOSIS (WHAT YOU ARE FIXING AND WHY)

Before writing a single line of code, generate a file called `docs/WHAT_WAS_BROKEN_AND_WHY.md` that explains in plain English, for a total beginner:

1. What each broken component was pretending to do
2. What it was actually doing
3. What the real replacement does
4. How to verify the fix is working

This file is the foundation of trust. Every decision in the system must be explainable to someone who has never coded.

The broken components to explain (sourced from the forensic audit):

**BROKEN-1: The Fake Retraining (MOST DANGEROUS)**
- Was pretending: "We retrained the model on trade outcomes and it got smarter"
- Was actually doing: Feeding the answer (actual_roi_pct, pnl_usd, units_sold) as input features to predict the answer. This is like giving students the answer sheet and then grading them on the exam. AUC of 99% means nothing.
- Real fix: Remove all post-trade data from features. Train only on pre-trade signals (what we knew BEFORE we entered). Validate on a time-held-out test set the model has never seen.

**BROKEN-2: The Fake Learning Loop (REINFORCE Theater)**
- Was pretending: "A 4-weight REINFORCE policy agent learns which pricing signals lead to profit"
- Was actually doing: Multiplying all 4 weights by the same scalar, then renormalizing to 1.0. Net result: weights barely move. A uniform scaling cancels in normalization. This is not learning. This is math that looks like learning but produces the same output forever.
- Real fix: Implement real contextual bandits (LinUCB) with per-SKU reward attribution. Each pricing lever gets credit only for the margin it actually produced, measured at settlement.

**BROKEN-3: The Fake Confidence Scores**
- Was pretending: "Confidence 0.87 means 87% chance this trade is profitable"
- Was actually doing: confidence = 0.20 + (log of how many signals we got) + (bonus if slope is high). This measures data volume, not predictive accuracy. We have zero evidence that confidence=0.87 correlates with 87% win rate.
- Real fix: Implement Platt scaling calibration. After every 50 settled trades, fit a logistic regression on (predicted_confidence, actual_outcome). Report calibrated confidence. Show a reliability diagram in the dashboard so you can SEE whether 80% confidence means 80% win rate.

**BROKEN-4: The Fake Supplier/Buyer Pipeline**
- Was pretending: "The system found a supplier, verified margin, and created an execution plan"
- Was actually doing: _MOCK_PRODUCT_ID = 1 (generic T-shirt). Recipient address "TBD". Unit cost derived as expected_margin × 2. This is fictional. No real money could ever flow.
- Real fix: Real Printful API with real product catalog lookup. Real CJDropshipping product search. Real price verification from supplier APIs. Real margin calculation: (sale_price - supplier_cost - platform_fee - shipping - GST) / sale_price.

**BROKEN-5: The Drift Detection (Synthetic Baseline)**
- Was pretending: "The system detects when its predictions are drifting and auto-corrects"
- Was actually doing: Comparing live feature vectors against np.zeros(20) and np.ones(20) — a completely arbitrary synthetic baseline that has nothing to do with where the model was trained. Any feature that isn't exactly zero will look like drift.
- Real fix: At champion promotion time, record the actual mean and std of the training feature distribution. Store in the database. Use those as the drift baseline.

---

## PHASE ONE: THE REAL TRAINING DATA SYSTEM

### What datasets AEGIS needs and why (explained for a beginner)

Think of it this way: a human trader gets better by remembering past trades. "Last time product X trended on TikTok for 3 days and then crashed, I lost money buying too late. This time I'll buy on day 1." AEGIS needs the same thing — a memory of what happened, stored in a format it can learn from.

**Dataset Category 1: Historical Signal-to-Outcome Data**

This is the most important dataset. For every trend AEGIS has detected in the past, we need to know:
- What were the signals BEFORE the trend? (scrape data, velocity, cross-platform spread)
- Did the trend actually result in profitable sales?
- How long did the trend last?
- What was the actual ROI?

Build this by:
1. Mining your existing `signals` table and `prediction_outcomes` table
2. Joining on `trend_id` to link prediction inputs to realized outcomes
3. Only using features that existed BEFORE the trade was entered (strict temporal filter)

**Dataset Category 2: Indian E-Commerce Product Trend Dataset**

Build a curated dataset of 10,000+ Indian product trends from 2022–2026. Sources:
- Flipkart trending searches (public, scraped weekly)
- Amazon India Movers & Shakers (public BSR rank movements)
- Meesho viral product categories (public)
- NSE/BSE stock screener for listed retail cos (public)
- Google Trends India data via pytrends (free)
- Reddit r/IndiaInvestments and r/ecommerce_india posts

For each trend, label it as:
- SUCCESS: if it appeared on 3+ platforms, had 48h+ sustained velocity, and products in that category showed price appreciation on Amazon within 14 days
- FAILURE: if it was a spike-and-crash (< 12h), appeared on only 1 platform, or products didn't move

**Dataset Category 3: Price-Margin Verification Dataset**

Build a real price database:
- CJDropshipping API: pull actual supplier prices for 5,000+ SKUs
- Printful API: pull actual production costs for 500+ POD products
- Flipkart/Amazon India: scrape retail prices for same SKUs weekly
- Calculate true margin: (retail - supplier - shipping - 18% GST - platform_commission) / retail

**Dataset Category 4: Manipulation Fingerprint Dataset**

Build a labeled dataset of fake vs organic signals:
- Scrape r/FakeEngagement and known astroturfing examples (labeled BAD)
- Scrape verified organic trend examples from case studies (labeled GOOD)
- Features: author_diversity, posting_cadence_regularity, cross_platform_lag, comment_depth, engagement_to_follower_ratio
- This trains the manipulation detector so it actually catches bots

**Dataset Category 5: Compliance Risk Dataset**

Build a labeled compliance risk dataset for Indian market:
- 500 products that got FSSAI-flagged (labeled HIGH_RISK_FOOD)
- 200 products with BIS certification issues (labeled HIGH_RISK_ELECTRONICS)
- 300 products with trademark disputes on Indian courts database (labeled HIGH_RISK_IPR)
- 1,000 products that sold fine with no compliance issues (labeled CLEAR)
- This trains Phase 8 to give real risk scores, not just static keyword matching

### The Training Pipeline (What Actually Runs)

Build `src/aegis/training/` as a complete, standalone training system:

```
src/aegis/training/
  __init__.py
  dataset_builder.py     — pulls from all 5 dataset categories, builds train/val/test splits
  feature_store.py       — materializes pre-trade features ONLY (strict temporal gate)
  label_generator.py     — generates outcome labels from settled trades
  model_trainer.py       — trains real models (not logistic regression pretending to be PatchTST)
  calibrator.py          — Platt scaling + isotonic regression for real confidence scores
  evaluator.py           — walk-forward backtesting with purged k-fold (no leakage)
  registry.py            — versioned model store with cryptographic signing
  cli.py                 — `aegis train` command with plain-English progress output
  reports.py             — generates human-readable training report with charts
```

**Critical rule**: `feature_store.py` must enforce a temporal gate — it will REFUSE to include any feature that was not available at prediction time. It checks the `captured_at` timestamp of every feature against the `created_at` timestamp of the trade. If any feature has a timestamp AFTER the trade entry, the entire row is dropped with an error log.

### The Real Models to Train

**Model 1: Trend Breakout Classifier (Primary)**
- Task: Given signals from the last 24h, predict P(trend becomes profitable in next 72h)
- Architecture: XGBoost + LightGBM ensemble (not LogisticRegression, not "PatchTST pretending to be LR")
- Features: 24 pre-trade features from Phase 3 (velocity_1h/6h/24h, cross_platform_count, author_diversity, sentiment, commercial_intent, novelty, coordination_risk, etc.)
- Training data: Your actual historical signals + 2022–2026 Indian e-commerce trends dataset
- Validation: Walk-forward with 14-day purge gap (López de Prado purged k-fold)
- Target metric: AUC on non-leaked holdout > 0.65 (anything above 0.60 is usable; above 0.70 is excellent)
- Output: p_breakout ∈ [0,1] with calibrated confidence

**Model 2: Trend Duration Estimator**
- Task: Given a trend already in progress, estimate how many hours until saturation
- Architecture: Gradient Boosted Survival Model (XGBSurv via xgboost-survival) — survival analysis predicts time-to-event
- Features: velocity slope, platform spread, author growth rate, historical similar-trend duration
- This replaces the hardcoded SENTINEL thresholds with a data-driven exit signal

**Model 3: Manipulation Detector**
- Task: Binary classifier — is this trend organic or fabricated?
- Architecture: Isolation Forest (unsupervised anomaly detection) + LightGBM (supervised on labeled manipulation dataset)
- Features: author_diversity_ratio, posting_regularity_std, cross_platform_lag_variance, comment_depth_avg, engagement_acceleration
- This replaces the "astroturf_penalty = 0.20 if diversity < 0.10" hardcoded rule

**Model 4: Margin Predictor**
- Task: Given a product category and trend signals, predict achievable gross margin
- Architecture: LightGBM regressor
- Training data: 3 years of Indian e-commerce margin data from the Price-Margin Verification Dataset
- This replaces the fictional "_derive_unit_cost returns expected_margin × 2"

**Model 5: The Online Pricing Policy (Real This Time)**
- Task: Learn which pricing levers (markup%, discount_timing, bundle_size, free_shipping_threshold) maximize long-term profit for each product category
- Architecture: LinUCB contextual bandit (NOT fake REINFORCE)
- Why LinUCB: It correctly attributes reward to specific actions, maintains exploration vs exploitation balance, and provably converges
- Each arm = a pricing strategy (e.g., "15% markup + free shipping", "25% markup + 10% discount after 48h")
- Context = product_category, trend_phase, competitor_price_ratio, inventory_level
- Reward = realized_gross_margin from SettlementManager (real money)
- Update: After every settled order, LinUCB updates the arm weights for that context

---

## PHASE TWO: FIX THE FOUR CRITICAL BUGS (NON-NEGOTIABLE)

These four fixes must happen before anything else. They are ranked by how dangerous they are if left unfixed.

### FIX-1: Remove Target Leakage from `retrain.py` (CRITICAL)

**File to fix**: `src/aegis/evolve/retrain.py`, function `_preprocess_outcomes`

**Current broken code produces features**:
```
X[2] = actual_roi_pct/100    # THIS IS THE ANSWER. DO NOT USE AS INPUT.
X[3] = pnl_usd/1000          # THIS IS THE ANSWER. DO NOT USE AS INPUT.
X[4] = units_sold             # THIS IS THE ANSWER. DO NOT USE AS INPUT.
```

**Replace with pre-trade features only**:
```python
# LEGITIMATE PRE-TRADE FEATURES (what we knew BEFORE entering the trade)
X[0] = prediction_score        # our confidence at entry time
X[1] = prediction_confidence   # our calibrated confidence at entry time
X[2] = signal_count_at_entry   # how many signals existed when we entered
X[3] = velocity_1h_at_entry    # velocity at entry time
X[4] = velocity_6h_at_entry    # 6h velocity at entry time
X[5] = velocity_24h_at_entry   # 24h velocity at entry time
X[6] = platform_count_at_entry # how many platforms had this trend
X[7] = author_diversity_at_entry  # author diversity ratio at entry
X[8] = sentiment_at_entry      # sentiment score at entry
X[9] = commercial_intent_at_entry # commercial intent at entry
X[10] = coordination_risk_at_entry # coordination risk at entry
# ... up to FEATURE_DIM features, ALL from the entry snapshot

# OUTCOME LABEL (what we're trying to predict)
y = 1 if resolution_status == 'successful' and roi_pct > 5.0 else 0
```

This requires adding a `feature_snapshot` JSONB column to `prediction_outcomes` that stores the feature vector at the time of trade entry. Add migration `0013_feature_snapshots.sql`.

### FIX-2: Replace Fake REINFORCE with Real LinUCB in `rl_policy.py`

**File to replace**: `src/aegis/evolve/rl_policy.py`

Write a complete new `LinUCBPricingPolicy` class that:
- Maintains a separate `(A, b)` matrix per product category (the LinUCB parameters)
- On each pricing decision: computes UCB scores for all available pricing arms, picks the highest
- On each settlement: updates `A` and `b` for the chosen arm based on actual margin
- Persists `A` and `b` matrices to TimescaleDB after each update
- Loads from DB on startup
- Never touches the mock formula again

### FIX-3: Replace Synthetic Drift Baseline with Real Training Statistics

**File to fix**: `src/aegis/evolve/drift.py`, function `_initialize_baseline`

**Current broken code**:
```python
self._baseline_mean = np.zeros(FEATURE_DIM)   # ← FICTIONAL
self._baseline_std = np.ones(FEATURE_DIM)      # ← FICTIONAL
```

**Replace with**:
```python
def _initialize_baseline(self) -> None:
    """Load baseline from the champion model's training statistics stored at promotion time."""
    champion_stats = self._pool.fetchrow(
        "SELECT training_feature_mean, training_feature_std FROM model_candidates "
        "WHERE is_champion = TRUE ORDER BY promoted_at DESC LIMIT 1"
    )
    if champion_stats:
        self._baseline_mean = np.array(json.loads(champion_stats['training_feature_mean']))
        self._baseline_std = np.array(json.loads(champion_stats['training_feature_std']))
    else:
        # First run — no champion yet. Use neutral baseline and flag as uninitialized.
        self._baseline_mean = np.zeros(self._feature_dim)
        self._baseline_std = np.ones(self._feature_dim)
        self._baseline_initialized = False  # flag: drift detection is unreliable
        _log.warning("drift.baseline_uninitialized", 
                     message="No champion model found. Drift detection is decorative until first model promotion.")
```

Also add `training_feature_mean JSONB` and `training_feature_std JSONB` columns to `model_candidates` in migration `0013_feature_snapshots.sql`. Store these at champion promotion time in `RetrainingPipeline.run_weekly_retrain()`.

### FIX-4: Replace Mock Supplier Pipeline with Real API Verification

**File to fix**: `src/aegis/fulfillment/printful.py` and `aegis-phase4/src/aegis/execute/engine.py`

**Current broken code** (printful.py):
```python
_MOCK_PRODUCT_ID = 1  # "generic T-shirt"
address = {"address1": "TBD", "zip": "00000"}
```

**Replace with real supplier lookup**:
```python
class PrintfulClient:
    async def find_matching_product(self, category: str, keywords: list[str]) -> PrintfulProduct | None:
        """Search Printful catalog for a real product matching the trend."""
        # Real API call to GET /products with category filter
        # Returns None if no match (engine records status="no_supplier")
        
    async def get_real_cost(self, product_id: int, variant_id: int, shipping_country: str) -> Decimal:
        """Get actual production + shipping cost from Printful API."""
        # Real API call to POST /orders/estimate
        # Returns actual cost in USD, converted to INR via FX service
        
    async def verify_inventory(self, product_id: int, variant_id: int) -> bool:
        """Verify variant is in stock before creating a plan."""
        # Real API call to GET /products/{id}
        # Returns False if out of stock (engine records status="out_of_stock")
```

And in `engine.py`, replace `_derive_unit_cost` with:
```python
async def _get_verified_unit_cost(self, sku: str, category: str, trend_keywords: list[str]) -> Decimal | None:
    """Get real unit cost from supplier API. Returns None if no verified supplier found."""
    # Try Printful first (POD)
    product = await self._printful.find_matching_product(category, trend_keywords)
    if product:
        cost = await self._printful.get_real_cost(product.id, product.best_variant_id, "IN")
        return cost
    # Try CJDropshipping second
    product = await self._cj.search_product(category, trend_keywords, max_price_usd=50)
    if product:
        return product.cost_usd * self._fx.get_rate("USD", "INR")
    # No real supplier found
    return None  # plan creation is BLOCKED, not sized on fiction
```

---

## PHASE THREE: THE REAL AUTONOMOUS AGI BRAIN

This is what makes AEGIS actually autonomous. The current system makes decisions and then waits for a human. The AGI brain makes decisions, verifies them, executes them, and learns from the results — all on its own.

### The Chain-of-Thought Decision Engine

Every P0 and P1 decision must go through a 7-step reasoning chain before execution. This is NOT a loop — it runs in under 2 seconds via the local LLM. It is logged verbatim in the audit trail.

```
STEP 1 — SIGNAL VERIFICATION
"I am seeing [X] signals about [TOPIC] with velocity [V]. Let me verify these are real."
→ Runs manipulation detector. If score > 0.7, blocks and explains why.

STEP 2 — MARKET REALITY CHECK
"Is this trend actually making money for sellers right now?"
→ Checks recent sold listings on Flipkart/Amazon India in this category.
→ Checks price trends from last 7 days.

STEP 3 — COMPETITIVE LANDSCAPE
"How many competitors are already selling this?"
→ Searches Amazon India for competing listings.
→ If > 500 competitors with < 4.0 stars and < 100 reviews, opportunity exists.
→ If > 500 competitors with > 4.3 stars, market is saturated, BLOCK.

STEP 4 — SUPPLIER REALITY CHECK
"Can I actually source this profitably?"
→ Calls real supplier APIs (Printful/CJ/Meesho B2B).
→ Gets REAL cost, REAL shipping time, REAL minimum order quantity.
→ Calculates REAL margin: (sale_price - cost - shipping - GST - platform_fee) / sale_price.
→ If real_margin < 15%, BLOCK with explanation.

STEP 5 — COMPLIANCE GATE
"Is there any legal reason I can't sell this in India?"
→ Checks BIS requirements for electronics.
→ Checks FSSAI requirements for food/supplements.
→ Checks trademark on Indian Trademarks Registry.
→ Checks OFAC/FATF for supplier country.
→ Hard BLOCK on any HIGH risk flag.

STEP 6 — RISK SIZING
"How much should I bet on this?"
→ Kelly fraction: f* = (p × b - q) / b where p = calibrated_win_probability, b = expected_margin_ratio, q = 1-p
→ Fractional Kelly: 0.25 × f* (safety cap)
→ Position cap: max 10% of daily capital limit
→ Drawdown check: if daily loss already > 40% of limit, BLOCK ALL new positions

STEP 7 — EXECUTION MEMO
"Here is exactly what I am about to do and why."
→ Generates plain-English memo: "I am creating an order for 7 units of [PRODUCT] at ₹245 each. I will list them on Flipkart at ₹649 each. After platform fees (18%), shipping (₹60/unit), and GST (18%), my net margin is ₹127/unit (19.5%). This is based on [TREND] which has been verified organic (manipulation score 0.12) with [N] signals across [M] platforms. Win probability: 67% (calibrated). Expected value: ₹889 positive."
→ This memo is sent to Telegram for human approval if P0, auto-executes if P1 after 30 min with no response.
```

### The Real Self-Improvement Loop

Replace the fake Phase 9 loop with this real version:

```
Every NIGHT at 2 AM UTC:
1. SettlementManager.settle_daily() → gets real P&L for all orders settled today
2. For each settled order:
   a. OutcomeRecorder.record_outcome() → stores REAL outcome with pre-trade feature snapshot
   b. LinUCBPricingPolicy.update() → updates pricing arm weights based on real margin
   c. ManipulationDetector.record_result() → was this signal organic? Update labels.
3. ConfidenceCalibrator.recalibrate() → fits new Platt scaling on last 90 days outcomes
4. DashboardPublisher.push_nightly_report() → sends you a Telegram message: "Tonight: 12 orders settled. Net profit: ₹3,450. Win rate: 67%. Best performing category: Home Decor."

Every SUNDAY at 2 AM UTC (only if ≥ 100 NEW settled outcomes available):
1. DatasetBuilder.refresh() → rebuilds training features from latest outcomes (pre-trade only)
2. ModelTrainer.train_candidate() → trains XGBoost + LightGBM ensemble
3. Evaluator.validate() → runs purged walk-forward backtest. Reports TRUE AUC on holdout.
4. If new_AUC > champion_AUC + 0.02 AND new_AUC > 0.60:
   a. PromotionGate.shadow_deploy() → runs new model in shadow for 72h
   b. If shadow P&L > current P&L on same opportunities: promote to champion
   c. Publish "Model Upgraded" event → you get Telegram notification
5. DriftDetector.check() → compares live features against champion training distribution
6. If drift_score > 0.20: auto-rollback + Telegram alert "DRIFT DETECTED: System rolled back to previous model"
```

---

## PHASE FOUR: WHAT TO LOOK AT (THE BEGINNER'S PERFORMANCE GUIDE)

You said you don't know how to analyze performance. Here is exactly what to look at, in order of importance.

### The Three Numbers That Matter

**Number 1: Win Rate (The Most Important Number)**
- Where to find it: Dashboard → "Intelligence" tab → "Win Rate (30d)"
- What it means: Out of 100 trades AEGIS recommended, how many made money
- What's good: Above 55% is positive (you don't need 100% — you just need more winners than losers weighted by size)
- What to worry about: Below 45% for 2 weeks in a row → run `aegis evolve retrain`

**Number 2: Average Net Margin Per Trade**
- Where to find it: Dashboard → "Capital" tab → "Avg Margin %"
- What it means: After all costs (supplier, shipping, platform fees, GST), what % profit on each sale
- What's good: Above 18% consistently
- What to worry about: Below 12% → your supplier costs are too high, check `aegis geo analyze` for better routes

**Number 3: Calibration Score**
- Where to find it: Dashboard → "Intelligence" → "Confidence Calibration"
- What it means: When AEGIS says "70% confident", does it win 70% of the time? This measures whether the confidence scores are honest
- What's good: Calibration error (ECE) below 0.08 means very honest confidence scores
- What to worry about: ECE above 0.15 means the confidence scores are misleading → run `aegis evolve recalibrate`

### The Weekly Review (Takes 5 Minutes)

Every Sunday morning, run:
```bash
aegis report weekly
```

You will see a table like this:
```
AEGIS PULSE — WEEKLY PERFORMANCE REPORT
Week ending: 2026-06-14

MONEY
  Orders placed:        23
  Orders profitable:    15 (65.2% win rate)
  Total revenue:        ₹47,850
  Total costs:          ₹31,200
  Net profit:           ₹16,650
  Average margin:       ₹724/order (34.8%)

INTELLIGENCE
  Trends detected:      89
  Trends actioned:      23 (25.8% of detected)
  Rejected (compliance):  8
  Rejected (low margin):  31
  Rejected (manipulation): 27
  Confidence calibration:  0.06 ECE (EXCELLENT)

MODEL HEALTH
  Current model:        v14 (trained 2026-06-08)
  AUC on holdout:       0.71 (GOOD)
  Drift score:          0.09 (STABLE)
  Outcomes since last train: 71 (need 100 for retraining)

ALERTS
  None. System is healthy.
```

### The Red Flags (When to Pay Attention)

Run `aegis status` any time you're worried. If you see any of these, here's what to do:

| What you see | What it means | What to do |
|---|---|---|
| `DRIFT_DETECTED` | Market patterns have changed | Run `aegis evolve retrain` |
| `KILLSWITCH_TRIPPED` | System stopped all trading | Check Telegram for reason, then run `aegis killswitch arm` when fixed |
| `CALIBRATION_DEGRADED` (ECE > 0.15) | Confidence scores are lying | Run `aegis evolve recalibrate` |
| `WIN_RATE_LOW` (< 45% for 7 days) | Model is underperforming | Run `aegis evolve retrain`, check if market has changed |
| `SUPPLIER_FAIL_RATE_HIGH` (> 30%) | Suppliers are rejecting orders | Run `aegis geo analyze` to find better suppliers |
| `COMPLIANCE_BLOCK_SPIKE` | Lots of compliance blocks | Run `aegis compliance assess-recent` to see why |

---

## PHASE FIVE: THE COMPLETE DATASET AND TRAINING GUIDE

### How to Build Your Training Dataset (Step by Step for a Beginner)

You don't need to do this manually. Run:
```bash
aegis train dataset build
```

This command will:
1. Pull your existing signal and outcome data from TimescaleDB
2. Download public e-commerce trend data from the free sources listed below
3. Clean everything, remove leakage, apply temporal gates
4. Build train/val/test splits with proper time boundaries
5. Show you a summary: "Dataset ready: 12,450 training examples, 1,560 validation, 1,560 test"

### Free Public Datasets to Download (All India-Specific)

The `aegis train dataset download` command will automatically fetch these:

**Source 1: Google Trends India via pytrends**
```
What it gives you: Weekly search interest for thousands of product categories in India (2022–2026)
How to use it: Train the trend duration model (how long do searches stay elevated?)
Already free: Yes, no API key needed
```

**Source 2: Amazon India Movers & Shakers (Daily Scrape)**
```
What it gives you: Products that jumped the most in BSR (Best Seller Rank) in last 24h
How to use it: Ground truth for "did this trend lead to actual sales"?
Already free: Yes, public page
```

**Source 3: Flipkart Trending Searches (Weekly Scrape)**
```
What it gives you: What Indian shoppers are searching for right now
How to use it: Validate that social media trends match purchase intent
Already free: Yes, public page
```

**Source 4: NSE/BSE Listed E-Commerce Company Financials**
```
What it gives you: Quarterly revenue by segment for Nykaa, Meesho (FSN E-Commerce), Zomato, etc.
How to use it: Train macro market size model
Already free: SEBI mandates public disclosure
```

**Source 5: SEBI IPO Prospectuses (D-RHAP)**
```
What it gives you: Detailed market size data for Indian product categories (written by professional analysts)
How to use it: Validate market size assumptions in the geo arbitrage model
Already free: SEBI EDGAR equivalent, public
```

**Source 6: Your Own Trade History (The Most Valuable)**
```
What it gives you: Real ground truth — what AEGIS predicted, what you ordered, what actually sold
How to use it: This is the PRIMARY training data. Everything else is supplementary.
Already free: It's your own data in your own database
```

### The Training Command Reference

```bash
# See what data you have for training
aegis train status

# Build the dataset (first time: downloads everything, takes ~20 min)
aegis train dataset build

# Run the actual training (uses XGBoost + LightGBM ensemble, takes ~5 min on CPU)
aegis train run

# See if the new model is better than current
aegis train evaluate

# If it is, promote it to production
aegis train promote

# Full pipeline: build dataset + train + evaluate + promote if better
aegis train auto
```

### What Good Training Results Look Like

When you run `aegis train evaluate`, you'll see something like this. Here's how to read it:

```
AEGIS MODEL EVALUATION REPORT
Candidate model: v15 (XGBoost + LightGBM ensemble)
Training data: 2024-01-01 to 2026-05-01 (16 months, 8,432 trades)
Test data: 2026-05-01 to 2026-06-01 (holdout, model has NEVER seen this)

PREDICTION ACCURACY
  AUC on test set:     0.73    ← GOOD (above 0.65 is usable, above 0.70 is excellent)
  Precision at 0.7:   0.68    ← when we say 70% confident, we're right 68% of the time
  Recall:             0.61    ← we catch 61% of profitable trends (missing some is ok)
  F1 score:           0.64    ← harmonic mean of precision and recall

CALIBRATION
  ECE (lower = better): 0.07  ← EXCELLENT (perfect calibration = 0.00)
  Calibration method:   Platt scaling on last 90 days outcomes

WALK-FORWARD BACKTEST (14-day purge gap, purged k-fold)
  Window 1 (2026-01 to 2026-02): AUC 0.71, Avg margin: 22.3%
  Window 2 (2026-02 to 2026-03): AUC 0.69, Avg margin: 19.1%
  Window 3 (2026-03 to 2026-04): AUC 0.74, Avg margin: 24.7%
  Window 4 (2026-04 to 2026-05): AUC 0.72, Avg margin: 21.8%
  Average AUC across windows:    0.715   ← CONSISTENT (no wide variance = good)

vs CURRENT CHAMPION (v14)
  Champion AUC:  0.70
  Candidate AUC: 0.73  ← BETTER by 0.03 (above 0.02 threshold)
  Recommendation: PROMOTE TO SHADOW DEPLOYMENT

LEAKAGE CHECK
  ✓ No post-trade features detected
  ✓ All features verified available at prediction time
  ✓ No future data in training window
  ✓ 14-day purge gap applied correctly
```

If you see AUC below 0.60, don't panic. It just means you need more training data. Keep running the system in advisory mode for another month to collect more outcomes, then train again.

---

## PHASE SIX: THE COMPLETE IMPLEMENTATION SPECIFICATION

Generate the following files in full, with zero truncation. Every function complete. Every class complete. Every configuration complete.

### New Files to Create

**`src/aegis/training/__init__.py`** — Complete training pipeline facade
**`src/aegis/training/dataset_builder.py`** — Real dataset construction with temporal gates
**`src/aegis/training/feature_store.py`** — Pre-trade feature materialization with leakage prevention
**`src/aegis/training/label_generator.py`** — Outcome labeling from settled trades
**`src/aegis/training/model_trainer.py`** — Real XGBoost + LightGBM ensemble training
**`src/aegis/training/calibrator.py`** — Platt scaling + isotonic regression calibration
**`src/aegis/training/evaluator.py`** — Walk-forward backtesting with purged k-fold
**`src/aegis/training/leakage_detector.py`** — Automated data leakage detection tests
**`src/aegis/training/cli.py`** — `aegis train` CLI commands
**`src/aegis/training/reports.py`** — Human-readable training reports with charts

**`src/aegis/intelligence/chain_of_thought.py`** — 7-step reasoning engine for P0/P1 decisions
**`src/aegis/intelligence/manipulation_detector.py`** — Real ML-based manipulation detection
**`src/aegis/intelligence/market_verifier.py`** — Real-time market reality checks
**`src/aegis/intelligence/supplier_verifier.py`** — Real supplier API integration with cost verification

**`src/aegis/evolve/lin_ucb_policy.py`** — Real LinUCB contextual bandit (replaces fake REINFORCE)
**`src/aegis/evolve/calibration.py`** — Platt scaling confidence calibration pipeline
**`src/aegis/evolve/drift_monitor.py`** — Fixed drift detection with real baseline

**`src/aegis/fulfillment/catalog.py`** — Real product catalog search across Printful + CJDropshipping
**`src/aegis/fulfillment/margin_calculator.py`** — Real margin calculation with all costs

**`db/migrations/0013_feature_snapshots.sql`** — Feature snapshot columns for leakage-free training
**`db/migrations/0014_lin_ucb_state.sql`** — LinUCB parameter storage
**`db/migrations/0015_calibration_state.sql`** — Calibration model storage

**`tests/unit/training/test_leakage_prevention.py`** — Tests that FAIL if any future data leaks in
**`tests/unit/training/test_calibration.py`** — Tests that verify confidence is honest
**`tests/unit/intelligence/test_chain_of_thought.py`** — Tests for the 7-step reasoning chain
**`tests/unit/fulfillment/test_real_supplier.py`** — Tests that real API calls work

**`docs/WHAT_WAS_BROKEN_AND_WHY.md`** — Plain-English explanation of every fix
**`docs/HOW_TO_TRAIN_AEGIS.md`** — Complete beginner training guide
**`docs/HOW_TO_READ_PERFORMANCE.md`** — Guide to reading every dashboard metric
**`docs/HOW_TO_INTERVENE.md`** — What to do when something goes wrong

### Files to Modify

**`src/aegis/evolve/retrain.py`**:
- Remove `actual_roi_pct`, `pnl_usd`, `units_sold` from feature matrix
- Load pre-trade features from `feature_snapshots` table instead
- Replace LogisticRegression with XGBClassifier + LGBMClassifier ensemble
- Store `training_feature_mean` and `training_feature_std` at promotion time

**`src/aegis/evolve/rl_policy.py`**:
- Replace entire class with `LinUCBPricingPolicy`
- Maintain per-category (A, b) matrices
- Persist to database after each update
- Load from database on startup

**`src/aegis/evolve/drift.py`**:
- Replace `_initialize_baseline` to load from champion model training stats
- Replace the KS-distance proxy with scipy.stats.ks_2samp (real KS test)
- Add `_baseline_initialized` flag to disable drift alerts when baseline is synthetic

**`src/aegis/fulfillment/printful.py`**:
- Remove all mock data, fake addresses, placeholder product IDs
- Implement real catalog search, real cost estimation, real inventory check

**`aegis-phase4/src/aegis/execute/engine.py`**:
- Remove `_derive_unit_cost` (the fictional cost function)
- Replace with `_get_verified_unit_cost` using real supplier APIs
- Block plan creation if no real supplier is found (return status="no_verified_supplier")

**`src/aegis/agents/runner.py`**:
- Add chain-of-thought step for all P0 and P1 results
- Store reasoning chain verbatim in GraphResult for audit trail

**`src/aegis/dashboard/app.py`**:
- Add `/api/training/status` endpoint
- Add `/api/calibration/curve` endpoint (for reliability diagram)
- Add `/api/chain-of-thought/{trend_id}` endpoint (see full 7-step reasoning)
- Add training status panel to dashboard frontend

### Verification Tests That Must Pass

All of the following tests must exist and pass (they are the proof that the fixes worked):

```python
# tests/unit/training/test_leakage_prevention.py

def test_no_future_data_in_training_features():
    """If this test passes, data leakage has been eliminated."""
    builder = DatasetBuilder()
    df = builder.build_training_features()
    for col in ['actual_roi_pct', 'pnl_usd', 'units_sold', 'resolution_status']:
        assert col not in df.columns, f"POST-TRADE column {col} found in training features!"
    
def test_feature_timestamps_predate_trade_entry():
    """Every feature must have been available BEFORE the trade was entered."""
    builder = DatasetBuilder()
    df = builder.build_training_features()
    assert (df['feature_captured_at'] < df['trade_entered_at']).all(), \
        "LEAKAGE: Some features were captured AFTER trade entry!"

def test_no_outcome_in_features():
    """The label must not appear as a feature."""
    builder = DatasetBuilder()
    X, y = builder.build_xy()
    # y is the label (profitable or not)
    # X must not contain information derivable from y
    corr_with_label = X.corrwith(pd.Series(y)).abs()
    suspiciously_correlated = corr_with_label[corr_with_label > 0.95]
    assert len(suspiciously_correlated) == 0, \
        f"LEAKAGE: Features suspiciously correlated with label: {suspiciously_correlated.index.tolist()}"
```

```python
# tests/unit/training/test_calibration.py

def test_calibrated_confidence_is_honest():
    """When we say 70% confident, we should win ~70% of the time."""
    calibrator = ConfidenceCalibrator()
    # Simulate 500 predictions with known outcomes
    predicted_probs = [0.7] * 500  # always say 70% confident
    actual_outcomes = [1] * 340 + [0] * 160  # 68% actual win rate
    calibrated = calibrator.calibrate(predicted_probs, actual_outcomes)
    ece = calibrator.expected_calibration_error(calibrated, actual_outcomes)
    assert ece < 0.10, f"Calibration is too poor: ECE={ece:.3f} (should be < 0.10)"

def test_confidence_correlates_with_outcome():
    """Higher confidence should correlate with higher win rate."""
    calibrator = ConfidenceCalibrator.load_from_db()
    recent_outcomes = OutcomeRecorder.fetch_last_n_outcomes(100)
    corr = calibrator.confidence_outcome_correlation(recent_outcomes)
    assert corr > 0.20, f"Confidence doesn't correlate with outcomes: r={corr:.3f}"
```

```python
# tests/unit/evolve/test_lin_ucb_learning.py

def test_lin_ucb_actually_learns():
    """Verify that LinUCB changes its arm preferences based on rewards."""
    policy = LinUCBPricingPolicy(n_arms=4, context_dim=5)
    initial_weights = policy.get_arm_weights(context=[0.5]*5).copy()
    
    # Simulate 20 rounds: arm 2 always gets reward 0.8, others get 0.2
    for _ in range(20):
        arm = policy.select_arm(context=[0.5]*5)
        reward = 0.8 if arm == 2 else 0.2
        policy.update(arm=arm, reward=reward, context=[0.5]*5)
    
    final_weights = policy.get_arm_weights(context=[0.5]*5)
    
    # Arm 2 should now be selected more frequently
    assert final_weights[2] > initial_weights[2], \
        "LinUCB should have increased probability of the rewarding arm!"
    
    # After enough rounds, arm 2 should be selected most often
    selections = [policy.select_arm(context=[0.5]*5) for _ in range(100)]
    arm_2_rate = selections.count(2) / 100
    assert arm_2_rate > 0.5, \
        f"LinUCB should prefer rewarding arm: arm 2 selected only {arm_2_rate:.0%} of the time"
```

```python
# tests/unit/fulfillment/test_real_supplier.py

def test_supplier_cost_is_not_fictional():
    """Unit cost must never be derived as expected_margin * 2."""
    engine = ExecutionEngine.create_for_testing()
    # Mock Printful to return a real product with real cost
    with mock_printful_catalog(returns_product=True, cost_inr=Decimal("245.00")):
        cost = asyncio.run(engine._get_verified_unit_cost("test-sku", "apparel", ["t-shirt", "cotton"]))
    assert cost == Decimal("245.00"), "Cost should be the real API value, not a derived constant"

def test_no_plan_created_without_real_supplier():
    """If no real supplier is found, no execution plan should be created."""
    engine = ExecutionEngine.create_for_testing()
    with mock_printful_catalog(returns_product=False), mock_cj_catalog(returns_product=False):
        result = asyncio.run(engine.execute_plan(
            trend_id="test-123", score=0.85, confidence=0.78,
            sku="mystery-product", unit_cost=None  # no verified cost
        ))
    assert result.status == "no_verified_supplier", \
        "Should not create a plan when no real supplier found"
```

---

## PHASE SEVEN: THE DASHBOARD UPGRADES

The dashboard must show you everything you need to know at a glance. Add these panels:

### New Dashboard Panels

**Panel: Training Status**
Shows: Last training date, current model version, AUC trend over time (chart), next scheduled training
Alert condition: AUC drops > 0.05 from peak → orange warning

**Panel: Calibration Reliability Diagram**
Shows: The actual reliability curve — "when we said X% confident, we won Y% of the time"
A perfect system is a diagonal line. You want to be close to that line.
Alert condition: ECE > 0.15 → red warning "CONFIDENCE SCORES UNRELIABLE"

**Panel: Chain-of-Thought Viewer**
Shows: For each recent P0/P1 decision, the 7-step reasoning chain in plain English
You can click any decision and read exactly why AEGIS made it

**Panel: Supplier Verification Status**
Shows: What % of plans had a verified real supplier vs were blocked (should be 0% unverified)
Alert condition: Any plan created without verified supplier → CRITICAL alert

**Panel: LinUCB Arm Performance**
Shows: Which pricing strategies are winning (arm weights over time as a line chart)
You can see the system learning which pricing approach works best for each category

**Panel: Dataset Health**
Shows: How many settled outcomes available for next training, when next auto-train will fire
"You have 73 outcomes. Need 100 to trigger auto-training. ETA: ~8 days at current velocity."

---

## PHASE EIGHT: THE AUTONOMOUS OPERATIONS GUIDE

### What "Fully Autonomous" Means

When everything is working, here is AEGIS's daily schedule without you doing anything:

```
00:00 UTC (5:30 AM IST) — Nightly Settlement
  SettlementManager runs. All yesterday's orders marked settled.
  Outcomes recorded to database.
  LinUCB policy updated with real P&L.
  Calibration re-checked.
  
02:00 UTC (7:30 AM IST) — Self-Evolution Check
  DriftDetector runs. If drift > threshold, auto-rollback.
  If 100+ new outcomes available AND it's Sunday: trigger retraining.
  DataLake daily refresh runs (Bronze → Silver → Gold).
  
06:00 UTC (11:30 AM IST) — Morning Scrape
  Full swarm run across all 35+ adapters.
  UCB1 allocator decides which adapters get more budget.
  Top trends scored. P0/P1 alerts generated.
  Chain-of-thought reasoning runs on top 5 candidates.
  Telegram notifications sent for human-approval items.
  
12:00 UTC (5:30 PM IST) — Afternoon Scrape  
  Repeat of morning scrape.
  Focus on real-time price movements (NSE/BSE, Flipkart flash sales).

18:00 UTC (11:30 PM IST) — Evening Review
  System health check runs.
  Dashboard metrics refreshed.
  Tomorrow's resource allocation planned (UCB1 weights).
  
Continuously (every 5 minutes):
  AegisHealthChecker monitors all 7 health signals.
  Auto-heals where possible (restarts stuck scraper, re-queues failed alerts).
  Sends Telegram alert if anything needs human attention.
```

### Your Only Jobs

1. **Approve P0 trades via Telegram** (takes 10 seconds each, < 5 per week)
2. **Review the weekly report** (5 minutes every Sunday)
3. **Read any urgent Telegram alerts** (only fires when something actually needs you)
4. **Run `aegis train auto` once a month** if it hasn't auto-trained (it usually does automatically)

### When to Intervene (And Exactly What to Type)

```bash
# If you got a "DRIFT DETECTED" alert
aegis evolve drift          # see what drifted
aegis evolve retrain        # retrain the model
aegis killswitch arm        # re-enable trading after you're satisfied

# If win rate has been below 45% for a week
aegis report weekly         # read the full report
aegis train auto            # rebuild everything from scratch

# If you got "SUPPLIER_FAIL_RATE_HIGH"  
aegis fulfillment status    # see which suppliers are failing
aegis geo analyze           # find alternative suppliers/routes

# If the dashboard shows nothing
aegis status                # diagnose what's wrong
aegis up                    # restart all services

# If you want to understand why AEGIS made a specific decision
aegis decision explain <trend_id>   # shows the full 7-step chain-of-thought

# Nuclear option: pause everything, investigate, restart
aegis killswitch trip --reason "manual review"
# ... investigate ...
aegis killswitch arm --reason "investigation complete"
```

---

## PHASE NINE: THE MONEY FLOW (HOW AEGIS ACTUALLY MAKES MONEY)

Let me explain in plain English exactly how money flows through the system. This is important because you need to understand what to check if it stops working.

### Step 1: Signal Detection (Automatic)
AEGIS scrapes 35+ sources every 6 hours. It sees: "Searches for 'jute macrame wall hanging' on Flipkart jumped 340% this week. 7 Reddit posts, 3 YouTube videos, and 2 Amazon listings all appeared in the last 48 hours."

### Step 2: Opportunity Scoring (Automatic)
The trained model says: "This looks like 68% probability of profitable trend based on historical patterns. Cross-platform spread suggests organic (manipulation score: 0.08 — very clean)."

### Step 3: Chain-of-Thought Verification (Automatic, <2 seconds)
The system runs all 7 verification steps:
- ✅ Signal is organic (not manipulated)
- ✅ Actual sales ARE increasing on Flipkart for this category (verified via live scrape)
- ✅ Competitor count: 47 (low — good opportunity)
- ✅ CJDropshipping has jute wall hangings at ₹280/unit
- ✅ Can list on Flipkart at ₹749, margin after all costs: ₹187/unit (28.2% — excellent)
- ✅ No compliance issues (jute products, no FSSAI/BIS required)
- ✅ Kelly sizing: 0.25 × (0.68 × 1.68 - 0.32) / 1.68 = 0.148 = buy 4 units

### Step 4: Human Approval (Telegram, optional for P1)
You get a message: "🟢 P1 OPPORTUNITY: Jute Macrame Wall Hanging. Buy 4 units @ ₹280 each (₹1,120 total). List @ ₹749 on Flipkart. Net profit: ₹187/unit, ₹748 total. Confidence: 68% (calibrated, ECE=0.07). [APPROVE] [IGNORE]"

You tap Approve (or after 30 min, it auto-approves P1).

### Step 5: Execution (Automatic after approval)
- CJDropshipping order created for 4 units, delivered to your address or fulfillment center
- Flipkart listing created via Flipkart Seller API
- Order tracking started

### Step 6: Settlement (Automatic, at end of day)
- 3 of 4 units sold for ₹749 each = ₹2,247 revenue
- Costs: 4 × ₹280 supplier + ₹240 Flipkart fees + ₹180 shipping = ₹700
- Net profit: ₹2,247 - ₹700 = ₹1,547 (real money in your account)
- AEGIS records this outcome: "jute macrame trend, 75% win rate this batch, 28.1% margin"

### Step 7: Learning (Automatic, nightly)
The outcome is stored. The model notes: "Jute + macrame + Flipkart + low competitor count → profitable. Store this pattern." Over time, the system gets better at spotting exactly these kinds of opportunities.

---

## FINAL VERIFICATION CHECKLIST

After implementing everything above, run these checks. They prove the system is real.

```bash
# 1. Verify no data leakage
aegis train dataset build
aegis train leakage-check
# Expected: "✅ No data leakage detected in training features"

# 2. Verify real model (not LR masquerading as neural)
aegis train run
aegis train evaluate
# Expected: Shows XGBoost + LightGBM ensemble, real AUC on non-leaked holdout

# 3. Verify LinUCB is learning
aegis evolve policy history --days 30
# Expected: Arm weights should have CHANGED over 30 days (not stuck at 0.25/0.25/0.25/0.25)

# 4. Verify real supplier verification
aegis fulfillment test --sku "TEST-001" --category "apparel"
# Expected: Shows real Printful API response with real cost, NOT "TBD"

# 5. Verify calibration is real
aegis evolve calibration check
# Expected: Shows reliability diagram, ECE < 0.15

# 6. Verify drift detection is real
aegis evolve drift check
# Expected: Shows drift score vs REAL champion training baseline (not zeros/ones)

# 7. Run a full end-to-end test in advisory mode
aegis topic "jute home decor" --dry-run
# Expected: Full 7-step chain-of-thought output, real margin calculation, real supplier cost

# 8. Verify the system runs unattended for 24 hours
aegis autonomous run --hours 24 --dry-run
# Expected: Runs 24 hours, makes decisions, learns, zero crashes
```

---

## THE NORTH STAR VISION (WHERE THIS IS GOING)

In 6 months, AEGIS Pulse should be:

**Month 1**: Fixed. No more theater. Real models, real suppliers, real confidence scores. Running in advisory mode, generating alerts you manually approve.

**Month 2**: Profitable. First real trades placed. Win rate > 55%. You're making ₹5,000–₹15,000/month part-time with < 30 min of your time per week.

**Month 3**: Self-improving. Model has been retrained 2–3 times on real outcomes. Win rate climbing. You can see the reliability diagram improving over time.

**Month 4**: Expanding. Add new product categories. Geographic expansion (Delhi → Mumbai → Bangalore supply chains). Start exploring B2B (IndiaMart bulk orders).

**Month 5**: Scaling. Throughput increases. More capital deployed. System handles 50+ active opportunities simultaneously.

**Month 6**: Autonomous. Less than 5 minutes of human attention per week. The system finds opportunities, verifies them, executes them, and learns from them — entirely on its own.

**Year 2**: The system has trained on 10,000+ real Indian market trades. Its models are calibrated to the specific dynamics of Indian e-commerce. No generic dataset from a university or Kaggle can match this. This is the moat.

---

## IMPLEMENTATION PRIORITY ORDER

If you can only implement things in sequence, do them in this order:

1. **FIX-1** (Remove leakage from retrain.py) — Day 1. Most critical. Everything else builds on honest training.
2. **FIX-3** (Real drift baseline) — Day 1. Goes with FIX-1.
3. **Dataset builder with temporal gates** — Day 2. Can't train well without this.
4. **Real model trainer (XGBoost + LightGBM)** — Day 2-3. The real brain.
5. **FIX-2** (LinUCB policy) — Day 3. Real learning.
6. **FIX-4** (Real supplier verification) — Day 4. Required before any live trading.
7. **Chain-of-thought reasoning engine** — Day 5. The explainability layer.
8. **Calibration pipeline** — Day 5-6. Honest confidence scores.
9. **Dashboard panels for training/calibration** — Day 7. So you can see it working.
10. **Full test suite for all fixes** — Day 7-8. Proof that everything is real.

---

## OUTPUT FORMAT (MANDATORY)

Return the following, in order, with zero truncation:

1. `docs/WHAT_WAS_BROKEN_AND_WHY.md` — Complete, plain-English diagnosis
2. `db/migrations/0013_feature_snapshots.sql` — Complete migration
3. `db/migrations/0014_lin_ucb_state.sql` — Complete migration
4. `db/migrations/0015_calibration_state.sql` — Complete migration
5. `src/aegis/training/__init__.py` — Complete
6. `src/aegis/training/dataset_builder.py` — Complete with temporal gate enforcement
7. `src/aegis/training/feature_store.py` — Complete with leakage detection
8. `src/aegis/training/label_generator.py` — Complete
9. `src/aegis/training/model_trainer.py` — Complete XGBoost + LightGBM ensemble
10. `src/aegis/training/calibrator.py` — Complete Platt scaling implementation
11. `src/aegis/training/evaluator.py` — Complete walk-forward backtest
12. `src/aegis/training/leakage_detector.py` — Complete leakage detection
13. `src/aegis/training/cli.py` — Complete `aegis train` commands
14. `src/aegis/training/reports.py` — Complete human-readable reports
15. `src/aegis/intelligence/chain_of_thought.py` — Complete 7-step reasoning engine
16. `src/aegis/intelligence/manipulation_detector.py` — Complete ML manipulation detection
17. `src/aegis/intelligence/market_verifier.py` — Complete real-time market verification
18. `src/aegis/intelligence/supplier_verifier.py` — Complete real supplier API verification
19. `src/aegis/evolve/lin_ucb_policy.py` — Complete LinUCB implementation (replaces fake REINFORCE)
20. `src/aegis/evolve/calibration.py` — Complete calibration pipeline
21. Modified `src/aegis/evolve/retrain.py` — Leakage removed, real ensemble
22. Modified `src/aegis/evolve/drift.py` — Real baseline from champion stats
23. Modified `src/aegis/evolve/rl_policy.py` — Replaced by LinUCB
24. Modified `src/aegis/fulfillment/printful.py` — Real API, no mocks
25. Modified `aegis-phase4/src/aegis/execute/engine.py` — Real cost verification
26. Modified `src/aegis/agents/runner.py` — Chain-of-thought integration
27. Modified `src/aegis/dashboard/app.py` — New panels
28. `tests/unit/training/test_leakage_prevention.py` — Complete tests
29. `tests/unit/training/test_calibration.py` — Complete tests
30. `tests/unit/evolve/test_lin_ucb_learning.py` — Complete tests
31. `tests/unit/fulfillment/test_real_supplier.py` — Complete tests
32. `docs/HOW_TO_TRAIN_AEGIS.md` — Complete beginner guide
33. `docs/HOW_TO_READ_PERFORMANCE.md` — Complete beginner performance guide
34. `docs/HOW_TO_INTERVENE.md` — Complete intervention guide

