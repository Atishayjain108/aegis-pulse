"""Phase 10 orchestration — flows + schedules.

This package exposes flow definitions for the Bronze→Silver→Gold pipeline.
It uses **Prefect 3** when available (preferred), and gracefully degrades to
a plain synchronous runner when Prefect is not installed. Both runners share
the same task functions, so behaviour is identical apart from concurrency
and the Prefect UI.
"""

from .flows import (
    bronze_ingest_postgres_signals,
    end_to_end_for_date,
    gold_build_for_date,
    silver_build_for_date,
)

__all__ = [
    "bronze_ingest_postgres_signals",
    "end_to_end_for_date",
    "gold_build_for_date",
    "silver_build_for_date",
]
