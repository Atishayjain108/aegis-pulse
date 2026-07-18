"""
aegis.llm.eval.runner — nightly eval runner
============================================

Runs every prompt template against its golden-answer set and reports
pass rate.  CI fails if pass rate drops below ``EVAL_PASS_RATE_FLOOR``.

Golden answer files live under ``src/aegis/llm/eval/golden/``:

    <template_name>.jsonl  — one JSON object per line:
    {"input_vars": {...}, "expected_contains": ["word1", "word2"],
     "expected_not_contains": [...], "schema": "OptionalPydanticClass"}

Author: AEGIS Engineering
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from aegis.llm.constants import EVAL_GOLDEN_DIR, EVAL_PASS_RATE_FLOOR
from aegis.llm.gateway.gateway import LLMGateway
from aegis.llm.registry.prompt_registry import PromptRegistry

_log = structlog.get_logger("aegis.llm.eval")


@dataclass
class EvalCase:
    """A single golden-answer test case."""

    template_name: str
    input_vars: dict[str, Any]
    expected_contains: list[str] = field(default_factory=list)
    expected_not_contains: list[str] = field(default_factory=list)
    schema_name: str | None = None


@dataclass
class EvalResult:
    """Result of a single eval case."""

    case: EvalCase
    passed: bool
    latency_ms: float
    output: str
    failures: list[str] = field(default_factory=list)


@dataclass
class EvalReport:
    """Aggregated report from a full eval run."""

    results: list[EvalResult]
    total: int
    passed: int
    failed: int
    pass_rate: float
    duration_ms: float

    def is_passing(self) -> bool:
        return self.pass_rate >= EVAL_PASS_RATE_FLOOR

    def summary(self) -> str:
        status = "PASS" if self.is_passing() else "FAIL"
        return (
            f"[{status}] {self.passed}/{self.total} passed "
            f"({self.pass_rate:.1%}) in {self.duration_ms:.0f}ms"
        )


class EvalRunner:
    """
    Nightly eval runner.

    Parameters
    ----------
    gateway:
        The ``LLMGateway`` to call for each eval case.
    registry:
        Loaded ``PromptRegistry``.
    golden_dir:
        Path to the directory containing ``.jsonl`` golden files.

    Example
    -------
    .. code-block:: python

        runner = EvalRunner(gateway=gw, registry=registry)
        report = await runner.run_all()
        print(report.summary())
        if not report.is_passing():
            raise SystemExit(1)
    """

    def __init__(
        self,
        gateway: LLMGateway,
        registry: PromptRegistry,
        *,
        golden_dir: str | Path = EVAL_GOLDEN_DIR,
    ) -> None:
        self._gateway = gateway
        self._registry = registry
        self._golden_dir = Path(golden_dir)

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    async def run_all(self) -> EvalReport:
        """Run all golden-answer tests and return a report."""
        cases = self._load_cases()
        if not cases:
            _log.warning("eval.no_cases", golden_dir=str(self._golden_dir))
            return EvalReport(
                results=[], total=0, passed=0, failed=0, pass_rate=1.0, duration_ms=0.0
            )

        t0 = time.perf_counter()
        results = []
        for case in cases:
            result = await self._run_case(case)
            results.append(result)
            _log.debug(
                "eval.case_done",
                template=case.template_name,
                passed=result.passed,
                latency_ms=round(result.latency_ms, 1),
            )

        duration_ms = (time.perf_counter() - t0) * 1000
        passed = sum(1 for r in results if r.passed)
        total = len(results)
        pass_rate = passed / total if total else 1.0

        report = EvalReport(
            results=results,
            total=total,
            passed=passed,
            failed=total - passed,
            pass_rate=pass_rate,
            duration_ms=duration_ms,
        )
        _log.info(
            "eval.done",
            summary=report.summary(),
            pass_rate=round(pass_rate, 4),
        )
        return report

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load_cases(self) -> list[EvalCase]:
        cases: list[EvalCase] = []
        if not self._golden_dir.exists():
            return cases

        for path in sorted(self._golden_dir.glob("*.jsonl")):
            template_name = path.stem
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    data = json.loads(line)
                    cases.append(
                        EvalCase(
                            template_name=template_name,
                            input_vars=data.get("input_vars", {}),
                            expected_contains=data.get("expected_contains", []),
                            expected_not_contains=data.get("expected_not_contains", []),
                            schema_name=data.get("schema"),
                        )
                    )
                except json.JSONDecodeError as exc:
                    _log.warning(
                        "eval.parse_error", path=str(path), error=str(exc)
                    )
        return cases

    async def _run_case(self, case: EvalCase) -> EvalResult:
        t0 = time.perf_counter()
        failures: list[str] = []
        output = ""

        try:
            rendered = self._registry.render(case.template_name, **case.input_vars)
            messages = [{"role": "user", "content": rendered}]
            response = await self._gateway.complete(messages, skip_guardrails=True)
            output = response.content
            latency_ms = (time.perf_counter() - t0) * 1000

            # Check expected_contains
            for phrase in case.expected_contains:
                if phrase.lower() not in output.lower():
                    failures.append(f"Missing expected phrase: {phrase!r}")

            # Check expected_not_contains
            for phrase in case.expected_not_contains:
                if phrase.lower() in output.lower():
                    failures.append(f"Found forbidden phrase: {phrase!r}")

        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            failures.append(f"Exception: {exc}")

        return EvalResult(
            case=case,
            passed=len(failures) == 0,
            latency_ms=latency_ms,
            output=output[:1000],
            failures=failures,
        )
