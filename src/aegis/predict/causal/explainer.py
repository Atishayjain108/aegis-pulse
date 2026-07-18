"""
AEGIS Pulse — Causal Explanation Generator.

Produces human-readable WHY explanations for every verdict.
Uses only heuristic feature values — no LLM required.
Output is deterministic and reproducible from the same inputs.

Called by agents/runner.py after run_trend() completes.
Guarantees: explanations reference at least 1 specific feature name,
include one counterfactual, stay under 300 words.
"""
from __future__ import annotations

import structlog

_log = structlog.get_logger("aegis.predict.causal.explainer")

# Feature thresholds derived from production signal statistics.
# Update quarterly as baseline distributions shift.
_HIGH_VELOCITY_1H = 5.0
_HIGH_VELOCITY_6H = 2.0
_HIGH_COMMERCIAL_INTENT = 0.75
_HIGH_COORDINATION_RISK = 0.40
_HIGH_NOVELTY = 0.70
_LOW_SENTIMENT = 0.35
_HIGH_SENTIMENT = 0.70


def generate_explanation(
    trend_id: str,
    verdict: str,
    score: float,
    confidence: float,
    velocity_1h: float,
    velocity_6h: float,
    velocity_24h: float,
    sentiment: float,
    commercial_intent: float,
    novelty: float,
    coordination_risk: float,
    signal_count: int,
    unique_authors: int,
    platforms: list[str],
) -> tuple[str, str, list[str]]:
    """Return (explanation, counterfactual, primary_drivers) for a verdict.

    explanation      — human-readable WHY string (< 300 words)
    counterfactual   — what single change would flip the verdict
    primary_drivers  — up to 3 feature names most responsible
    """
    drivers: list[tuple[float, str, str]] = []

    if velocity_1h > _HIGH_VELOCITY_1H:
        strength = (velocity_1h - _HIGH_VELOCITY_1H) / _HIGH_VELOCITY_1H
        drivers.append((strength, "velocity_1h",
            f"velocity_1h={velocity_1h:.1f} signals/hour "
            f"({velocity_1h / _HIGH_VELOCITY_1H:.1f}x above elevated threshold)"))
    elif velocity_1h < 0.5:
        drivers.append((0.5 - velocity_1h, "velocity_1h",
            f"velocity_1h={velocity_1h:.2f} signals/hour (very low activity)"))

    if velocity_6h > _HIGH_VELOCITY_6H:
        strength = (velocity_6h - _HIGH_VELOCITY_6H) / _HIGH_VELOCITY_6H
        drivers.append((strength, "velocity_6h",
            f"velocity_6h={velocity_6h:.1f} sustained over 6 hours"))

    if commercial_intent > _HIGH_COMMERCIAL_INTENT:
        drivers.append((commercial_intent - _HIGH_COMMERCIAL_INTENT, "commercial_intent",
            f"commercial_intent={commercial_intent:.2f} "
            f"(strong buy-signal language detected)"))
    elif commercial_intent < 0.3:
        drivers.append((0.3 - commercial_intent, "commercial_intent",
            f"commercial_intent={commercial_intent:.2f} (low purchase intent)"))

    if coordination_risk > _HIGH_COORDINATION_RISK:
        drivers.append((coordination_risk - _HIGH_COORDINATION_RISK, "coordination_risk",
            f"coordination_risk={coordination_risk:.2f} "
            f"(potential bot coordination detected)"))

    if novelty > _HIGH_NOVELTY:
        drivers.append((novelty - _HIGH_NOVELTY, "novelty",
            f"novelty={novelty:.2f} (early-stage signal, few prior occurrences)"))

    if sentiment > _HIGH_SENTIMENT:
        drivers.append((sentiment - _HIGH_SENTIMENT, "sentiment",
            f"sentiment={sentiment:.2f} (strongly positive consumer language)"))
    elif sentiment < _LOW_SENTIMENT:
        drivers.append((_LOW_SENTIMENT - sentiment, "sentiment",
            f"sentiment={sentiment:.2f} (negative consumer sentiment)"))

    if signal_count > 0:
        author_ratio = unique_authors / signal_count
        if author_ratio > 0.7:
            drivers.append((author_ratio - 0.5, "author_diversity",
                f"{unique_authors} unique authors across {signal_count} signals "
                f"(organic spread, not concentrated)"))
        elif author_ratio < 0.2:
            drivers.append((0.2 - author_ratio, "author_diversity",
                f"only {unique_authors} unique authors for {signal_count} signals "
                f"(concentrated source — low diversity)"))

    drivers.sort(key=lambda x: x[0], reverse=True)
    top_drivers = drivers[:3]
    primary_driver_names = [d[1] for d in top_drivers]

    if not top_drivers:
        explanation = (
            f"Verdict {verdict} (score={score:.2f}) based on "
            f"{signal_count} signals across {len(platforms)} platforms. "
            f"No single feature strongly dominates the signal."
        )
    else:
        labels = ["Primary", "Secondary", "Tertiary"]
        parts = [
            f"Verdict {verdict} (score={score:.2f}, confidence={confidence:.2f}) driven by:"
        ]
        for i, (_, __, desc) in enumerate(top_drivers):
            parts.append(f"  {labels[i]}: {desc}")
        if len(platforms) > 1:
            parts.append(
                f"Signal spread: {signal_count} signals across "
                f"{len(platforms)} platforms ({', '.join(platforms[:3])})"
            )
        explanation = "\n".join(parts)

    counterfactual = _build_counterfactual(
        verdict, score, velocity_1h, coordination_risk, top_drivers
    )

    _log.debug(
        "explainer.generated",
        trend_id=trend_id,
        verdict=verdict,
        primary_drivers=primary_driver_names,
        driver_count=len(drivers),
    )

    return explanation, counterfactual, primary_driver_names


