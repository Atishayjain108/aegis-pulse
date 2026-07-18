"""Fractional Kelly position sizing (advisory only).

Given an expected margin per unit, a loss probability, and a capital budget,
returns a recommended unit count and dollar allocation.

This module NEVER executes. It returns numbers. Phase 6 may consume them.

Kelly formula (simplified for binary win/loss with skewed payoff):
    f* = (p * b - q) / b
where:
    p = probability of win
    q = 1 - p = probability of loss
    b = win_payoff / loss_payoff (win/loss odds ratio)

We then apply fractional Kelly (default 0.25×) for conservatism, cap at
`KELLY_MAX_POSITION_PCT` of capital, and never recommend negative units.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from aegis.execute.constants import (
    KELLY_CAPITAL_DEFAULT_USD,
    KELLY_FLOOR_UNITS,
    KELLY_FRACTION_DEFAULT,
    KELLY_MAX_POSITION_PCT,
)

_log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SizingResult:
    """Output of the Kelly advisor."""

    units: int
    capital_usd: float
    kelly_fraction_raw: float  # the unbounded Kelly f* (may be negative)
    kelly_fraction_used: float  # what we actually applied (clipped)
    rationale: str


def _kelly_fraction(*, p_win: float, win_payoff: float, loss_payoff: float) -> float:
    """Return raw Kelly fraction.

    win_payoff is positive (e.g. +$5). loss_payoff is positive (e.g. $3 lost).
    Both expressed as magnitudes; signs are implicit.
    """
    if win_payoff <= 0 or loss_payoff <= 0:
        return 0.0
    p = max(0.0, min(1.0, float(p_win)))
    q = 1.0 - p
    b = win_payoff / loss_payoff
    return (p * b - q) / b


class KellyAdvisor:
    """Advisory position sizer.

    Constructor parameters are configuration; `advise()` is the hot-path.
    """

    __slots__ = ("_fraction", "_max_pct")

    def __init__(
        self,
        *,
        fraction: float = KELLY_FRACTION_DEFAULT,
        max_pct_of_capital: float = KELLY_MAX_POSITION_PCT,
    ) -> None:
        if not 0.0 < fraction <= 1.0:
            raise ValueError("fraction must be in (0, 1]")
        if not 0.0 < max_pct_of_capital <= 1.0:
            raise ValueError("max_pct_of_capital must be in (0, 1]")
        self._fraction = float(fraction)
        self._max_pct = float(max_pct_of_capital)

    def advise(
        self,
        *,
        expected_margin_usd: float | None,
        loss_probability: float | None,
        unit_cost_usd: float | None,
        capital_usd: float = KELLY_CAPITAL_DEFAULT_USD,
    ) -> SizingResult:
        """Return a sizing recommendation.

        Behaviour:
          - If any of the inputs is None or non-positive, returns 0 units
            with an explanatory rationale.
          - Otherwise computes raw Kelly, applies the safety fraction,
            caps at max_pct_of_capital, and floors at 0.
        """
        if (
            expected_margin_usd is None
            or loss_probability is None
            or unit_cost_usd is None
        ):
            return SizingResult(
                units=KELLY_FLOOR_UNITS,
                capital_usd=0.0,
                kelly_fraction_raw=0.0,
                kelly_fraction_used=0.0,
                rationale="missing inputs (margin, loss prob, or unit cost)",
            )
        if unit_cost_usd <= 0 or capital_usd <= 0:
            return SizingResult(
                units=KELLY_FLOOR_UNITS,
                capital_usd=0.0,
                kelly_fraction_raw=0.0,
                kelly_fraction_used=0.0,
                rationale="non-positive unit_cost or capital",
            )

        win_payoff = max(0.0, float(expected_margin_usd))
        loss_payoff = float(unit_cost_usd)  # full unit cost assumed at loss
        p_win = 1.0 - max(0.0, min(1.0, float(loss_probability)))

        f_raw = _kelly_fraction(
            p_win=p_win, win_payoff=win_payoff, loss_payoff=loss_payoff
        )
        f_safe = self._fraction * max(0.0, f_raw)
        f_used = min(f_safe, self._max_pct)

        target_capital = capital_usd * f_used
        units = max(KELLY_FLOOR_UNITS, int(target_capital // unit_cost_usd))
        actual_capital = units * unit_cost_usd

        rationale = (
            f"kelly_raw={f_raw:.4f} fraction={self._fraction:.2f} "
            f"capped={f_used:.4f} unit_cost={unit_cost_usd:.2f}"
        )
        _log.debug(
            "execute.sizing.advise",
            units=units,
            capital_usd=round(actual_capital, 2),
            kelly_raw=round(f_raw, 4),
            kelly_used=round(f_used, 4),
        )
        return SizingResult(
            units=units,
            capital_usd=actual_capital,
            kelly_fraction_raw=f_raw,
            kelly_fraction_used=f_used,
            rationale=rationale,
        )


__all__ = ["KellyAdvisor", "SizingResult"]
