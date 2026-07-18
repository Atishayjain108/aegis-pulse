"""Buyer-demand signal for SCOUT — Google Trends interest, not media chatter.

SCOUT's velocity features measure how fast *people are talking about* a trend
(news/RSS/social arrival rate). That is supply-side noise: a topic can be loud
in headlines while nobody is actually trying to buy it. This helper reads the
one keyless buyer-intent source we already have — Google Trends "interest over
time" for the candidate's title — and distils it into a single demand factor in
[0, 1] that combines *current search interest* with its *recent slope*.

Design rules (deliberately conservative, hot-path safe):

  * Fail-open: ANY failure (pytrends missing, 429, timeout, flat series) returns
    ``None`` → SCOUT applies no adjustment. Demand can only ever *reward* a real
    buyer signal; its absence never lowers the deterministic heuristic floor.
  * Time-boxed: a hard timeout caps the cost of a slow/blocked Trends call so it
    can never blow the agent latency budget.
  * Cached: results are memoised per keyword (TTL) because Google Trends is rate
    limited (~0.2 req/s) and re-querying the same trend within a pipeline run is
    wasteful.
  * Opt-out: set ``AEGIS_DISABLE_TRENDS=1`` (and it is auto-disabled under
    ``AEGIS_ENV=test``) so unit tests and offline runs never touch the network.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import structlog

_log = structlog.get_logger("aegis.agents.nodes.demand")

# How long a demand read stays fresh. Google Trends is daily-granularity data —
# a buyer-interest read is "fine if it's a few hours old" (TASK 1 quality bar).
_TTL_S = 6 * 3600.0
# Hard ceiling on a single Trends fetch. pytrends is synchronous + rate limited;
# this bounds its worst-case contribution to SCOUT latency.
_TIMEOUT_S = 4.0

# keyword(lower) -> (expires_at, factor_or_None)
_CACHE: dict[str, tuple[float, float | None]] = {}


def _disabled() -> bool:
    if os.getenv("AEGIS_DISABLE_TRENDS", "").strip() in {"1", "true", "True"}:
        return True
    return os.getenv("AEGIS_ENV", "").strip().lower() == "test"


def _interest_series(keyword: str) -> list[float] | None:
    """Synchronous pytrends fetch — runs in a thread. Returns the raw 0-100 series."""
    try:
        from pytrends.request import TrendReq  # type: ignore[import-untyped]
    except Exception:
        return None
    try:
        pt = TrendReq(hl="en-US", tz=330, timeout=(3, 3))
        pt.build_payload([keyword[:80]], timeframe="today 3-m")
        df = pt.interest_over_time()
    except Exception:
        return None
    if df is None or getattr(df, "empty", True):
        return None
    col = keyword[:80]
    try:
        return [float(v) for v in df[col].tolist()]
    except Exception:
        return None


def _factor_from_series(series: list[float]) -> float | None:
    """Fold a 0-100 interest series into a [0,1] demand factor.

    factor = 0.6 * current_interest + 0.4 * rising
      * current_interest = mean of the last quarter of the series / 100.
      * rising = OLS slope over the window, normalised so a clearly upward trend
        approaches 1 and a flat/declining trend approaches 0.
    Returns ``None`` for a degenerate (all-zero / empty) series so SCOUT skips it.
    """
    if len(series) < 4 or max(series) <= 0:
        return None
    tail = series[max(1, len(series) // 4) * -1:]
    level = (sum(tail) / len(tail)) / 100.0
    # OLS slope (units of interest per bucket) over the window.
    n = len(series)
    xs = list(range(n))
    mean_x = (n - 1) / 2.0
    mean_y = sum(series) / n
    denom = sum((xi - mean_x) ** 2 for xi in xs)
    slope = (
        sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(xs, series, strict=False)) / denom
        if denom > 1e-12
        else 0.0
    )
    # Normalise slope: ~+2 units/bucket of interest over the window reads as a
    # strong uptrend (→1); flat or negative reads as 0.
    rising = max(0.0, min(1.0, slope / 2.0))
    return round(max(0.0, min(1.0, 0.6 * level + 0.4 * rising)), 4)


async def demand_factor(keyword: str) -> float | None:
    """Return a [0,1] buyer-demand factor for *keyword*, or ``None`` if unknown.

    Never raises. ``None`` means "no usable demand read" → SCOUT applies no
    adjustment (demand is a reward-only signal).
    """
    kw = (keyword or "").strip().lower()
    if not kw or _disabled():
        return None

    now = time.monotonic()
    cached = _CACHE.get(kw)
    if cached and cached[0] > now:
        return cached[1]

    try:
        series = await asyncio.wait_for(asyncio.to_thread(_interest_series, kw), timeout=_TIMEOUT_S)
    except TimeoutError:
        _log.debug("demand.trends_timeout", keyword=kw)
        series = None
    except Exception as exc:
        _log.debug("demand.trends_failed", keyword=kw, error=type(exc).__name__)
        series = None

    factor = _factor_from_series(series) if series else None
    _CACHE[kw] = (now + _TTL_S, factor)
    return factor


async def stamp_demand(candidate: Any) -> Any:
    """Fetch buyer-demand ONCE at harvest and stamp it onto the candidate.

    Writes ``candidate.metadata["buyer_demand"]`` (a float in [0,1], or ``None``
    when Trends yields no usable read). SCOUT then reads this stamped value
    instead of re-querying Google Trends per node — which avoids the 429 storm
    that made the demand nudge intermittently disappear. Never raises; returns
    the same candidate for chaining. Idempotent: a candidate already carrying a
    ``buyer_demand`` key is left untouched.
    """
    try:
        meta = getattr(candidate, "metadata", None)
        if not isinstance(meta, dict) or "buyer_demand" in meta:
            return candidate
        title = getattr(candidate, "title", "") or ""
        meta["buyer_demand"] = await demand_factor(title)
    except Exception as exc:  # pragma: no cover - defensive, harvest must not fail
        _log.debug("demand.stamp_skipped", reason=type(exc).__name__)
    return candidate
