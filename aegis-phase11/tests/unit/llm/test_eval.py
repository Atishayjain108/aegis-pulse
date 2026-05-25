"""tests/unit/llm/test_eval.py"""
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock
import pytest


def _make_response(content: str = "PROCEED analysis done"):
    from aegis.llm.gateway.response import LLMResponse, TokenUsage
    return LLMResponse(content=content, provider="ollama", model="test",
                       usage=TokenUsage(10, 20, 30), latency_ms=50.0)


class TestEvalRunner:
    @pytest.mark.asyncio()
    async def test_run_all_empty_golden_dir(self, tmp_path):
        from aegis.llm.eval.runner import EvalRunner
        from aegis.llm.registry.prompt_registry import PromptRegistry

        mock_gw = MagicMock()
        registry = PromptRegistry(tmp_path)
        registry.load_all()
        runner = EvalRunner(mock_gw, registry, golden_dir=tmp_path / "nonexistent")
        report = await runner.run_all()
        assert report.total == 0
        assert report.pass_rate == 1.0

    @pytest.mark.asyncio()
    async def test_run_all_passes_with_matching_output(self, tmp_path):
        from aegis.llm.eval.runner import EvalRunner
        from aegis.llm.registry.prompt_registry import PromptRegistry
        import json

        # Create a template
        tpl = tmp_path / "my_tpl.jinja2"
        tpl.write_text("---\nname: my_tpl\nversion: 1\nrequired_vars: [topic]\ndescription: test\n---\n{{ topic }}")

        # Create a golden file
        golden_dir = tmp_path / "golden"
        golden_dir.mkdir()
        (golden_dir / "my_tpl.jsonl").write_text(
            json.dumps({"input_vars": {"topic": "AI"}, "expected_contains": ["PROCEED"]}) + "\n"
        )

        mock_gw = MagicMock()
        mock_gw.complete = AsyncMock(return_value=_make_response("PROCEED is the verdict"))

        registry = PromptRegistry(tmp_path)
        registry.load_all()
        runner = EvalRunner(mock_gw, registry, golden_dir=golden_dir)
        report = await runner.run_all()
        assert report.total == 1
        assert report.passed == 1
        assert report.pass_rate == 1.0

    @pytest.mark.asyncio()
    async def test_run_all_fails_on_missing_phrase(self, tmp_path):
        from aegis.llm.eval.runner import EvalRunner
        from aegis.llm.registry.prompt_registry import PromptRegistry
        import json

        tpl = tmp_path / "tpl2.jinja2"
        tpl.write_text("---\nname: tpl2\nversion: 1\nrequired_vars: [x]\ndescription: t\n---\n{{ x }}")

        golden_dir = tmp_path / "golden"
        golden_dir.mkdir()
        (golden_dir / "tpl2.jsonl").write_text(
            json.dumps({"input_vars": {"x": "val"}, "expected_contains": ["NEVER_IN_OUTPUT"]}) + "\n"
        )

        mock_gw = MagicMock()
        mock_gw.complete = AsyncMock(return_value=_make_response("something else"))

        registry = PromptRegistry(tmp_path)
        registry.load_all()
        runner = EvalRunner(mock_gw, registry, golden_dir=golden_dir)
        report = await runner.run_all()
        assert report.failed == 1
        assert not report.is_passing()


class TestEvalMetrics:
    def _make_report(self, pass_rate: float = 1.0):
        from aegis.llm.eval.runner import EvalReport, EvalResult, EvalCase
        case = EvalCase("test", {})
        results = [EvalResult(case=case, passed=True, latency_ms=100.0, output="ok")]
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        return EvalReport(
            results=results, total=total, passed=passed,
            failed=total - passed, pass_rate=pass_rate, duration_ms=200.0
        )

    def test_latency_metrics_from_results(self):
        from aegis.llm.eval.metrics import EvalMetricsCollector
        report = self._make_report()
        c = EvalMetricsCollector()
        latency = c.latency(report)
        assert latency.min_ms == 100.0
        assert latency.max_ms == 100.0

    def test_summary_dict_keys(self):
        from aegis.llm.eval.metrics import EvalMetricsCollector
        report = self._make_report()
        c = EvalMetricsCollector()
        summary = c.summary_dict(report)
        assert "pass_rate" in summary
        assert "latency_p99_ms" in summary

    def test_regression_detected(self):
        from aegis.llm.eval.metrics import EvalMetricsCollector
        baseline = self._make_report(pass_rate=0.95)
        current = self._make_report(pass_rate=0.80)
        c = EvalMetricsCollector()
        reg = c.regression(baseline, current, threshold=0.05)
        assert reg.regressed
        assert reg.status == "REGRESSION"
