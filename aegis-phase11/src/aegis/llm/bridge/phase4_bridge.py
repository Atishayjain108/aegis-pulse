"""
aegis.llm.bridge.phase4_bridge — Phase 4 ↔ Phase 11 bridge
============================================================

Allows Phase 4 (Execution & Alert System) to use the Phase 11
``LLMGateway`` for:
  1. Alert message composition (human-readable Telegram/Discord alerts)
  2. Priority narration ("Why is this P0?")
  3. Stop-loss explanation generation

This bridge is import-safe: no Phase 4 imports here.

Author: AEGIS Engineering
"""

from __future__ import annotations

from typing import Any

import structlog

_log = structlog.get_logger("aegis.llm.bridge.phase4")

_ALERT_COMPOSE_PROMPT = """\
You are composing a concise, actionable market intelligence alert for a trader.

Trend: {trend_title}
Verdict: {verdict}
Score: {score:.2f}/1.00
Confidence: {confidence:.0%}
Priority: {priority}
Platforms: {platforms}
Velocity (1h): {velocity_1h:.1f} signals/hour

Write a single-paragraph alert (max 120 words) that:
1. Opens with the opportunity in plain language (no jargon)
2. States the key supporting data point
3. Gives a clear action recommendation
4. Notes the primary risk

Use active voice. Write as if texting a smart friend who trades.
Do NOT use bullet points. Do NOT include headers.
"""

_PRIORITY_NARRATION_PROMPT = """\
In 1 sentence (max 20 words), explain why this opportunity is priority {priority}:
Trend: {trend_title}, Score: {score:.2f}, Verdict: {verdict}
"""


async def compose_alert_message(
    trend_title: str,
    verdict: str,
    score: float,
    confidence: float,
    priority: str,
    platforms: list[str],
    velocity_1h: float,
) -> str:
    """
    Compose a human-readable alert message for Phase 4.

    Returns a concise, actionable alert string. Falls back to a
    structured template if the LLM is unavailable.

    Parameters
    ----------
    trend_title:
        Trend or product name.
    verdict:
        Execution verdict (ENTER / HOLD / BLOCK).
    score:
        Composite opportunity score [0, 1].
    confidence:
        Model confidence [0, 1].
    priority:
        Priority tier (P0–P3).
    platforms:
        List of platforms where signals were detected.
    velocity_1h:
        Signal velocity in signals/hour.

    Returns
    -------
    str
        Alert message text suitable for Telegram/Discord.
    """
    try:
        from aegis.llm.bridge.agents_bridge import get_gateway

        gw = await get_gateway()
        prompt = _ALERT_COMPOSE_PROMPT.format(
            trend_title=trend_title,
            verdict=verdict,
            score=score,
            confidence=confidence,
            priority=priority,
            platforms=", ".join(platforms),
            velocity_1h=velocity_1h,
        )
        response = await gw.complete(
            [{"role": "user", "content": prompt}],
            temperature=0.4,  # Slightly creative for natural language
            max_tokens=200,
        )
        return response.content.strip()

    except Exception as exc:  # noqa: BLE001
        _log.warning("phase4_bridge.compose_failed", error=str(exc))
        # Deterministic fallback alert
        emoji = {"ENTER": "🚀", "HOLD": "⏳", "BLOCK": "🚫"}.get(verdict, "📊")
        return (
            f"{emoji} [{priority}] {trend_title}\n"
            f"Verdict: {verdict} | Score: {score:.2f} | Confidence: {confidence:.0%}\n"
            f"Platforms: {', '.join(platforms)} | Velocity: {velocity_1h:.0f}/h"
        )


async def narrate_priority(
    trend_title: str,
    verdict: str,
    score: float,
    priority: str,
) -> str:
    """
    Generate a one-sentence priority justification for an alert.

    Parameters
    ----------
    trend_title:
        Trend name.
    verdict:
        Execution verdict.
    score:
        Composite score.
    priority:
        Priority tier.

    Returns
    -------
    str
        One-sentence justification or empty string on failure.
    """
    try:
        from aegis.llm.bridge.agents_bridge import get_gateway

        gw = await get_gateway()
        prompt = _PRIORITY_NARRATION_PROMPT.format(
            priority=priority,
            trend_title=trend_title,
            score=score,
            verdict=verdict,
        )
        response = await gw.complete(
            [{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=40,
        )
        return response.content.strip()

    except Exception as exc:  # noqa: BLE001
        _log.debug("phase4_bridge.narrate_failed", error=str(exc))
        return f"Score {score:.2f} with {verdict} verdict qualifies for {priority}."


def format_alert_for_telegram(
    alert_text: str,
    trend_title: str,
    verdict: str,
    priority: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    """
    Format an alert message with Telegram Markdown v2 formatting.

    Parameters
    ----------
    alert_text:
        Core alert text (from ``compose_alert_message``).
    trend_title:
        Trend name (for the header).
    verdict:
        Execution verdict.
    priority:
        Priority tier.
    metadata:
        Optional additional fields to append.

    Returns
    -------
    str
        Telegram-formatted message string.
    """
    verdict_emoji = {"ENTER": "🟢", "HOLD": "🟡", "BLOCK": "🔴"}.get(verdict, "⚪")
    priority_emoji = {"P0": "🔥", "P1": "⚡", "P2": "📊", "P3": "📝"}.get(priority, "📊")

    lines = [
        f"{verdict_emoji} *{trend_title}* {priority_emoji}",
        "",
        alert_text,
    ]

    if metadata:
        lines.append("")
        for key, value in list(metadata.items())[:4]:
            lines.append(f"_{key}_: `{value}`")

    return "\n".join(lines)


def format_alert_for_discord(
    alert_text: str,
    trend_title: str,
    verdict: str,
    priority: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Format an alert as a Discord embed dict.

    Parameters
    ----------
    alert_text:
        Core alert text.
    trend_title:
        Trend name.
    verdict:
        Execution verdict.
    priority:
        Priority tier.
    metadata:
        Optional fields for the embed footer.

    Returns
    -------
    dict
        Discord embed payload dict.
    """
    colour_map = {"ENTER": 0x00C851, "HOLD": 0xFFBB33, "BLOCK": 0xFF4444}
    colour = colour_map.get(verdict, 0x808080)

    embed: dict[str, Any] = {
        "title": f"{trend_title} — {verdict}",
        "description": alert_text,
        "color": colour,
        "fields": [],
    }

    if metadata:
        for key, value in list(metadata.items())[:5]:
            embed["fields"].append({"name": key, "value": str(value), "inline": True})

    embed["footer"] = {"text": f"Priority: {priority} | AEGIS Pulse"}
    return {"embeds": [embed]}
