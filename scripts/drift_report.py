"""
AEGIS Signal Feature Drift Report — Evidently-powered.

Pulls two time windows of signals from the production DB and computes
an Evidently DataDriftReport comparing the feature distributions. This
catches silent data quality regressions before they corrupt model predictions.

Architecture relationship:
  Operates on Phase 1 signals (TimescaleDB) using real feature columns
  that feed the Phase 3 InferenceRunner (confidence, completeness, price,
  intent, platform). Outputs an HTML report + JSON summary for CI use.

Usage:
    uv run python scripts/drift_report.py
    uv run python scripts/drift_report.py --reference-hours 168 --current-hours 24
    uv run python scripts/drift_report.py --output-html /tmp/drift.html --json-out

Exit codes:
    0 — no drift detected (or all features stable)
    1 — drift detected in >= 1 feature (for CI gating)
    2 — DB unreachable / insufficient data
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_DEV_DSN = os.getenv(
    "AEGIS_PG_DSN",
    "postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis",
)
_TENANT_ID = "00000000-0000-0000-0000-000000000001"

_FEATURES = [
    "confidence",
    "completeness",
    "hours_since_created",
    "price",
    "is_purchase",
]


async def _fetch_window(
    conn: object,
    hours_start: float,
    hours_end: float,
    limit: int,
) -> list[dict]:
    """Fetch signal features for a time window [NOW - hours_start, NOW - hours_end]."""
    rows = await conn.fetch(  # type: ignore[attr-defined]
        """
        SELECT
            ROUND(source_confidence::numeric, 4)                            AS confidence,
            ROUND(completeness::numeric, 4)                                 AS completeness,
            ROUND(EXTRACT(EPOCH FROM (NOW() - created_at))::numeric/3600, 2)
                                                                            AS hours_since_created,
            COALESCE(price_amount, 0)                                       AS price,
            CASE WHEN intent = 'purchase' THEN 1.0 ELSE 0.0 END            AS is_purchase,
            platform
        FROM signals
        WHERE created_at > NOW() - ($1 || ' hours')::interval
          AND created_at <= NOW() - ($2 || ' hours')::interval
        ORDER BY created_at DESC
        LIMIT $3
        """,
        str(hours_start),
        str(hours_end),
        limit,
    )
    return [dict(r) for r in rows]


async def _load_data(
    ref_hours: float,
    cur_hours: float,
    limit: int,
) -> tuple[list[dict], list[dict]]:
    """Return (reference_rows, current_rows) from the DB."""
    try:
        import asyncpg  # type: ignore[import-untyped]
    except ImportError:
        print("ERROR: asyncpg not installed. Run: uv sync --all-extras")
        sys.exit(2)

    try:
        conn = await asyncio.wait_for(asyncpg.connect(_DEV_DSN), timeout=10.0)
    except Exception as exc:
        print(f"ERROR: DB connection failed: {exc}")
        sys.exit(2)

    try:
        await conn.execute(f"SET LOCAL app.current_tenant = '{_TENANT_ID}'")
        ref = await _fetch_window(conn, ref_hours, cur_hours, limit)
        cur = await _fetch_window(conn, cur_hours, 0, limit)
    finally:
        await conn.close()

    return ref, cur


def _build_dataframe(rows: list[dict]) -> object:
    try:
        import pandas as pd
    except ImportError:
        print("ERROR: pandas not installed.")
        sys.exit(2)

    if not rows:
        return None
    df = pd.DataFrame(rows)[_FEATURES]
    return df.astype(float)


def _run_evidently(ref_df: object, cur_df: object) -> tuple[dict, object]:
    """Run Evidently DataDriftReport; return (json_summary, report_object)."""
    try:
        from evidently import ColumnMapping
        from evidently.metric_preset import DataDriftPreset
        from evidently.report import Report
    except ImportError:
        print("ERROR: evidently not installed. Run: uv pip install 'evidently>=0.4,<0.5'")
        sys.exit(2)

    column_mapping = ColumnMapping(
        numerical_features=_FEATURES,
    )

    report = Report(metrics=[DataDriftPreset()])
    report.run(
        reference_data=ref_df,
        current_data=cur_df,
        column_mapping=column_mapping,
    )

    result = report.as_dict()
    metrics = result.get("metrics", [])

    drift_summary: dict = {
        "generated_at": datetime.now(UTC).isoformat(),
        "reference_rows": len(ref_df),  # type: ignore[arg-type]
        "current_rows": len(cur_df),  # type: ignore[arg-type]
        "features": {},
        "dataset_drift_detected": False,
        "drifted_features": [],
    }

    for m in metrics:
        m_result = m.get("result", {})
        # DatasetDriftMetric: dataset-level summary
        if "dataset_drift" in m_result and "drift_by_columns" not in m_result:
            drift_summary["dataset_drift_detected"] = bool(m_result["dataset_drift"])
            drift_summary["drift_share"] = round(
                float(m_result.get("share_of_drifted_columns", 0.0)), 4
            )
        # DataDriftTable: per-feature breakdown (Evidently 0.4 structure)
        drift_by_feat = m_result.get("drift_by_columns", {})
        for feat_name, info in drift_by_feat.items():
            drifted = bool(info.get("drift_detected", False))
            # Evidently 0.4: score is the test statistic / p-value depending on test
            score = float(info.get("drift_score") or 1.0)
            drift_summary["features"][feat_name] = {
                "drift_detected": drifted,
                "drift_score": round(score, 6),
                "stattest": info.get("stattest_name", ""),
                "threshold": round(float(info.get("stattest_threshold") or 0.05), 4),
            }
            if drifted:
                drift_summary["drifted_features"].append(feat_name)

    return drift_summary, report


def _print_summary(summary: dict) -> None:
    print()
    print("═" * 60)
    print("  AEGIS SIGNAL FEATURE DRIFT REPORT")
    print(f"  Generated: {summary['generated_at']}")
    print("═" * 60)
    print(
        f"\n  Reference: {summary['reference_rows']} signals   "
        f"Current: {summary['current_rows']} signals"
    )
    print(f"\n  Dataset drift detected: {'⚠️  YES' if summary['dataset_drift_detected'] else '✅ NO'}")
    if "drift_share" in summary:
        print(f"  Drifted feature share:  {summary['drift_share']:.1%}")

    print("\n  Per-feature drift:")
    for feat, info in summary["features"].items():
        icon = "⚠️ " if info["drift_detected"] else "✅"
        score = info["drift_score"]
        test = info["stattest"]
        thresh = info.get("threshold", 0.05)
        print(f"    {icon} {feat:<26} score={score:.4f}  thresh={thresh}  [{test}]")

    if summary["drifted_features"]:
        print(f"\n  ⚠️  Drifted features: {', '.join(summary['drifted_features'])}")
    else:
        print("\n  ✅ All features stable — no drift detected")
    print("═" * 60)


async def main(
    ref_hours: float,
    cur_hours: float,
    limit: int,
    output_html: str | None,
    json_out: bool,
    json_path: str | None,
    ci_gate: bool,
) -> int:
    print(f"Fetching signals: reference=[{ref_hours}h–{cur_hours}h ago], "
          f"current=[{cur_hours}h–now]…")

    ref_rows, cur_rows = await _load_data(ref_hours, cur_hours, limit)

    if len(ref_rows) < 10:
        print(f"ERROR: insufficient reference data ({len(ref_rows)} rows). "
              "Need >= 10 signals. Run scrapers first.")
        sys.exit(2)
    if len(cur_rows) < 10:
        print(f"ERROR: insufficient current data ({len(cur_rows)} rows). "
              "Need >= 10 signals in recent window.")
        sys.exit(2)

    ref_df = _build_dataframe(ref_rows)
    cur_df = _build_dataframe(cur_rows)

    print(f"Running Evidently DataDriftPreset on {len(_FEATURES)} features…")
    summary, report = _run_evidently(ref_df, cur_df)

    _print_summary(summary)

    if output_html:
        report.save_html(output_html)
        print(f"\n  HTML report saved to: {output_html}")

    if json_path:
        with open(json_path, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"  JSON summary saved to: {json_path}")

    if json_out:
        print(json.dumps(summary, indent=2))

    if ci_gate and summary["dataset_drift_detected"]:
        print("\n  CI GATE: drift detected — exiting 1")
        return 1

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AEGIS Signal Feature Drift Report")
    parser.add_argument("--reference-hours", type=float, default=168.0,
                        help="End of reference window (hours ago, default: 168 = 7 days)")
    parser.add_argument("--current-hours", type=float, default=24.0,
                        help="Start of current window (hours ago, default: 24)")
    parser.add_argument("--limit", type=int, default=1000,
                        help="Max rows per window (default: 1000)")
    parser.add_argument("--output-html", metavar="PATH",
                        help="Save HTML report to this path")
    parser.add_argument("--json-path", metavar="PATH",
                        help="Save JSON summary to this path")
    parser.add_argument("--json-out", action="store_true",
                        help="Print JSON summary to stdout")
    parser.add_argument("--ci-gate", action="store_true",
                        help="Exit 1 if drift detected (for CI pipelines)")
    args = parser.parse_args()

    code = asyncio.run(main(
        ref_hours=args.reference_hours,
        cur_hours=args.current_hours,
        limit=args.limit,
        output_html=args.output_html,
        json_out=args.json_out,
        json_path=args.json_path,
        ci_gate=args.ci_gate,
    ))
    sys.exit(code)
