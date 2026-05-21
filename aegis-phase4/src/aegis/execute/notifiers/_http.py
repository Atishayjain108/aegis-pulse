"""Shared HTTP helpers for notifier implementations.

`HttpNotifierMixin` owns a single `httpx.AsyncClient` per notifier
instance. Lifecycle is bound to the notifier: `aclose()` shuts it down.

We deliberately do NOT share clients across notifiers — each channel
has different timeout / header / auth needs, and an isolated client
prevents pathological cases where one channel's stuck connection
backpressures another.

If `httpx` is not installed, the HTTP notifiers are still importable
but `enabled=False` and `send` returns a SKIPPED result. This keeps the
package usable in minimal environments.
"""

from __future__ import annotations

import time
from typing import Any, Final

import structlog

from aegis.execute.constants import NOTIFY_USER_AGENT
from aegis.execute.errors import (
    EXEC_NOTIFIER_HTTP_ERROR,
    EXEC_NOTIFIER_TIMEOUT,
)
from aegis.execute.schemas.alert import DeliveryStatus
from aegis.execute.schemas.notification import NotificationResult

_log = structlog.get_logger(__name__)

# Try to import httpx; degrade gracefully if absent.
try:
    import httpx  # type: ignore[import-not-found]
    _HAVE_HTTPX = True
except ImportError:  # pragma: no cover - exercised by environment
    httpx = None  # type: ignore[assignment]
    _HAVE_HTTPX = False


class HttpNotifierMixin:
    """Shared async HTTP plumbing for notifiers.

    Subclasses construct via `super().__init__(timeout_s=...)` and use
    `await self._post_json(url, body, ...)`.
    """

    __slots__ = ("_client", "_timeout_s")

    def __init__(self, *, timeout_s: float) -> None:
        self._timeout_s = float(timeout_s)
        if _HAVE_HTTPX:
            self._client: Any | None = httpx.AsyncClient(
                timeout=self._timeout_s,
                headers={"User-Agent": NOTIFY_USER_AGENT},
            )
        else:
            self._client = None

    @property
    def http_available(self) -> bool:
        return self._client is not None

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as exc:
                _log.warning("notifier.http_aclose_failed", error=str(exc))
            self._client = None

    async def _post_json(
        self,
        *,
        channel: str,
        url: str,
        json_body: dict[str, Any] | None = None,
        data_body: str | bytes | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        accept_204: bool = True,
    ) -> NotificationResult:
        """POST to `url`. Return a NotificationResult; never raise.

        `json_body` and `data_body` are mutually exclusive. If `data_body`
        is set, it's sent verbatim and the caller is responsible for
        setting Content-Type via `headers`.
        """
        if self._client is None:
            return NotificationResult(
                channel=channel,
                status=DeliveryStatus.SKIPPED,
                http_status=None,
                latency_ms=0.0,
                error_code="AEGIS-EXEC-9001",
                error_message="httpx not installed; channel skipped",
            )

        start = time.monotonic()
        try:
            kwargs: dict[str, Any] = {}
            if json_body is not None:
                kwargs["json"] = json_body
            if data_body is not None:
                kwargs["content"] = data_body
            if headers is not None:
                kwargs["headers"] = headers
            if params is not None:
                kwargs["params"] = params
            resp = await self._client.post(url, **kwargs)
        except Exception as exc:
            elapsed_ms = (time.monotonic() - start) * 1000.0
            # Distinguish timeout from other errors.
            is_timeout = _HAVE_HTTPX and isinstance(exc, httpx.TimeoutException)  # type: ignore[union-attr]
            if is_timeout:
                _log.warning(
                    "notifier.http_timeout",
                    channel=channel,
                    url=url,
                    elapsed_ms=elapsed_ms,
                )
                return NotificationResult(
                    channel=channel,
                    status=DeliveryStatus.TIMEOUT,
                    http_status=None,
                    latency_ms=elapsed_ms,
                    error_code=EXEC_NOTIFIER_TIMEOUT.code,
                    error_message=EXEC_NOTIFIER_TIMEOUT.message,
                )
            _log.warning(
                "notifier.http_failure",
                channel=channel,
                url=url,
                elapsed_ms=elapsed_ms,
                error=str(exc),
            )
            return NotificationResult(
                channel=channel,
                status=DeliveryStatus.FAILURE,
                http_status=None,
                latency_ms=elapsed_ms,
                error_code=EXEC_NOTIFIER_HTTP_ERROR.code,
                error_message=str(exc)[:1000],
            )

        elapsed_ms = (time.monotonic() - start) * 1000.0
        http_status = int(getattr(resp, "status_code", 0))
        ok = 200 <= http_status < 300 or (accept_204 and http_status == 204)
        if ok:
            return NotificationResult(
                channel=channel,
                status=DeliveryStatus.SUCCESS,
                http_status=http_status,
                latency_ms=elapsed_ms,
                error_code=None,
                error_message=None,
            )
        # Non-2xx — read body for debugging
        try:
            body_preview = resp.text[:500]
        except Exception:
            body_preview = "<unreadable>"
        _log.warning(
            "notifier.http_non2xx",
            channel=channel,
            url=url,
            http_status=http_status,
            body_preview=body_preview,
        )
        return NotificationResult(
            channel=channel,
            status=DeliveryStatus.FAILURE,
            http_status=http_status,
            latency_ms=elapsed_ms,
            error_code=EXEC_NOTIFIER_HTTP_ERROR.code,
            error_message=f"HTTP {http_status}: {body_preview}",
        )


__all__: Final = ["HttpNotifierMixin"]
