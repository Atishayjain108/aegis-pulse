"""Approval workflow for high-priority execution plans.

P0 and P1 plans (large capital at risk) require human sign-off before
execution. `ApprovalBroker` sends an interactive Telegram message with
inline buttons and awaits a callback from the operator within a configurable
timeout window.

Decision states:
  approved   — operator clicked ✅ or auto-approved (P0 + auto_execute_p0=True).
  rejected   — operator clicked ❌.
  escalated  — operator clicked 🔴 (creates a human-review ticket).
  timeout    — no response within `approval_timeout_s`.

The broker is fully optional: when no Telegram credentials are configured it
falls back to an advisory log-only path that returns `approved=False` so the
engine stays in dry-run territory.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Final, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field

_log = structlog.get_logger(__name__)

ApprovalDecision = Literal["approved", "rejected", "escalated", "timeout"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ApprovalRequest(BaseModel):
    """Parameters for a human-approval request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    plan_id: str
    trend_id: str
    quantity: int
    capital_usd: float
    estimated_profit_usd: float
    kelly_fraction: float
    risk_score: float
    created_at: datetime = Field(default_factory=_utc_now)


class ApprovalResult(BaseModel):
    """Outcome of an approval request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str
    plan_id: str
    decision: ApprovalDecision
    decided_at: datetime = Field(default_factory=_utc_now)
    decided_by: str = "system"


class ApprovalBroker:
    """Send and handle approval requests via Telegram.

    All async methods are safe to call concurrently. Each pending request
    is tracked by `request_id` and resolved via `handle_callback`.

    If `bot_token` or `chat_id` are empty, `request_approval` immediately
    returns a `timeout` result (no Telegram calls are made).
    """

    # No __slots__: service object, and tests need monkeypatch on _send_telegram.

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        timeout_s: int = 300,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._timeout_s = timeout_s
        # Maps request_id → asyncio.Event; set when callback arrives.
        self._pending: dict[str, tuple[asyncio.Event, ApprovalDecision | None]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._bot_token and self._chat_id)

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResult:
        """Send request and block until approved/rejected/timeout.

        Returns an `ApprovalResult` — never raises.
        """
        if not self.enabled:
            _log.warning(
                "execute.approval.no_credentials",
                plan_id=req.plan_id,
                fallback="timeout",
            )
            return ApprovalResult(
                request_id=req.request_id,
                plan_id=req.plan_id,
                decision="timeout",
            )

        sent = await self._send_telegram(req)
        if not sent:
            return ApprovalResult(
                request_id=req.request_id,
                plan_id=req.plan_id,
                decision="timeout",
            )

        # Register a slot for the callback.
        event: asyncio.Event = asyncio.Event()
        self._pending[req.request_id] = (event, None)

        try:
            await asyncio.wait_for(event.wait(), timeout=self._timeout_s)
        except TimeoutError:
            _log.warning(
                "execute.approval.timeout",
                request_id=req.request_id,
                plan_id=req.plan_id,
                timeout_s=self._timeout_s,
            )
            self._pending.pop(req.request_id, None)
            return ApprovalResult(
                request_id=req.request_id,
                plan_id=req.plan_id,
                decision="timeout",
            )

        _, decision = self._pending.pop(req.request_id, (None, None))
        resolved: ApprovalDecision = decision if decision is not None else "timeout"

        _log.info(
            "execute.approval.resolved",
            request_id=req.request_id,
            plan_id=req.plan_id,
            decision=resolved,
        )
        return ApprovalResult(
            request_id=req.request_id,
            plan_id=req.plan_id,
            decision=resolved,
        )

    async def handle_callback(
        self,
        request_id: str,
        decision: ApprovalDecision,
        decided_by: str = "operator",
    ) -> None:
        """Called by the Telegram webhook handler when an operator responds."""
        slot = self._pending.get(request_id)
        if slot is None:
            _log.warning(
                "execute.approval.unknown_callback",
                request_id=request_id,
                decision=decision,
            )
            return

        event, _ = slot
        self._pending[request_id] = (event, decision)
        event.set()

        _log.info(
            "execute.approval.callback_received",
            request_id=request_id,
            decision=decision,
            decided_by=decided_by,
        )

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    async def _send_telegram(self, req: ApprovalRequest) -> bool:
        """POST approval message to Telegram Bot API."""
        try:
            import httpx
        except ImportError:
            _log.error("execute.approval.httpx_missing")
            return False

        text = _build_message(req)
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve", "callback_data": f"approve:{req.request_id}"},
                    {"text": "❌ Reject", "callback_data": f"reject:{req.request_id}"},
                ],
                [
                    {"text": "🔴 Escalate", "callback_data": f"escalate:{req.request_id}"},
                ],
            ]
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
                    json={
                        "chat_id": self._chat_id,
                        "text": text,
                        "parse_mode": "MarkdownV2",
                        "disable_web_page_preview": True,
                        "reply_markup": keyboard,
                    },
                )
                if resp.status_code != 200:
                    _log.error(
                        "execute.approval.telegram_error",
                        status=resp.status_code,
                        plan_id=req.plan_id,
                    )
                    return False
                return True
        except Exception as exc:
            _log.error("execute.approval.send_failed", error=str(exc), plan_id=req.plan_id)
            return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MDV2_RESERVED: Final[str] = r"_*[]()~`>#+-=|{}.!"


def _esc(s: str) -> str:
    return "".join(f"\\{c}" if c in _MDV2_RESERVED else c for c in s)


def _build_message(req: ApprovalRequest) -> str:
    lines = [
        "🚀 *Execution Approval Required*",
        "",
        f"Trend: `{_esc(req.trend_id)}`",
        f"Qty: `{req.quantity}` units",
        f"Capital: `${req.capital_usd:.2f}`",
        f"Est\\. Profit: `${req.estimated_profit_usd:.2f}`",
        f"Kelly Fraction: `{req.kelly_fraction:.2%}`",
        f"Risk Score: `{req.risk_score:.2f}`",
        "",
        f"Request ID: `{_esc(req.request_id)}`",
    ]
    return "\n".join(lines)


__all__: Final = [
    "ApprovalBroker",
    "ApprovalDecision",
    "ApprovalRequest",
    "ApprovalResult",
]
