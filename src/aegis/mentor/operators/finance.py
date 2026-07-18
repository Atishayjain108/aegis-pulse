"""FinanceOp — unit economics, PnL, capital fit, runway (grounded arithmetic).

The finance operator answers the money questions deterministically from grounded
inputs — a verified unit cost, a sale price, fixed costs, expected volume, and the
user's own capital from their :class:`UserProfile`:

* **Unit economics** — contribution margin per unit, gross margin %.
* **Break-even** — units needed to cover fixed costs.
* **PnL** — expected profit at the planned volume.
* **Capital fit** — does the user's capital cover the upfront outlay?
* **Runway** — months of survival at the stated burn.

Grounded-or-silent: without a real cost AND price there is no unit economics to
report — it stays silent rather than invent numbers. Pure analysis: FinanceOp
moves no capital and triggers no execution.

Inputs arrive via ``context.extras["finance_inputs"]`` (verified cost from
SupplierOp, price from OpsOp, etc.) and ``profile.capital_usd``. No LLM, no
network — everything is deterministic arithmetic on the numbers it is handed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from aegis.mentor.operators.base import OperatorContext, OperatorResult

if TYPE_CHECKING:
    from aegis.mentor.schemas import UserProfile

_log = structlog.get_logger("aegis.mentor.operators.finance")

# Profile capital is INR-denominated (India default). PnL inputs are USD.
_INR_PER_USD_FALLBACK = 83.0


class FinanceOp:
    """Unit-economics / PnL / capital-fit / runway operator."""

    name = "finance"

    def __init__(self, *, inr_per_usd: float | None = None) -> None:
        self._inr_per_usd = inr_per_usd or _INR_PER_USD_FALLBACK

    async def run(
        self,
        profile: UserProfile,
        request: str,
        context: OperatorContext,
    ) -> OperatorResult:
        inputs = dict(context.extras.get("finance_inputs", {}) or {})
        unit_cost = self._as_float(inputs.get("unit_cost_usd"))
        unit_price = self._as_float(inputs.get("unit_price_usd"))

        # Grounded-or-silent: unit economics needs a real cost AND price.
        if unit_cost is None or unit_price is None or unit_cost <= 0 or unit_price <= 0:
            return OperatorResult(
                operator=self.name,
                reasoning=(
                    "No grounded unit cost + price available — cannot compute unit "
                    "economics without real numbers. Get a verified supplier cost and "
                    "a price (OpsOp) first. (Grounded-or-silent.)"
                ),
                actions=[
                    "Provide a verified unit cost and a sale price to model the "
                    "unit economics and capital fit."
                ],
                confidence=0.0,
                data={"capital_moved": False},
            )

        findings: list[str] = []
        sources: set[str] = {"finance:unit_economics"}

        margin_per_unit = unit_price - unit_cost
        margin_pct = round((margin_per_unit / unit_price) * 100, 1)
        findings.append(
            f"Unit economics: ${unit_price:g} price − ${unit_cost:g} cost = "
            f"${round(margin_per_unit, 4):g} contribution/unit ({margin_pct}% margin)."
        )

        fixed_costs = self._as_float(inputs.get("fixed_costs_usd")) or 0.0
        expected_units = int(inputs.get("expected_units", 0) or 0)

        breakeven = self._breakeven(fixed_costs, margin_per_unit, findings)
        pnl = self._pnl(margin_per_unit, expected_units, fixed_costs, findings)
        capital_fit = self._capital_fit(profile, unit_cost, expected_units, findings, sources)
        runway = self._runway(profile, inputs, findings)

        # Sector margin gate (grounded benchmark).
        below_floor = self._margin_gate(context, margin_pct, findings, sources)

        actions = self._actions(capital_fit, below_floor)

        confidence = self._confidence(breakeven, pnl, capital_fit)
        reasoning = (
            f"Unit economics modeled for '{request or profile.sector}': "
            f"{margin_pct}% margin, "
            f"{'capital fits' if capital_fit.get('fits') else 'capital gap'}"
            f"{', below sector floor' if below_floor else ''}. "
            "Pure analysis — no capital moved."
        )

        return OperatorResult(
            operator=self.name,
            findings=findings,
            actions=actions,
            sources=sorted(sources),
            reasoning=reasoning,
            confidence=confidence,
            data={
                "margin_per_unit_usd": round(margin_per_unit, 4),
                "margin_pct": margin_pct,
                "breakeven_units": breakeven,
                "pnl": pnl,
                "capital_fit": capital_fit,
                "runway_months": runway,
                "capital_moved": False,
            },
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _breakeven(
        fixed_costs: float, margin_per_unit: float, findings: list[str]
    ) -> int | None:
        if fixed_costs <= 0:
            return 0
        if margin_per_unit <= 0:
            findings.append(
                "Break-even: impossible at this price — contribution margin is "
                "≤ 0, so fixed costs are never recovered. Raise price or cut cost."
            )
            return None
        import math

        units = math.ceil(fixed_costs / margin_per_unit)
        findings.append(
            f"Break-even: {units} unit(s) to cover ${fixed_costs:g} fixed costs."
        )
        return units

    @staticmethod
    def _pnl(
        margin_per_unit: float,
        expected_units: int,
        fixed_costs: float,
        findings: list[str],
    ) -> dict[str, Any] | None:
        if expected_units <= 0:
            return None
        gross = margin_per_unit * expected_units
        net = gross - fixed_costs
        findings.append(
            f"Projected PnL at {expected_units} units: ${round(gross, 2):g} gross, "
            f"${round(net, 2):g} net after ${fixed_costs:g} fixed costs."
        )
        return {
            "expected_units": expected_units,
            "gross_profit_usd": round(gross, 2),
            "net_profit_usd": round(net, 2),
        }

    def _capital_fit(
        self,
        profile: UserProfile,
        unit_cost: float,
        expected_units: int,
        findings: list[str],
        sources: set[str],
    ) -> dict[str, Any]:
        capital_usd = self._profile_capital_usd(profile)
        upfront = unit_cost * max(expected_units, 0)
        fits = capital_usd >= upfront if expected_units > 0 else True
        if expected_units > 0:
            findings.append(
                f"Capital fit: ~${round(upfront, 2):g} upfront for {expected_units} "
                f"units vs ${round(capital_usd, 2):g} available "
                f"→ {'fits' if fits else 'SHORT by $' + f'{round(upfront - capital_usd, 2):g}'}."
            )
            sources.add("profile:capital")
        return {
            "available_usd": round(capital_usd, 2),
            "upfront_usd": round(upfront, 2),
            "fits": fits,
        }

    def _profile_capital_usd(self, profile: UserProfile) -> float:
        cap = float(getattr(profile, "capital_usd", 0.0) or 0.0)
        # ``capital_usd`` carries the user's capital in their currency; convert
        # from INR when the profile is INR-denominated (India default).
        if (getattr(profile, "currency", "USD") or "USD").upper() == "INR":
            return cap / self._inr_per_usd
        return cap

    def _runway(
        self, profile: UserProfile, inputs: dict[str, Any], findings: list[str]
    ) -> float | None:
        burn = self._as_float(inputs.get("monthly_burn_usd"))
        if burn is None or burn <= 0:
            return None
        capital_usd = self._profile_capital_usd(profile)
        months = round(capital_usd / burn, 1)
        findings.append(
            f"Runway: ~{months} month(s) at ${burn:g}/mo burn on "
            f"${round(capital_usd, 2):g} capital."
        )
        return months

    @staticmethod
    def _margin_gate(
        context: OperatorContext,
        margin_pct: float,
        findings: list[str],
        sources: set[str],
    ) -> bool:
        if context.sector_pack is None:
            return False
        try:
            floor = context.sector_pack.benchmarks().get("min_viable_gross_margin_pct")
        except Exception:
            return False
        if not isinstance(floor, int | float):
            return False
        floor_pct = round(floor * 100, 1)
        sources.add(f"sector_pack:{getattr(context.sector_pack, 'sector_tag', 'baseline')}")
        if margin_pct < floor_pct:
            findings.append(
                f"⚠ {margin_pct}% margin is below the sector's minimum viable "
                f"{floor_pct}% — the unit economics are fragile."
            )
            return True
        return False

    @staticmethod
    def _actions(capital_fit: dict[str, Any], below_floor: bool) -> list[str]:
        actions: list[str] = []
        if below_floor:
            actions.append(
                "Fix the margin before scaling: raise price, cut unit cost, or pick a "
                "higher-margin product — thin margins die on ad spend."
            )
        if not capital_fit.get("fits", True):
            actions.append(
                "Capital is short for the planned volume — start smaller (lower units) "
                "or use a no-MOQ/POD supplier to reduce upfront outlay."
            )
        actions.append(
            "This is analysis only — no capital is moved here; real orders stay gated."
        )
        return actions

    @staticmethod
    def _as_float(v: Any) -> float | None:
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _confidence(
        breakeven: int | None, pnl: dict[str, Any] | None, capital_fit: dict[str, Any]
    ) -> float:
        # Arithmetic on grounded inputs is reliable; confidence reflects how
        # complete the inputs were, never overclaimed.
        score = 0.4  # unit economics always computed at this point
        if breakeven is not None:
            score += 0.1
        if pnl is not None:
            score += 0.15
        if capital_fit.get("upfront_usd", 0):
            score += 0.1
        return round(min(0.75, score), 3)
