"""Sentry error tracking initialisation for AEGIS services.

Usage:
    from aegis.observability.sentry_init import init_sentry

    init_sentry()   # reads SENTRY_DSN from environment; no-ops if unset

Environment variables:
    SENTRY_DSN         — Sentry project DSN (empty = disabled)
    SENTRY_ENV         — environment tag (default: value of AEGIS_ENV)
    SENTRY_RELEASE     — release tag for commit correlation (default: "unknown")
    SENTRY_SAMPLE_RATE — traces_sample_rate 0–1 (default: 0.1)
    SENTRY_PROFILE_RATE— profiles_sample_rate 0–1 (default: 0.1)

Graceful degradation: if ``sentry-sdk`` is absent or DSN is empty, init_sentry
is a no-op — the service never crashes because of missing Sentry config.
"""

from __future__ import annotations

import os

import structlog

_log = structlog.get_logger("aegis.observability.sentry")

SENTRY_DSN = os.getenv("SENTRY_DSN", "")
SENTRY_ENV = os.getenv("SENTRY_ENV", os.getenv("AEGIS_ENV", "dev"))
SENTRY_RELEASE = os.getenv("SENTRY_RELEASE", "unknown")
SENTRY_SAMPLE_RATE = float(os.getenv("SENTRY_SAMPLE_RATE", "0.1"))
SENTRY_PROFILE_RATE = float(os.getenv("SENTRY_PROFILE_RATE", "0.1"))

_sentry_initialised = False


def init_sentry() -> None:
    """Configure Sentry SDK with FastAPI, asyncpg, and httpx integrations.

    Safe to call multiple times — subsequent calls are no-ops.
    """
    global _sentry_initialised  # noqa: PLW0603

    if _sentry_initialised:
        return

    if not SENTRY_DSN:
        _log.info("sentry.disabled", reason="SENTRY_DSN not set")
        _sentry_initialised = True
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration

        integrations = [
            LoggingIntegration(level=None, event_level=None),
        ]

        try:
            from sentry_sdk.integrations.fastapi import (
                FastApiIntegration,  # type: ignore[import-not-found]
            )

            integrations.append(FastApiIntegration())
        except ImportError:
            pass

        try:
            from sentry_sdk.integrations.asyncpg import (
                AsyncPGIntegration,  # type: ignore[import-not-found]
            )

            integrations.append(AsyncPGIntegration())
        except ImportError:
            pass

        try:
            from sentry_sdk.integrations.httpx import (
                HttpxIntegration,  # type: ignore[import-not-found]
            )

            integrations.append(HttpxIntegration())
        except ImportError:
            pass

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            environment=SENTRY_ENV,
            release=SENTRY_RELEASE,
            integrations=integrations,
            traces_sample_rate=SENTRY_SAMPLE_RATE,
            profiles_sample_rate=SENTRY_PROFILE_RATE,
            send_default_pii=False,
        )
        _sentry_initialised = True
        _log.info(
            "sentry.initialised",
            environment=SENTRY_ENV,
            release=SENTRY_RELEASE,
            sample_rate=SENTRY_SAMPLE_RATE,
        )

    except Exception:  # pragma: no cover
        _log.warning("sentry.init_failed")
        _sentry_initialised = True


def reset_sentry_for_testing() -> None:
    """Reset initialisation flag so unit tests can call init_sentry again."""
    global _sentry_initialised  # noqa: PLW0603
    _sentry_initialised = False
