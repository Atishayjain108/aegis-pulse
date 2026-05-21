"""Typed error codes for Phase 4.

Every Phase 4 failure mode is enumerated here. Codes are stable and
referenced by `docs/errors/AEGIS-EXEC-NNNN.md`. Every error is:
  - assigned a machine code
  - given a human message
  - flagged retryable (bool)
  - linked to a docs path

This module never imports anything from outside the standard library so it
can be imported from any layer (including the lowest-level transport code).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    """Static definition of a Phase 4 error code.

    Frozen + slots → cheap, hashable, immutable. These instances are module
    constants; never construct one at runtime in production code.
    """

    code: str
    message: str
    retryable: bool
    docs_path: str


# ---------------------------------------------------------------------------
# Composer (0001..0009)
# ---------------------------------------------------------------------------
EXEC_COMPOSER_BOTH_INPUTS_MISSING: Final = ErrorSpec(
    code="AEGIS-EXEC-0001",
    message="Composer received neither GraphResult nor InferenceResult.",
    retryable=False,
    docs_path="docs/errors/AEGIS-EXEC-0001.md",
)
EXEC_COMPOSER_TREND_ID_MISMATCH: Final = ErrorSpec(
    code="AEGIS-EXEC-0002",
    message="GraphResult.trend_id != InferenceResult.trend_id.",
    retryable=False,
    docs_path="docs/errors/AEGIS-EXEC-0002.md",
)
EXEC_COMPOSER_INVALID_VERDICT: Final = ErrorSpec(
    code="AEGIS-EXEC-0003",
    message="Composed verdict not in allowed set.",
    retryable=False,
    docs_path="docs/errors/AEGIS-EXEC-0003.md",
)

# ---------------------------------------------------------------------------
# Risk (0010..0019)
# ---------------------------------------------------------------------------
EXEC_RISK_MARGIN_BELOW_FLOOR: Final = ErrorSpec(
    code="AEGIS-EXEC-0010",
    message="Expected margin below floor; alert blocked.",
    retryable=False,
    docs_path="docs/errors/AEGIS-EXEC-0010.md",
)
EXEC_RISK_LOSS_PROB_TOO_HIGH: Final = ErrorSpec(
    code="AEGIS-EXEC-0011",
    message="Loss probability above ceiling; alert blocked.",
    retryable=False,
    docs_path="docs/errors/AEGIS-EXEC-0011.md",
)
EXEC_RISK_CONFIDENCE_TOO_LOW: Final = ErrorSpec(
    code="AEGIS-EXEC-0012",
    message="Overall confidence below floor; alert blocked.",
    retryable=False,
    docs_path="docs/errors/AEGIS-EXEC-0012.md",
)
EXEC_RISK_TENANT_BLOCKED: Final = ErrorSpec(
    code="AEGIS-EXEC-0013",
    message="Tenant is on the operator blocklist.",
    retryable=False,
    docs_path="docs/errors/AEGIS-EXEC-0013.md",
)

# ---------------------------------------------------------------------------
# Kill-switch (0020..0024)
# ---------------------------------------------------------------------------
EXEC_KILLSWITCH_TRIPPED: Final = ErrorSpec(
    code="AEGIS-EXEC-0020",
    message="Kill-switch is TRIPPED; dispatch halted.",
    retryable=True,  # operator may un-trip
    docs_path="docs/errors/AEGIS-EXEC-0020.md",
)
EXEC_KILLSWITCH_BACKEND_UNAVAILABLE: Final = ErrorSpec(
    code="AEGIS-EXEC-0021",
    message="Redis unreachable; kill-switch defaults to TRIPPED (fail-closed).",
    retryable=True,
    docs_path="docs/errors/AEGIS-EXEC-0021.md",
)

# ---------------------------------------------------------------------------
# Outbox / store (0025..0029)
# ---------------------------------------------------------------------------
EXEC_OUTBOX_INSERT_FAILED: Final = ErrorSpec(
    code="AEGIS-EXEC-0025",
    message="Failed to insert alert into outbox; durability not guaranteed.",
    retryable=True,
    docs_path="docs/errors/AEGIS-EXEC-0025.md",
)
EXEC_OUTBOX_NO_POOL: Final = ErrorSpec(
    code="AEGIS-EXEC-0026",
    message="Shared Postgres pool not initialized; call set_shared_pool() first.",
    retryable=False,
    docs_path="docs/errors/AEGIS-EXEC-0026.md",
)

# ---------------------------------------------------------------------------
# Notifiers (0030..0039)
# ---------------------------------------------------------------------------
EXEC_NOTIFIER_TIMEOUT: Final = ErrorSpec(
    code="AEGIS-EXEC-0030",
    message="Notifier channel exceeded per-call timeout budget.",
    retryable=True,
    docs_path="docs/errors/AEGIS-EXEC-0030.md",
)
EXEC_NOTIFIER_HTTP_ERROR: Final = ErrorSpec(
    code="AEGIS-EXEC-0031",
    message="Notifier received non-2xx HTTP response.",
    retryable=True,
    docs_path="docs/errors/AEGIS-EXEC-0031.md",
)
EXEC_NOTIFIER_DISABLED: Final = ErrorSpec(
    code="AEGIS-EXEC-0032",
    message="Notifier disabled due to missing configuration (non-fatal).",
    retryable=False,
    docs_path="docs/errors/AEGIS-EXEC-0032.md",
)
EXEC_NOTIFIER_HMAC_REQUIRED: Final = ErrorSpec(
    code="AEGIS-EXEC-0033",
    message="Webhook notifier requires HMAC key; channel disabled.",
    retryable=False,
    docs_path="docs/errors/AEGIS-EXEC-0033.md",
)


class AegisExecuteError(Exception):
    """Typed exception carrying an `ErrorSpec`.

    Always raise with the spec, not a free-form string:

        raise AegisExecuteError(EXEC_OUTBOX_INSERT_FAILED, cause=exc)
    """

    __slots__ = ("cause", "context", "spec")

    def __init__(
        self,
        spec: ErrorSpec,
        *,
        cause: BaseException | None = None,
        context: dict[str, object] | None = None,
    ) -> None:
        super().__init__(f"[{spec.code}] {spec.message}")
        self.spec = spec
        self.cause = cause
        self.context: dict[str, object] = dict(context) if context else {}


__all__ = [
    "EXEC_COMPOSER_BOTH_INPUTS_MISSING",
    "EXEC_COMPOSER_INVALID_VERDICT",
    "EXEC_COMPOSER_TREND_ID_MISMATCH",
    "EXEC_KILLSWITCH_BACKEND_UNAVAILABLE",
    "EXEC_KILLSWITCH_TRIPPED",
    "EXEC_NOTIFIER_DISABLED",
    "EXEC_NOTIFIER_HMAC_REQUIRED",
    "EXEC_NOTIFIER_HTTP_ERROR",
    "EXEC_NOTIFIER_TIMEOUT",
    "EXEC_OUTBOX_INSERT_FAILED",
    "EXEC_OUTBOX_NO_POOL",
    "EXEC_RISK_CONFIDENCE_TOO_LOW",
    "EXEC_RISK_LOSS_PROB_TOO_HIGH",
    "EXEC_RISK_MARGIN_BELOW_FLOOR",
    "EXEC_RISK_TENANT_BLOCKED",
    "AegisExecuteError",
    "ErrorSpec",
]
