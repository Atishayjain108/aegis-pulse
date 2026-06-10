"""
tests/mutation/run_mutmut.py — Mutation testing runner for AEGIS Pulse.

Invokes mutmut against the hottest code paths and reports mutation score.
Target: ≥ 85% mutation kill rate.

Usage:
    python tests/mutation/run_mutmut.py [--paths src/aegis/scrape/dedup.py]
    make test-mutation

Architecture: Phase 13 (Testing) → mutation testing via mutmut
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

# ---------------------------------------------------------------------------
# High-value mutation targets (sorted by criticality)
# ---------------------------------------------------------------------------

MUTATION_TARGETS: list[dict[str, str]] = [
    {
        "path": "src/aegis/scrape/dedup.py",
        "description": "Semantic deduplication — wrong threshold kills signal quality",
    },
    {
        "path": "src/aegis/scrape/confidence.py",
        "description": "Confidence gate — wrong threshold or inversion affects pipeline",
    },
    {
        "path": "src/aegis/agents/supervisor.py",
        "description": "Supervisor aggregation — wrong weighting changes investment decisions",
    },
    {
        "path": "src/aegis/predict/models/heuristic.py",
        "description": "Heuristic floor — wrong comparisons flip trade verdicts",
    },
    {
        "path": "src/aegis/predict/resilience.py",
        "description": "Resilience wrapper — wrong timeout or retry count",
    },
    {
        "path": "src/aegis/agents_phase3_glue/bridge.py",
        "description": "Bridge mapping — wrong horizon or verdict translation",
    },
    {
        "path": "src/aegis/datalake/silver/builder.py",
        "description": "Silver builder — NaN check idiom, wrong aggregations",
    },
]


def _run(cmd: list[str]) -> int:
    print(f"\n$ {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, check=False)
    return result.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description="AEGIS mutation testing runner")
    parser.add_argument(
        "--paths",
        nargs="*",
        help="Specific file paths to mutate (default: all MUTATION_TARGETS)",
    )
    parser.add_argument(
        "--runner",
        default="python -m pytest -x -q --no-header --no-cov -m 'not integration and not perf'",
        help="Test runner command",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="CI mode: fail if kill rate < 85%%",
    )
    args = parser.parse_args()

    targets = args.paths or [t["path"] for t in MUTATION_TARGETS]
    repo_root = Path(__file__).parent.parent.parent

    print("=" * 70)
    print("AEGIS Pulse — Phase 13: Mutation Testing")
    print(f"Targets: {len(targets)} files")
    print("=" * 70)

    for path in targets:
        full_path = repo_root / path
        if not full_path.exists():
            print(f"  SKIP {path} (file not found)")
            continue

        print(f"\n→ Mutating: {path}")
        rc = _run([
            "python", "-m", "mutmut", "run",
            f"--paths-to-mutate={path}",
            f"--runner={args.runner}",
            "--no-progress",
        ])
        if rc not in (0, 1):  # 1 = some mutants survived (expected in dev)
            print(f"  ERROR: mutmut exited with {rc}")

    # Generate HTML report
    print("\n→ Generating HTML report...")
    _run(["python", "-m", "mutmut", "html"])

    # Print summary
    _run(["python", "-m", "mutmut", "results"])

    if args.ci:
        # Parse kill rate and fail if below threshold
        import re
        result = subprocess.run(
            ["python", "-m", "mutmut", "results"],
            capture_output=True,
            text=True,
            check=False,
        )
        output = result.stdout + result.stderr
        match = re.search(r"Killed:\s+(\d+)", output)
        total_match = re.search(r"Total:\s+(\d+)", output)
        if match and total_match:
            killed = int(match.group(1))
            total = int(total_match.group(1))
            if total > 0:
                rate = killed / total * 100
                print(f"\nMutation kill rate: {rate:.1f}% ({killed}/{total})")
                if rate < 85.0:
                    print(f"FAIL: Kill rate {rate:.1f}% < 85% threshold")
                    sys.exit(1)
                else:
                    print(f"PASS: Kill rate {rate:.1f}% ≥ 85% threshold")


if __name__ == "__main__":
    main()