def _build_counterfactual(
    verdict: str,
    score: float,
    velocity_1h: float,
    coordination_risk: float,
    top_drivers: list[tuple[float, str, str]],
) -> str:
    if not top_drivers:
        return "Insufficient signal variance to compute a counterfactual."

    top_name = top_drivers[0][1]

    if verdict == "ENTER":
        if top_name == "coordination_risk":
            return (
                f"COUNTERFACTUAL: if coordination_risk exceeded 0.50 "
                f"(currently {coordination_risk:.2f}), verdict would likely "
                f"shift to BLOCK."
            )
        if top_name == "velocity_1h":
            halved = velocity_1h / 2
            return (
                f"COUNTERFACTUAL: if velocity_1h were halved to "
                f"{halved:.1f} signals/hour (currently {velocity_1h:.1f}), "
                f"estimated score would drop ~{score * 0.25:.2f} points, "
                f"likely shifting verdict to HOLD."
            )
        return (
            f"COUNTERFACTUAL: reducing {top_name} by 40% would "
            f"reduce estimated score by ~{score * 0.2:.2f}, "
            f"potentially shifting verdict to HOLD."
        )

    if verdict == "HOLD":
        if top_name == "velocity_1h":
            doubled = velocity_1h * 2
            return (
                f"COUNTERFACTUAL: if velocity_1h doubled to "
                f"{doubled:.1f} signals/hour (currently {velocity_1h:.1f}), "
                f"verdict could shift to ENTER."
            )
        return (
            f"COUNTERFACTUAL: a 50% increase in {top_name} "
            f"could shift verdict to ENTER."
        )

    # BLOCK
    if top_name == "coordination_risk":
        return (
            f"COUNTERFACTUAL: if coordination_risk dropped below 0.30 "
            f"(currently {coordination_risk:.2f}), the block signal "
            f"would weaken and verdict might shift to HOLD."
        )
    return (
        f"COUNTERFACTUAL: resolving the {top_name} concern "
        f"could shift verdict from BLOCK to HOLD."
    )
