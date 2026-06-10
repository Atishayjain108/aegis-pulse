"""
src/aegis/testing/validators.py — AEGIS Pulse Phase 13: Pandera Data Validators.

Defines pandera DataFrame schemas for every AEGIS data layer.
These are used in tests AND in production pipeline health checks.

Usage::

    from aegis.testing.validators import SignalDataFrameSchema, validate_signals_df

Architecture: Phase 13 (Testing) — validates data shape at every layer boundary.
"""

from __future__ import annotations

from typing import Any

try:
    import pandera as pa  # type: ignore[import-untyped]
    _PANDERA_AVAILABLE = True
except ImportError:
    _PANDERA_AVAILABLE = False

__all__ = [
    "PANDERA_AVAILABLE",
    "AlertDataFrameSchema",
    "BronzeSignalSchema",
    "PredictionDataFrameSchema",
    "SignalDataFrameSchema",
    "SilverSignalSchema",
    "validate_predictions_df",
    "validate_signals_df",
]

PANDERA_AVAILABLE = _PANDERA_AVAILABLE


# ---------------------------------------------------------------------------
# Signal DataFrame schema (Phase 0 output → Phase 1 input)
# ---------------------------------------------------------------------------

if _PANDERA_AVAILABLE:
    class SignalDataFrameSchema(pa.DataFrameModel):  # type: ignore[misc]
        """Pandera schema for raw signals DataFrame.

        Validates the output of any scrape adapter before persistence.
        """

        platform: pa.typing.Series[str] = pa.Field(  # type: ignore[type-arg]
            nullable=False,
            description="Source platform identifier",
        )
        title: pa.typing.Series[str] = pa.Field(  # type: ignore[type-arg]
            nullable=False,
            min_len=1,
            max_len=2000,
        )
        url: pa.typing.Series[str] = pa.Field(  # type: ignore[type-arg]
            nullable=False,
            str_startswith="http",
        )
        score: pa.typing.Series[float] = pa.Field(ge=0.0, le=1.0, nullable=False)  # type: ignore[type-arg]
        content_hash: pa.typing.Series[str] = pa.Field(  # type: ignore[type-arg]
            nullable=False,
            str_length={"min_value": 64, "max_value": 64},
            description="SHA-256 hex digest",
        )
        scraped_at: pa.typing.Series[Any] = pa.Field(nullable=False)  # type: ignore[type-arg]

        class Config:
            name = "SignalDataFrame"
            strict = False  # allow extra columns

    class PredictionDataFrameSchema(pa.DataFrameModel):  # type: ignore[misc]
        """Schema for Phase 3 predictions output."""

        trend_id: pa.typing.Series[str] = pa.Field(nullable=False, min_len=1)  # type: ignore[type-arg]
        verdict: pa.typing.Series[str] = pa.Field(  # type: ignore[type-arg]
            isin=["breakout", "hold", "decline", "neutral"],
        )
        confidence: pa.typing.Series[float] = pa.Field(ge=0.0, le=1.0)  # type: ignore[type-arg]
        p_breakout: pa.typing.Series[float] = pa.Field(ge=0.0, le=1.0)  # type: ignore[type-arg]
        p_decline: pa.typing.Series[float] = pa.Field(ge=0.0, le=1.0)  # type: ignore[type-arg]

        class Config:
            name = "PredictionDataFrame"
            strict = False

    class AlertDataFrameSchema(pa.DataFrameModel):  # type: ignore[misc]
        """Schema for Phase 4 alert records."""

        alert_id: pa.typing.Series[str] = pa.Field(nullable=False)  # type: ignore[type-arg]
        verdict: pa.typing.Series[str] = pa.Field(isin=["ENTER", "HOLD", "BLOCK"])  # type: ignore[type-arg]
        score: pa.typing.Series[float] = pa.Field(ge=0.0, le=1.0)  # type: ignore[type-arg]
        confidence: pa.typing.Series[float] = pa.Field(ge=0.0, le=1.0)  # type: ignore[type-arg]

        class Config:
            name = "AlertDataFrame"
            strict = False

    class BronzeSignalSchema(pa.DataFrameModel):  # type: ignore[misc]
        """Bronze layer signal DataFrame schema (Phase 10)."""

        batch_id: pa.typing.Series[str] = pa.Field(  # type: ignore[type-arg]
            nullable=False,
            str_length={"min_value": 64, "max_value": 64},
        )
        tenant_id: pa.typing.Series[str] = pa.Field(nullable=False)  # type: ignore[type-arg]
        platform: pa.typing.Series[str] = pa.Field(nullable=False)  # type: ignore[type-arg]
        ingested_at: pa.typing.Series[Any] = pa.Field(nullable=False)  # type: ignore[type-arg]

        class Config:
            name = "BronzeSignal"
            strict = False

    class SilverSignalSchema(pa.DataFrameModel):  # type: ignore[misc]
        """Silver layer signal DataFrame schema (Phase 10)."""

        trend_id: pa.typing.Series[str] = pa.Field(nullable=False)  # type: ignore[type-arg]
        platform: pa.typing.Series[str] = pa.Field(nullable=False)  # type: ignore[type-arg]
        velocity_24h: pa.typing.Series[float] = pa.Field(  # type: ignore[type-arg]
            ge=0.0, nullable=True,
        )
        score_normalised: pa.typing.Series[float] = pa.Field(  # type: ignore[type-arg]
            ge=0.0, le=1.0, nullable=True,
        )
        dt: pa.typing.Series[Any] = pa.Field(  # type: ignore[type-arg]
            nullable=False, description="UTC date partition",
        )

        class Config:
            name = "SilverSignal"
            strict = False

else:
    # Stub classes when pandera not installed — allow import without crashing
    class SignalDataFrameSchema:  # type: ignore[no-redef]
        """Stub when pandera is not installed."""

    class PredictionDataFrameSchema:  # type: ignore[no-redef]
        """Stub when pandera is not installed."""

    class AlertDataFrameSchema:  # type: ignore[no-redef]
        """Stub when pandera is not installed."""

    class BronzeSignalSchema:  # type: ignore[no-redef]
        """Stub when pandera is not installed."""

    class SilverSignalSchema:  # type: ignore[no-redef]
        """Stub when pandera is not installed."""


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validate_signals_df(df: Any) -> tuple[bool, list[str]]:
    """Validate a signals DataFrame against :class:`SignalDataFrameSchema`.

    Args:
        df: pandas DataFrame to validate.

    Returns:
        Tuple of ``(is_valid, errors)`` where errors is an empty list on success.
    """
    if not _PANDERA_AVAILABLE:
        return True, ["pandera not installed — validation skipped"]

    try:
        SignalDataFrameSchema.validate(df, lazy=True)  # type: ignore[union-attr]
        return True, []
    except Exception as exc:
        return False, [str(exc)]


def validate_predictions_df(df: Any) -> tuple[bool, list[str]]:
    """Validate a predictions DataFrame against :class:`PredictionDataFrameSchema`."""
    if not _PANDERA_AVAILABLE:
        return True, ["pandera not installed — validation skipped"]

    try:
        PredictionDataFrameSchema.validate(df, lazy=True)  # type: ignore[union-attr]
        return True, []
    except Exception as exc:
        return False, [str(exc)]
