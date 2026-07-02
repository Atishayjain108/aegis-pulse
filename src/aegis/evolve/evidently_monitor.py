"""
Evidently-based model monitoring for AEGIS.

Phase 9 evolve layer. Wraps Evidently AI's data and model quality reports.
Generates both structured JSON metrics (for alerting) and HTML reports
(for the dashboard) from prediction_outcomes data.

Falls back to AEGIS native KS-distance detection when Evidently absent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

_log = structlog.get_logger("aegis.evolve.evidently_monitor")

try:
    # NOTE: a bare ``except ImportError`` is insufficient — some Evidently builds
    # raise a ``DeprecationWarning`` (escalated to an error under pytest's
    # ``filterwarnings=error``) or a ``pydantic`` ``ValidationError`` at import
    # time. Catch broadly so a broken/incompatible install degrades gracefully to
    # the native KS-distance detector instead of crashing the evolve layer.
    from evidently import ColumnMapping
    from evidently.metric_preset import DataDriftPreset, TargetDriftPreset
    from evidently.report import Report

    _EVIDENTLY_AVAILABLE = True
except Exception:
    _EVIDENTLY_AVAILABLE = False


class EvidentlyMonitor:
    """
    Wraps Evidently AI reports for production model monitoring.

    Generates:
    - Data drift report: are feature distributions shifting?
    - Target drift report: is outcome distribution shifting?
    - HTML report: saved to ~/.aegis/reports/ for dashboard serving

    Usage:
        monitor = EvidentlyMonitor()
        result = await monitor.run_drift_report(reference_df, current_df)
    """

    _REPORTS_DIR = Path.home() / ".aegis" / "reports"

    def __init__(self) -> None:
        self._reports_dir = self._REPORTS_DIR
        try:
            self._reports_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # pragma: no cover - fs failure path
            _log.warning("evidently_monitor.reports_dir_failed", error=str(exc))

    async def run_drift_report(
        self,
        reference_df: Any,     # pandas DataFrame of reference period predictions
        current_df: Any,       # pandas DataFrame of current period predictions
        feature_cols: list[str] | None = None,
        target_col: str = "roi",
    ) -> dict:
        """
        Run full drift analysis. Returns structured dict with drift metrics.
        Also saves HTML report to ~/.aegis/reports/drift_{timestamp}.html.
        """
        if not _EVIDENTLY_AVAILABLE:
            _log.warning("evidently_monitor.not_available", fallback="native_ks_distance")
            return {"available": False, "fallback": "native_ks_distance"}

        try:
            import pandas as pd

            if not isinstance(reference_df, pd.DataFrame):
                return {"error": "reference_df must be pandas DataFrame"}

            column_mapping = ColumnMapping(
                target=target_col,
                numerical_features=feature_cols or [],
            )

            report = Report(metrics=[
                DataDriftPreset(),
                TargetDriftPreset(),
            ])
            report.run(
                reference_data=reference_df,
                current_data=current_df,
                column_mapping=column_mapping,
            )

            # Save HTML report
            ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            report_path = self._reports_dir / f"drift_{ts}.html"
            report.save_html(str(report_path))

            # Extract structured metrics
            result_dict = report.as_dict()
            drift_detected = self._extract_drift_detected(result_dict)
            drifted_features = self._extract_drifted_features(result_dict)

            _log.info(
                "evidently_monitor.report_complete",
                drift_detected=drift_detected,
                drifted_features=len(drifted_features),
                report_path=str(report_path),
            )

            return {
                "available": True,
                "drift_detected": drift_detected,
                "drifted_features": drifted_features,
                "report_path": str(report_path),
                "timestamp": datetime.now(UTC).isoformat(),
            }
        except Exception as exc:
            _log.error("evidently_monitor.report_failed", error=str(exc))
            return {"available": True, "error": str(exc)}

    def _extract_drift_detected(self, result: dict) -> bool:
        try:
            for metric in result.get("metrics", []):
                if "DatasetDriftMetric" in str(metric.get("metric", "")):
                    return bool(metric.get("result", {}).get("dataset_drift", False))
        except Exception:
            pass
        return False

    def _extract_drifted_features(self, result: dict) -> list[str]:
        drifted = []
        try:
            for metric in result.get("metrics", []):
                result_data = metric.get("result", {})
                for feat, data in result_data.get("drift_by_columns", {}).items():
                    if data.get("drift_detected", False):
                        drifted.append(feat)
        except Exception:
            pass
        return drifted
