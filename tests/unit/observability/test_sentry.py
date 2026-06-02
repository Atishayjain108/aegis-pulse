"""Unit tests for aegis.observability.sentry_init."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import aegis.observability.sentry_init as s_mod
from aegis.observability.sentry_init import (
    init_sentry,
    reset_sentry_for_testing,
)


@pytest.fixture(autouse=True)
def _reset_sentry():
    """Ensure Sentry init state is reset between tests."""
    reset_sentry_for_testing()
    yield
    reset_sentry_for_testing()


class TestInitSentryDisabled:
    def test_noop_when_dsn_empty(self) -> None:
        """init_sentry must be a no-op and must not crash when SENTRY_DSN is empty."""
        original_dsn = s_mod.SENTRY_DSN
        s_mod.SENTRY_DSN = ""
        try:
            init_sentry()  # must not raise
        finally:
            s_mod.SENTRY_DSN = original_dsn

    def test_idempotent(self) -> None:
        """Calling init_sentry twice is safe."""
        original_dsn = s_mod.SENTRY_DSN
        s_mod.SENTRY_DSN = ""
        try:
            init_sentry()
            init_sentry()  # second call is no-op
        finally:
            s_mod.SENTRY_DSN = original_dsn


class TestInitSentryWithDsn:
    def test_calls_sentry_init_when_dsn_set(self) -> None:
        original_dsn = s_mod.SENTRY_DSN
        s_mod.SENTRY_DSN = "https://fake@sentry.io/123"
        try:
            mock_sdk = MagicMock()
            with patch.dict(
                "sys.modules",
                {
                    "sentry_sdk": mock_sdk,
                    "sentry_sdk.integrations.logging": MagicMock(),
                },
            ):
                init_sentry()
        finally:
            s_mod.SENTRY_DSN = original_dsn

    def test_no_crash_on_sentry_sdk_absent(self) -> None:
        original_dsn = s_mod.SENTRY_DSN
        s_mod.SENTRY_DSN = "https://fake@sentry.io/456"
        try:
            with patch.dict("sys.modules", {"sentry_sdk": None}):  # type: ignore[dict-item]
                init_sentry()  # must not raise even if sentry_sdk is missing
        finally:
            s_mod.SENTRY_DSN = original_dsn


class TestResetSentry:
    def test_reset_allows_reinit(self) -> None:
        original_dsn = s_mod.SENTRY_DSN
        s_mod.SENTRY_DSN = ""
        try:
            init_sentry()
            reset_sentry_for_testing()
            init_sentry()  # second init after reset must not raise
        finally:
            s_mod.SENTRY_DSN = original_dsn
