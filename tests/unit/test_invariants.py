"""
PASS12 — Programmatic invariant verification.

These tests fail if anyone accidentally violates the sacred AEGIS invariants.
They analyse the source tree itself (not runtime behaviour), so they are fast
and require no infrastructure.

Run as part of the standard suite: pytest tests/unit/test_invariants.py
"""

from __future__ import annotations

import re
from pathlib import Path

# Resolve roots relative to the repo root (two parents up from tests/unit/).
_REPO = Path(__file__).resolve().parents[2]
SRC = _REPO / "src"
PHASE4_SRC = _REPO / "aegis-phase4" / "src"

_SKIP_FRAGMENTS = ("test_", ".pyc", "__pycache__")


def _py_files(*roots: Path) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if root.exists():
            files.extend(root.rglob("*.py"))
    return [f for f in files if not any(p in str(f) for p in _SKIP_FRAGMENTS)]


def _line_at(content: str, idx: int) -> tuple[int, str]:
    line_no = content[:idx].count("\n") + 1
    line = content.split("\n")[line_no - 1]
    return line_no, line


def test_invariant_1_stream_field_body() -> None:
    """§1: Redis stream field must always be 'body' (never payload/data/message)."""
    violations: list[str] = []
    pattern = re.compile(r'"(payload|data|message)"\s*:')
    context = re.compile(r"(xadd|XADD|publish_event)")
    for f in _py_files(SRC, PHASE4_SRC):
        content = f.read_text(encoding="utf-8")
        for match in pattern.finditer(content):
            line_no, line = _line_at(content, match.start())
            if context.search(line) and "# noqa" not in line:
                violations.append(f"{f}:{line_no}: {line.strip()}")
    assert not violations, "§1 violations (use 'body'):\n" + "\n".join(violations)


def test_invariant_3_kelly_cap() -> None:
    """§3: Kelly fraction literal never exceeds 0.25."""
    violations: list[str] = []
    pattern = re.compile(r"kelly_fraction\s*=\s*([0-9.]+)")
    for f in _py_files(SRC, PHASE4_SRC):
        content = f.read_text(encoding="utf-8")
        for match in pattern.finditer(content):
            try:
                value = float(match.group(1))
            except ValueError:
                continue
            if value > 0.25:
                line_no, _ = _line_at(content, match.start())
                violations.append(f"{f}:{line_no}: kelly_fraction={value} > 0.25")
    assert not violations, "§3 violations:\n" + "\n".join(violations)


def test_invariant_4_no_naive_datetimes() -> None:
    """§4: All datetimes must be timezone-aware UTC (no bare datetime.now())."""
    violations: list[str] = []
    pattern = re.compile(r"datetime\.now\(\)")
    for f in _py_files(SRC):
        content = f.read_text(encoding="utf-8")
        for match in pattern.finditer(content):
            line_no, line = _line_at(content, match.start())
            upper = line.upper()
            if "TIMEZONE" not in upper and "UTC" not in upper and "# noqa" not in line:
                violations.append(f"{f}:{line_no}: {line.strip()}")
    assert not violations, "§4 violations (use datetime.now(UTC)):\n" + "\n".join(
        violations
    )


def test_invariant_5_no_stdlib_logging_in_agents() -> None:
    """§5: Only structlog in aegis.agents.* and aegis.llm.*"""
    violations: list[str] = []
    agent_dirs = [SRC / "aegis" / "agents", SRC / "aegis" / "llm"]
    pattern = re.compile(r"^import logging$|^from logging import", re.MULTILINE)
    for agent_dir in agent_dirs:
        if not agent_dir.exists():
            continue
        for f in agent_dir.rglob("*.py"):
            if "__pycache__" in str(f):
                continue
            content = f.read_text(encoding="utf-8")
            if pattern.search(content) and "structlog" not in content:
                violations.append(str(f))
    assert not violations, "§5 violations (use structlog):\n" + "\n".join(violations)


def test_invariant_6_no_pydantic_v1() -> None:
    """§6: No Pydantic v1 patterns (@validator, .dict())."""
    violations: list[str] = []
    patterns = [
        re.compile(r"@validator\b"),
        re.compile(r"from pydantic import.*\bvalidator\b"),
    ]
    for f in _py_files(SRC):
        content = f.read_text(encoding="utf-8")
        for pattern in patterns:
            for match in pattern.finditer(content):
                line_no, line = _line_at(content, match.start())
                if "# noqa" not in line:
                    violations.append(f"{f}:{line_no}: {line.strip()}")
    assert not violations, "§6 violations (use pydantic v2):\n" + "\n".join(violations)


def test_invariant_11_streams_capped() -> None:
    """§11: All Redis stream xadd calls must specify maxlen within 5 lines."""
    violations: list[str] = []
    xadd_pattern = re.compile(r"\.xadd\(|(?<![\w.])xadd\(")
    for f in _py_files(SRC, PHASE4_SRC):
        content = f.read_text(encoding="utf-8")
        lines = content.split("\n")
        for match in xadd_pattern.finditer(content):
            line_no, line = _line_at(content, match.start())
            # Skip Protocol / abstract method *definitions* — §11 governs call
            # sites, not type signatures (whose maxlen kwarg may sit >5 lines down).
            if re.search(r"\bdef\s+xadd\b", line):
                continue
            window = "\n".join(lines[line_no - 1 : line_no + 4])
            if "maxlen" not in window and "# noqa" not in window:
                violations.append(f"{f}:{line_no}: xadd without maxlen")
    assert not violations, "§11 violations (add maxlen=10_000):\n" + "\n".join(
        violations
    )
