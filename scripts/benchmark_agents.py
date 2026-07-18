"""
AEGIS Agent Pipeline Benchmark Script.

Measures single-request latency, verdict distribution, and agent coverage
across a range of TrendCandidate scenarios derived from real DB signal stats.

Architecture relationship:
  Exercises run_trend() end-to-end in heuristic mode (use_llm=False).
  Validates INVARIANT-1 (sentinel 100%) and INVARIANT compliance gate (100%)
  as SLA checks. Exits non-zero if any SLA check fails.

Usage:
    uv run python scripts/benchmark_agents.py [--scenarios N] [--output-json PATH]

SLA thresholds:
  p99 latency < 5000ms
  sentinel coverage = 100%
  compliance coverage = 100%
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Any

# Ensure aegis is importable when run from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ---------------------------------------------------------------------------
# Real DB stats (Step-0d / Step-4a, 2026-06-04)
# Used to build realistic velocity ranges for benchmark scenarios.
# ---------------------------------------------------------------------------
_REAL_STATS = {
    "avg_conf": 0.870,
    "total_24h": 198,
    "vel_1h": 106.0,
    "vel_6h": 106.0,
    "platforms": 5,
    # Per-platform from Step-4a query:
    "platform_counts": {
        "hacker_news": 80,
        "google_news": 39,
        "github_trending": 36,
        "reddit": 29,
        "bing_news": 14,
    },
    "platform_confs": {
        "hacker_news": 0.900,
        "google_news": 0.800,
        "github_trending": 0.900,
        "reddit": 0.900,
        "bing_news": 0.750,
    },
}

# Velocity range: 10th–90th percentile of realistic observed spread
# Spanning from near-zero (weak) to 3× real vel_1h (high-activity day)
_VEL_P10 = 0.5
_VEL_P90 = _REAL_STATS["vel_1h"] * 3.0  # 318.0

_PLATFORM_PAIRS = [
    ["hacker_news", "google_news"],
    ["reddit", "github_trending"],
    ["hacker_news", "reddit", "bing_news"],
    ["google_news", "bing_news"],
    ["hacker_news"],
]


def _build_scenarios(n: int) -> list[dict[str, Any]]:
    """Build N TrendCandidate kwargs spanning the real velocity range."""
    import math

    scenarios = []
    for i in range(n):
        t = i / max(n - 1, 1)  # 0.0 → 1.0

        # Log-spaced velocities so we cover weak AND strong signal space
        vel_1h = _VEL_P10 * math.exp(t * math.log(_VEL_P90 / _VEL_P10))
        vel_6h = vel_1h * 3.0
        vel_24h = vel_1h * 8.0

        # Vary commercial_intent and coordination_risk across scenarios
        commercial_intent = 0.3 + 0.6 * (i % 5) / 4.0
        coordination_risk = 0.02 + 0.25 * ((i * 3) % 7) / 6.0

        platforms = _PLATFORM_PAIRS[i % len(_PLATFORM_PAIRS)]
        sentiment = 0.1 + 0.7 * (i % 3) / 2.0 - 0.35  # maps to [-0.25, 0.35]

        scenarios.append(
            {
                "trend_id": f"bench-{i:04d}",
                "title": f"Benchmark scenario {i:04d} — vel={vel_1h:.1f}",
                "signal_count": max(1, int(_REAL_STATS["total_24h"] * t) + 1),
                "unique_authors": max(1, int(_REAL_STATS["total_24h"] * t / 5) + 1),
                "platforms": platforms,
                "velocity_1h": round(vel_1h, 2),
                "velocity_6h": round(vel_6h, 2),
                "velocity_24h": round(vel_24h, 2),
                "sentiment": round(max(-1.0, min(1.0, sentiment)), 3),
                "commercial_intent": round(commercial_intent, 3),
                "novelty": round(0.3 + 0.5 * (i % 4) / 3.0, 3),
                "coordination_risk": round(coordination_risk, 3),
            }
        )
    return scenarios


async def _reset_state() -> None:
    from aegis.agents import runner as runner_module
    from aegis.agents.llm import router as router_module

    await router_module.reset_default_router()
    await runner_module.reset_graph_cache()


async def _run_scenario(kwargs: dict[str, Any]) -> dict[str, Any]:
    from aegis.agents.runner import run_trend
    from aegis.agents.schemas import TrendCandidate

    tc = TrendCandidate(**kwargs)
    t0 = time.perf_counter()
    try:
        result = await run_trend(tc, use_llm=False)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        agents_ran = {d.agent for d in result.decisions}
        return {
            "trend_id": tc.trend_id,
            "ok": True,
            "verdict": result.final_verdict.value,
            "score": round(result.final_score, 4),
            "confidence": round(result.final_confidence, 4),
            "halt": result.halt_reason,
            "latency_ms": round(elapsed_ms, 1),
            "agents": sorted(agents_ran),
            "sentinel_ran": "sentinel" in agents_ran,
            "compliance_ran": "compliance" in agents_ran,
        }
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return {
            "trend_id": kwargs["trend_id"],
            "ok": False,
            "error": str(exc),
            "latency_ms": round(elapsed_ms, 1),
            "sentinel_ran": False,
            "compliance_ran": False,
        }


def _percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    data = sorted(data)
    idx = (len(data) - 1) * p / 100.0
    lo, hi = int(idx), min(int(idx) + 1, len(data) - 1)
    return data[lo] + (data[hi] - data[lo]) * (idx - lo)


def _bar(frac: float, width: int = 24) -> str:
    filled = int(frac * width)
    return "█" * filled + "░" * (width - filled)


async def main(n_scenarios: int, output_json: str | None) -> int:
    print("Initialising agent singletons…")
    await _reset_state()

    scenarios = _build_scenarios(n_scenarios)
    results: list[dict[str, Any]] = []

    print(f"Running {n_scenarios} scenarios sequentially (heuristic mode)…\n")
    for i, sc in enumerate(scenarios, 1):
        r = await _run_scenario(sc)
        results.append(r)
        status = "✓" if r["ok"] else "✗"
        verdict = r.get("verdict", "ERR")
        lat = r["latency_ms"]
        print(f"  [{i:3d}/{n_scenarios}] {status} {sc['trend_id']}  "
              f"vel={sc['velocity_1h']:>7.1f}  {verdict:<8}  {lat:>7.1f}ms")

    # ------------------------------------------------------------------
    # Aggregate metrics
    # ------------------------------------------------------------------
    ok_results = [r for r in results if r["ok"]]
    err_count = len(results) - len(ok_results)

    latencies = [r["latency_ms"] for r in ok_results]
    sentinel_count = sum(1 for r in ok_results if r["sentinel_ran"])
    compliance_count = sum(1 for r in ok_results if r["compliance_ran"])

    sentinel_pct = 100.0 * sentinel_count / max(len(ok_results), 1)
    compliance_pct = 100.0 * compliance_count / max(len(ok_results), 1)

    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)
    p99 = _percentile(latencies, 99)
    mean_lat = sum(latencies) / max(len(latencies), 1)
    max_lat = max(latencies) if latencies else 0.0

    # Verdict distribution
    verdict_counts: dict[str, int] = {}
    for r in ok_results:
        v = r.get("verdict", "unknown")
        verdict_counts[v] = verdict_counts.get(v, 0) + 1

    # Agent coverage
    all_agents = [
        "scout", "sentinel", "compliance", "historian",
        "sourcer", "auditor", "geo_arbitrage", "narrative",
        "red_team", "hedge",
    ]
    agent_coverage: dict[str, float] = {}
    for agent in all_agents:
        count = sum(1 for r in ok_results if agent in r.get("agents", []))
        agent_coverage[agent] = 100.0 * count / max(len(ok_results), 1)

    # SLA checks
    sla_p99 = p99 < 5000.0
    sla_sentinel = sentinel_pct >= 100.0
    sla_compliance = compliance_pct >= 100.0
    all_slas_pass = sla_p99 and sla_sentinel and sla_compliance

    # ------------------------------------------------------------------
    # Print formatted report
    # ------------------------------------------------------------------
    print()
    print("═" * 63)
    print(f"  AEGIS AGENT PIPELINE BENCHMARK  ({n_scenarios} scenarios, real data)")
    print("═" * 63)

    print(f"\n  Latency (ms)    {'p50':>8}  {'p95':>8}  {'p99':>8}  {'mean':>8}  {'max':>8}")
    print(f"                  {p50:>8.1f}  {p95:>8.1f}  {p99:>8.1f}  "
          f"{mean_lat:>8.1f}  {max_lat:>8.1f}")

    print("\n  Verdict distribution:")
    for v, cnt in sorted(verdict_counts.items(), key=lambda x: -x[1]):
        pct = 100.0 * cnt / max(len(ok_results), 1)
        print(f"    {v:<10} {cnt:>4}  ({pct:>5.1f}%)  {_bar(pct / 100)}")

    print("\n  Agent coverage (% of runs each agent executed):")
    mandatory = {"scout", "sentinel", "compliance"}
    for agent in all_agents:
        pct = agent_coverage[agent]
        tag = "  [MANDATORY]" if agent in mandatory else ""
        print(f"    {agent:<16} {pct:>6.1f}%{tag}")

    print("\n  SLA checks:")
    print(f"    p99 < 5000ms:     {'✅ PASS' if sla_p99 else '❌ FAIL':8}  "
          f"(p99={p99:.1f}ms)")
    print(f"    sentinel 100%:    {'✅ PASS' if sla_sentinel else '❌ FAIL':8}  "
          f"← CRITICAL  ({sentinel_pct:.1f}%)")
    print(f"    compliance 100%:  {'✅ PASS' if sla_compliance else '❌ FAIL':8}  "
          f"← CRITICAL  ({compliance_pct:.1f}%)")

    print(f"\n  Errors:  {err_count} / {n_scenarios}")
    print("═" * 63)

    # ------------------------------------------------------------------
    # Write JSON summary
    # ------------------------------------------------------------------
    summary = {
        "n_scenarios": n_scenarios,
        "errors": err_count,
        "latency_p50_ms": round(p50, 1),
        "latency_p95_ms": round(p95, 1),
        "latency_p99_ms": round(p99, 1),
        "latency_mean_ms": round(mean_lat, 1),
        "latency_max_ms": round(max_lat, 1),
        "verdict_distribution": verdict_counts,
        "agent_coverage_pct": {k: round(v, 1) for k, v in agent_coverage.items()},
        "sentinel_coverage_pct": round(sentinel_pct, 1),
        "compliance_coverage_pct": round(compliance_pct, 1),
        "sla_p99_pass": sla_p99,
        "sla_sentinel_pass": sla_sentinel,
        "sla_compliance_pass": sla_compliance,
        "all_slas_pass": all_slas_pass,
        "raw_results": results,
    }

    if output_json:
        with open(output_json, "w") as fh:
            json.dump(summary, fh, indent=2)
        print(f"\n  JSON summary written to: {output_json}")

    return 0 if all_slas_pass else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AEGIS Agent Pipeline Benchmark")
    parser.add_argument("--scenarios", type=int, default=30,
                        help="Number of benchmark scenarios (default: 30)")
    parser.add_argument("--output-json", metavar="PATH",
                        help="Write JSON summary to this file")
    args = parser.parse_args()

    exit_code = asyncio.run(main(args.scenarios, args.output_json))
    sys.exit(exit_code)
