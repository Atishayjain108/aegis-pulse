"""Vyapar execution bridge stub.

Maps a Phase 4 ``ComposerInput`` (ENTER verdict) to a Vyapar webhook
payload and dispatches it when ``AEGIS_EXECUTE_MODE=live`` and the
killswitch is armed.

Current status: **stub** — the HTTP dispatch is not yet wired.
The module is importable and the payload builder is exercised by tests.
To activate:
  1. Set ``AEGIS_VYAPAR_WEBHOOK_URL`` in your environment.
  2. Set ``AEGIS_EXECUTE_MODE=live`` (after confirming killswitch is armed).
  3. Replace ``_dispatch_stub`` with a real httpx POST.

Vyapar webhook reference:
  https://api.vyapar.in/docs/webhooks  (internal link)

Author: AEGIS Engineering
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from aegis.execute.bridge.types import ComposerInput
from aegis.execute.config import ExecuteSettings

_log = structlog.get_logger("aegis.execute.bridge.vyapar")


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------


def build_vyapar_payload(inp: ComposerInput) -> dict[str, Any]:
    """Convert a ComposerInput to a Vyapar draft-invoice webhook payload.

    Only ENTER verdicts are converted; all others return an empty dict.
    The caller is responsible for gating on verdict before calling this.

    Fields follow the Vyapar Intelligent Order API v1 schema:
      - ``party_name``  — buyer/seller display name (trend_id used as stub)
      - ``items``       — list of line items (single stub line for now)
      - ``total_amount``— estimated notional value (capital_budget_usd → INR)
      - ``notes``       — machine-readable JSON for the AEGIS audit trail
    """
    if inp.phase2_verdict != "ENTER":
        return {}

    notional_inr: float = 0.0
    if inp.capital_budget_usd is not None:
        notional_inr = round(inp.capital_budget_usd * 83.5, 2)  # stub exchange rate

    payload: dict[str, Any] = {
        "webhook_id": str(uuid.uuid4()),
        "source": "aegis-pulse",
        "action": "create_draft_order",
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "tenant_id": str(inp.tenant_id),
        "trend_id": inp.trend_id,
        "party_name": inp.trend_id,
        "items": [
            {
                "item_name": inp.phase2_title or inp.trend_id,
                "quantity": 1,
                "rate": notional_inr,
                "amount": notional_inr,
            }
        ],
        "total_amount": notional_inr,
        "notes": json.dumps(
            {
                "aegis_correlation_id": inp.correlation_id,
                "phase2_score": inp.phase2_score,
                "phase2_confidence": inp.phase2_confidence,
                "phase3_p_breakout_24h": inp.phase3_p_breakout_24h,
                "decision_window": inp.decision_window,
            },
            default=str,
        ),
    }
    return payload


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


async def maybe_dispatch(inp: ComposerInput) -> bool:
    """Send a Vyapar draft order when the system is in live execute mode.

    Returns True when the payload was dispatched, False when skipped
    (advisory mode, killswitch tripped, non-ENTER verdict, or missing URL).

    Does NOT raise — all errors are logged and swallowed so the main
    alert pipeline is never blocked by Vyapar connectivity issues.
    """
    cfg = ExecuteSettings()

    if cfg.mode != "live":
        _log.debug("vyapar.skipped_advisory", trend_id=inp.trend_id)
        return False

    if inp.phase2_verdict != "ENTER":
        _log.debug("vyapar.skipped_non_enter", trend_id=inp.trend_id, verdict=inp.phase2_verdict)
        return False

    webhook_url: str = cfg.vyapar_webhook_url
    if not webhook_url:
        _log.warning(
            "vyapar.skipped_no_url",
            trend_id=inp.trend_id,
            hint="Set AEGIS_VYAPAR_WEBHOOK_URL to enable Vyapar integration",
        )
        return False

    payload = build_vyapar_payload(inp)
    if not payload:
        return False

    try:
        await _dispatch_stub(webhook_url, payload)
        _log.info(
            "vyapar.dispatched",
            trend_id=inp.trend_id,
            webhook_id=payload.get("webhook_id"),
            total_amount_inr=payload.get("total_amount"),
        )
        return True
    except Exception as exc:
        _log.warning(
            "vyapar.dispatch_failed",
            trend_id=inp.trend_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return False


async def _dispatch_stub(url: str, payload: dict[str, Any]) -> None:
    """Placeholder HTTP POST — replace with real httpx call when wiring live.

    Raises ``NotImplementedError`` intentionally so integration tests catch
    any attempt to call this in a live environment without the real impl.
    """
    raise NotImplementedError(
        "Vyapar HTTP dispatch is not yet implemented. "
        "Replace _dispatch_stub with an httpx.AsyncClient POST. "
        f"Target URL: {url}"
    )


__all__ = ["build_vyapar_payload", "maybe_dispatch"]
