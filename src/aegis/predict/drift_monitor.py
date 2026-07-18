"""
AEGIS Pulse — Production drift monitoring using Evidently AI.

Compares reference (7-14 days ago) vs current (last 7 days) signal
feature distributions to detect data drift before it degrades predictions.

Run: uv run python src/aegis/predict/drift_monitor.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

REPORTS_DIR = Path("~/.aegis/drift_reports").expanduser()
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

try:
    from evidently.metric_preset import DataDriftPreset, DataQualityPreset
    from evidently.report import Report

    EVIDENTLY_AVAILABLE = True
except ImportError:
    EVIDENTLY_AVAILABLE = False
    log.warning("evidently not installed. Run: uv pip install evidently")


def generate_drift_report(
    reference_data: list[dict[str, Any]],
    current_data: list[dict[str, Any]],
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Compare reference vs current signal distributions."""
    if not EVIDENTLY_AVAILABLE:
        return {"error": "evidently not installed", "drift_detected": False}
    if len(reference_data) < 10 or len(current_data) < 10:
        return {
            "error": (
                f"insufficient data: ref={len(reference_data)} cur={len(current_data)}"
            ),
            "drift_detected": False,
        }
    try:
        import numpy as np
        import pandas as pd

        ref_df = pd.DataFrame(reference_data)
        cur_df = pd.DataFrame(current_data)

        numeric_cols = ref_df.select_dtypes(include=[np.number]).columns.tolist()
        if not numeric_cols:
            return {"error": "no numeric columns", "drift_detected": False}

        ref_df = ref_df[numeric_cols].fillna(0)
        cur_df = cur_df[[c for c in numeric_cols if c in cur_df.columns]].fillna(0)

        report = Report(metrics=[DataDriftPreset(), DataQualityPreset()])
        report.run(reference_data=ref_df, current_data=cur_df)

        if output_path is None:
            ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            output_path = REPORTS_DIR / f"drift_{ts}.html"

        report.save_html(str(output_path))

        result_dict = report.as_dict()
        drifted_features: list[str] = []
        drift_scores: dict[str, float] = {}

        for metric in result_dict.get("metrics", []):
            if "DataDrift" in str(metric.get("metric", "")):
                for col, col_data in (
                    metric.get("result", {}).get("drift_by_columns", {}).items()
                ):
                    drift_scores[col] = col_data.get("drift_score", 0.0)
                    if col_data.get("drift_detected", False):
                        drifted_features.append(col)

        return {
            "drift_detected": len(drifted_features) > 0,
            "drifted_features": drifted_features,
            "drift_scores": drift_scores,
            "reference_rows": len(ref_df),
            "current_rows": len(cur_df),
            "report_path": str(output_path),
            "generated_at": datetime.now(UTC).isoformat(),
        }
    except Exception as e:
        log.error("Drift report failed: %s", e)
        return {"error": str(e), "drift_detected": False}


async def run_drift_check() -> None:
    """Run drift check against real signals from DB."""
    import asyncpg

    DSN = "postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis"
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2)

    ref_rows = await pool.fetch("""
        SELECT source_confidence,
               COALESCE(views, 0) as views,
               COALESCE(likes, 0) as likes,
               COALESCE(comments, 0) as comments
        FROM signals
        WHERE created_at BETWEEN NOW()-INTERVAL '14 days'
          AND NOW()-INTERVAL '7 days'
        LIMIT 500
    """)

    cur_rows = await pool.fetch("""
        SELECT source_confidence,
               COALESCE(views, 0) as views,
               COALESCE(likes, 0) as likes,
               COALESCE(comments, 0) as comments
        FROM signals
        WHERE created_at > NOW()-INTERVAL '7 days'
        LIMIT 500
    """)

    await pool.close()

    _out = sys.stdout.write
    _out(f"Reference period: {len(ref_rows)} signals (7-14 days ago)\n")
    _out(f"Current period:   {len(cur_rows)} signals (last 7 days)\n")

    if len(ref_rows) < 10:
        _out("WARNING: fewer than 10 reference signals.\n")
        _out("System needs > 14 days of data for drift detection.\n")
        _out("This is expected on a fresh install.\n")
        _out("Drift monitoring will activate once historical data accumulates.\n")
        return

    summary = generate_drift_report(
        [dict(r) for r in ref_rows],
        [dict(r) for r in cur_rows],
    )

    _out("\n")
    _out("=" * 50 + "\n")
    _out("EVIDENTLY DRIFT REPORT SUMMARY\n")
    _out("=" * 50 + "\n")
    drift = summary.get("drift_detected", False)
    _out(f"Drift detected:    {'YES' if drift else 'NO'}\n")
    _out(f"Drifted features:  {summary.get('drifted_features', [])}\n")
    _out(f"Reference rows:    {summary.get('reference_rows', 0)}\n")
    _out(f"Current rows:      {summary.get('current_rows', 0)}\n")
    if summary.get("report_path"):
        _out(f"HTML report:       {summary['report_path']}\n")
    if summary.get("error"):
        _out(f"Error:             {summary['error']}\n")
    _out("=" * 50 + "\n")


if __name__ == "__main__":
    asyncio.run(run_drift_check())
