"""
Tool: monte_carlo.

Runs N draws from log-normal cost / shipping / return-rate / ad-cost
priors and returns p10/p50/p90 of net margin per unit. Used by the
AUDITOR agent to size the Kelly fraction.

This is a deliberately simple model. It's *not* meant to be the
final pricing engine — that lives in Phase 6. The output here is
"is the EV positive enough to bother sourcing?" — a gate, not a
trade.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import random
import statistics
from dataclasses import asdict, dataclass

from .base import ToolResult, tool_call


@dataclass(frozen=True, slots=True)
class MarginPriors:
    sell_price: float = 30.0  # USD
    cost_mean: float = 8.0  # supplier cost
    cost_sigma: float = 0.4  # log-normal sigma
    shipping_mean: float = 4.0
    shipping_sigma: float = 0.3
    return_rate_mean: float = 0.05  # 5% expected
    return_rate_sigma: float = 0.4
    cac_mean: float = 6.0  # customer-acq cost (per converted sale)
    cac_sigma: float = 0.5
    platform_fee_pct: float = 0.10  # 10% take rate
    payment_fee_pct: float = 0.029  # 2.9% + $0.30 (the 0.30 absorbed in fixed)
    fixed_fee: float = 0.30


def _lognormal(mean: float, sigma: float, *, rng: random.Random) -> float:
    """Sample so that the *median* equals `mean`. (mu = ln(mean))"""
    if mean <= 0:
        return 0.0
    import math

    mu = math.log(mean)
    return float(rng.lognormvariate(mu, sigma))


@tool_call("monte_carlo_margin")
async def simulate(
    *,
    iterations: int = 5_000,
    sell_price: float | None = None,
    priors: MarginPriors | None = None,
    seed: int | None = None,
) -> ToolResult:
    """Run the simulation. Returns per-unit margin distribution stats."""
    p = priors or MarginPriors()
    if sell_price is not None and sell_price > 0:
        p = MarginPriors(**{**asdict(p), "sell_price": float(sell_price)})

    rng = random.Random(seed)

    iterations = max(100, min(int(iterations), 100_000))
    samples: list[float] = []
    losses = 0
    for _ in range(iterations):
        cost = _lognormal(p.cost_mean, p.cost_sigma, rng=rng)
        shipping = _lognormal(p.shipping_mean, p.shipping_sigma, rng=rng)
        ret_rate = max(0.0, min(0.5, _lognormal(p.return_rate_mean, p.return_rate_sigma, rng=rng)))
        cac = _lognormal(p.cac_mean, p.cac_sigma, rng=rng)

        gross = p.sell_price * (1.0 - ret_rate)
        platform = gross * p.platform_fee_pct
        payment = gross * p.payment_fee_pct + p.fixed_fee
        cogs = cost + shipping
        margin = gross - platform - payment - cogs - cac
        samples.append(margin)
        if margin < 0:
            losses += 1

    samples.sort()

    def _pct(p_: float) -> float:
        if not samples:
            return 0.0
        idx = int(p_ * (len(samples) - 1))
        return samples[idx]

    mean = statistics.fmean(samples) if samples else 0.0
    stdev = statistics.pstdev(samples) if len(samples) > 1 else 0.0

    return ToolResult.success(
        {
            "p10": round(_pct(0.10), 4),
            "p50": round(_pct(0.50), 4),
            "p90": round(_pct(0.90), 4),
            "mean": round(mean, 4),
            "stdev": round(stdev, 4),
            "loss_probability": round(losses / iterations, 4),
            "iterations": iterations,
            "sell_price": p.sell_price,
        }
    )
