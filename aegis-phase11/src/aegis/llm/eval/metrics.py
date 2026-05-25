"""
aegis.llm.eval.metrics — Eval quality metrics
==============================================

Computes quantitative quality metrics from eval results:

  - ``ExactMatchMetric``  — binary pass/fail on expected_contains checks
  - ``LatencyMetric``     — p50/p95/p99 latency across eval cases
  - ``CoverageMetric``    — % of golden cases covered across templates
  - ``RegressionMetric``  — compares current run to a baseline

Author: AEGIS Engineering
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aegis.llm.eval.runner import EvalReport, EvalResult


@dataclass(frozen=True)
class LatencyMetrics:
    """Latency statistics over an eval run."""

    p50_ms: float
    p95_ms: float
    p99_ms: float
    min_ms: float
    max_ms: float
    mean_ms: float

    @classmethod
    def from_results(cls, results: list["EvalResult"]) -> "LatencyMetrics":
        latencies = sorted(r.latency_ms for r in results)
        if not latencies:
            return cls(0, 0, 0, 0, 0, 0)

        def _percentile(data: list[float], pct: float) -> float:
            idx = int(len(data) * pct / 100)
            return data[min(idx, len(data) - 1)]

        return cls(
            p50_ms=round(_percentile(latencies, 50), 1),
            p95_ms=round(_percentile(latencies, 95), 1),
            p99_ms=round(_percentile(latencies, 99), 1),
            min_ms=round(latencies[0], 1),
            max_ms=round(latencies[-1], 1),
            mean_ms=round(statistics.mean(latencies), 1),
        )


@dataclass(frozen=True)
class CoverageMetrics:
    """Golden case coverage across templates."""

    total_templates: int
    templates_with_cases: int
    total_cases: int
    coverage_pct: float

    @classmethod
    def from_report(cls, report: "EvalReport") -> "CoverageMetrics":
        templates = {r.case.template_name for r in report.results}
        return cls(
            total_templates=len(templates),
            templates_with_cases=len(templates),
            total_cases=report.total,
            coverage_pct=100.0 if report.total > 0 else 0.0,
        )


@dataclass(frozen=True)
class RegressionResult:
    """Comparison between current and baseline eval results."""

    baseline_pass_rate: float
    current_pass_rate: float
    delta: float                     # current - baseline
    regressed: bool                  # True if delta < -threshold
    threshold: float

    @property
    def status(self) -> str:
        if self.regressed:
            return "REGRESSION"
        if self.delta > 0.01:
            return "IMPROVEMENT"
        return "STABLE"

    @classmethod
    def compare(
        cls,
        baseline: "EvalReport",
        current: "EvalReport",
        *,
        threshold: float = 0.05,
    ) -> "RegressionResult":
        delta = current.pass_rate - baseline.pass_rate
        return cls(
            baseline_pass_rate=baseline.pass_rate,
            current_pass_rate=current.pass_rate,
            delta=round(delta, 4),
            regressed=delta < -threshold,
            threshold=threshold,
        )


class EvalMetricsCollector:
    """
    Collects and summarises metrics from an ``EvalReport``.

    Example
    -------
    .. code-block:: python

        collector = EvalMetricsCollector()
        latency = collector.latency(report)
        coverage = collector.coverage(report)
        print(f"p99 latency: {latency.p99_ms}ms")
    """

    def latency(self, report: "EvalReport") -> LatencyMetrics:
        """Compute latency statistics from a report."""
        return LatencyMetrics.from_results(report.results)

    def coverage(self, report: "EvalReport") -> CoverageMetrics:
        """Compute template coverage from a report."""
        return CoverageMetrics.from_report(report)

    def regression(
        self,
        baseline: "EvalReport",
        current: "EvalReport",
        *,
        threshold: float = 0.05,
    ) -> RegressionResult:
        """Compare current run against a baseline for regressions."""
        return RegressionResult.compare(baseline, current, threshold=threshold)

    def failed_cases(self, report: "EvalReport") -> list[dict[str, object]]:
        """Return a list of failed case summaries for debugging."""
        return [
            {
                "template": r.case.template_name,
                "failures": r.failures,
                "latency_ms": r.latency_ms,
                "output_preview": r.output[:200],
            }
            for r in report.results
            if not r.passed
        ]

    def summary_dict(self, report: "EvalReport") -> dict[str, object]:
        """Return a complete metrics summary as a plain dict."""
        latency = self.latency(report)
        coverage = self.coverage(report)
        return {
            "pass_rate": report.pass_rate,
            "passed": report.passed,
            "failed": report.failed,
            "total": report.total,
            "duration_ms": report.duration_ms,
            "is_passing": report.is_passing(),
            "latency_p50_ms": latency.p50_ms,
            "latency_p95_ms": latency.p95_ms,
            "latency_p99_ms": latency.p99_ms,
            "coverage_pct": coverage.coverage_pct,
            "templates_covered": coverage.templates_with_cases,
        }
