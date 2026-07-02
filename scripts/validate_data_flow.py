"""
AEGIS Pulse data flow validation.
Run: uv run python scripts/validate_data_flow.py
All 7 checks must pass before moving to Prompt 3.
"""
import asyncio
import sys
import time
from datetime import UTC, datetime

CHECKS = []
RESULTS = []

def check(name):
    def decorator(fn):
        CHECKS.append((name, fn))
        return fn
    return decorator

@check("1. DB connection and signals exist")
async def check_db():
    import asyncpg
    dsn = "postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis"
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2, timeout=5)
    count = await pool.fetchval("SELECT COUNT(*) FROM signals")
    fresh = await pool.fetchval(
        "SELECT COUNT(*) FROM signals WHERE created_at > NOW() - INTERVAL '24 hours'"
    )
    await pool.close()
    if count == 0:
        return False, "signals table is empty"
    return True, f"{count} total signals, {fresh} in last 24h"

@check("2. Redis connection and stream exists")
async def check_redis():
    import redis.asyncio as redis
    r = await redis.from_url("redis://localhost:6380/0")
    await r.ping()
    stream_len = await r.xlen("aegis:phase2:graph_results")
    await r.aclose()
    return True, f"Redis OK, stream has {stream_len} entries"

@check("3. scrape_topic returns signals")
async def check_scraper():
    from aegis.scrape.topic import scrape_topic
    result = await scrape_topic("ecommerce", limit_per_source=5, dry_run=True)
    if result.total_fetched == 0:
        return False, "0 signals fetched — scrapers broken"
    return True, f"{result.total_fetched} signals scraped (dry_run)"

@check("4. Agent pipeline runs with sentinel+compliance")
async def check_agents():
    import logging

    import structlog

    from aegis.agents.runner import run_trend
    from aegis.agents.schemas import TrendCandidate
    structlog.reset_defaults()
    logging.disable(logging.WARNING)
    tc = TrendCandidate(
        trend_id="validation-flow-001",
        title="Validation Test",
        signal_count=50, unique_authors=20,
        platforms=["hacker_news"],
        velocity_1h=2.0, velocity_6h=1.0, velocity_24h=0.5,
        sentiment=0.6, commercial_intent=0.7,
        novelty=0.5, coordination_risk=0.1,
    )
    t0 = time.perf_counter()
    result = await run_trend(tc, use_llm=False)
    ms = (time.perf_counter() - t0) * 1000
    logging.disable(logging.NOTSET)
    agents = [d.agent for d in result.decisions]
    if "sentinel" not in agents:
        return False, f"SENTINEL DID NOT RUN. Agents: {sorted(agents)}"
    if "compliance" not in agents:
        return False, f"COMPLIANCE DID NOT RUN. Agents: {sorted(agents)}"
    return True, f"verdict={result.final_verdict.value} score={result.final_score:.2f} in {ms:.0f}ms"

@check("5. Phase 3 prediction runs under 500ms")
async def check_predict():
    from datetime import datetime

    import numpy as np

    from aegis.predict.inference import InferenceRunner
    from aegis.predict.schemas import FeatureWindow
    runner = InferenceRunner()
    fw = FeatureWindow(
        trend_id="validation-predict-001",
        values=list(np.random.rand(20).tolist()),
        window_size=1, feature_dim=20,
        correlation_id="validation-predict-001",
        captured_at=datetime.now(UTC),
    )
    t0 = time.perf_counter()
    result = await runner.run(
        tenant_id="00000000-0000-0000-0000-000000000001",
        trend_id="validation-predict-001",
        window=fw,
    )
    ms = (time.perf_counter() - t0) * 1000
    preds = result.bundle.predictions if result.bundle else []
    if not preds:
        return False, "no predictions returned"
    if ms > 500:
        return False, f"latency {ms:.0f}ms exceeds 500ms SLA"
    p = preds[0]
    return True, f"action={p.action} confidence={p.confidence:.2f} in {ms:.0f}ms"

@check("6. Redis stream uses field name 'body'")
async def check_stream_field():
    import redis.asyncio as redis
    r = await redis.from_url("redis://localhost:6380/0")
    entries = await r.xrange("aegis:phase2:graph_results", count=1)
    await r.aclose()
    if not entries:
        return False, "stream is empty — run aegis analyze first"
    entry_id, fields = entries[0]
    has_body = b"body" in fields or "body" in fields
    if not has_body:
        return False, f"WRONG FIELD: found {list(fields.keys())} — must be 'body'"
    return True, "stream field is 'body'"

@check("7. Dashboard API responds")
async def check_dashboard():
    import httpx
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get("http://localhost:8300/healthz")
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        return True, "HTTP 200"

async def main():
    print("\n" + "="*60)
    print("  AEGIS PULSE — DATA FLOW VALIDATION")
    print(f"  {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("="*60)
    passed = failed = 0
    for name, fn in CHECKS:
        try:
            ok, detail = await fn()
            icon = "✅" if ok else "❌"
            print(f"\n{icon} {name}")
            print(f"   {detail}")
            if ok:
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"\n❌ {name}")
            print(f"   EXCEPTION: {str(e)[:150]}")
            failed += 1
    print("\n" + "="*60)
    print(f"  RESULT: {passed}/{passed+failed} checks passed")
    print("="*60 + "\n")
    sys.exit(0 if failed == 0 else 1)

if __name__ == "__main__":
    asyncio.run(main())
