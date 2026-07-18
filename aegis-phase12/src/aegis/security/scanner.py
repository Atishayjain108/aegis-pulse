"""
aegis.security.scanner — Secret scanning and leak detection utilities.

Integrates with:
    - ``detect-secrets``  — pattern-based secret detection in files/strings
    - ``gitleaks``        — git history scanning (subprocess)
    - ``bandit``          — Python AST security linting (subprocess)
    - ``trivy``           — container image vulnerability scanning (subprocess)

All scanners degrade gracefully when the underlying tool is not installed,
logging a warning instead of crashing.

Usage::

    from aegis.security.scanner import SecretScanner

    scanner = SecretScanner()
    results = scanner.scan_string("api_key = 'sk-abc123def456'")
    if results.has_secrets:
        print(results.findings)

    # Scan a file
    results = scanner.scan_file(Path("config/settings.py"))

    # Scan entire directory (excludes .git, __pycache__, etc.)
    results = scanner.scan_directory(Path("."))

Error codes: AEGIS-SEC-0111..0115 (scanner sub-range)
"""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

_log = structlog.get_logger(__name__)

# Patterns excluded from scanning (committed encrypted files, examples, tests)
_DEFAULT_EXCLUDE_PATTERNS: list[str] = [
    r"\.sops\.yaml$",
    r"\.secrets\.baseline$",
    r"\.env\.example",
    r"tests/",
    r"docs/",
    r"\.git/",
    r"__pycache__/",
    r"\.pyc$",
]


@dataclass
class ScanFinding:
    """A single secret detection finding."""

    file_path: str
    line_number: int
    secret_type: str          # e.g. "Base64 High Entropy String", "AWS Access Key"
    hashed_secret: str        # detect-secrets stores only the hash, never plaintext
    is_verified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file_path,
            "line": self.line_number,
            "type": self.secret_type,
            "hash": self.hashed_secret,
            "verified": self.is_verified,
        }


@dataclass
class ScanResult:
    """Aggregate results from one scan operation."""

    scanned_files: int = 0
    findings: list[ScanFinding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    tool: str = "detect-secrets"
    duration_ms: float = 0.0

    @property
    def has_secrets(self) -> bool:
        return len(self.findings) > 0

    @property
    def finding_count(self) -> int:
        return len(self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "scanned_files": self.scanned_files,
            "finding_count": self.finding_count,
            "has_secrets": self.has_secrets,
            "findings": [f.to_dict() for f in self.findings],
            "errors": self.errors,
            "duration_ms": round(self.duration_ms, 2),
        }


class SecretScanner:
    """Unified secret scanning interface.

    All scan methods are synchronous (subprocess-based).
    For async usage wrap with ``asyncio.get_event_loop().run_in_executor``.

    Parameters
    ----------
    exclude_patterns:
        Additional regex patterns to exclude from scans.
    verify_secrets:
        If True, attempt to verify findings (slower, more accurate).
    """

    def __init__(
        self,
        exclude_patterns: list[str] | None = None,
        *,
        verify_secrets: bool = False,
    ) -> None:
        self._exclude = list(_DEFAULT_EXCLUDE_PATTERNS) + (exclude_patterns or [])
        self._verify = verify_secrets

    # ── detect-secrets ────────────────────────────────────────────────────── #

    def scan_string(self, content: str, *, filename: str = "<string>") -> ScanResult:
        """Scan a string for secrets using detect-secrets.

        Parameters
        ----------
        content:
            Text content to scan.
        filename:
            Virtual filename for context in findings.

        Returns
        -------
        ScanResult
        """
        import time

        if not self._is_tool_available("detect-secrets"):
            _log.warning("scanner.detect_secrets_missing", note="pip install detect-secrets")
            return ScanResult(tool="detect-secrets", errors=["detect-secrets not installed"])

        start = time.monotonic()
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                delete=False,
                encoding="utf-8",
            ) as f:
                f.write(content)
                tmp_path = Path(f.name)

            result = self._run_detect_secrets(tmp_path)
            result.duration_ms = (time.monotonic() - start) * 1000
            # Override file path in findings to the virtual name
            for finding in result.findings:
                finding.file_path = filename
            return result
        finally:
            with contextlib.suppress(Exception):
                tmp_path.unlink(missing_ok=True)

    def scan_file(self, path: Path) -> ScanResult:
        """Scan a single file for secrets.

        Parameters
        ----------
        path:
            File path to scan.

        Returns
        -------
        ScanResult
        """
        import time

        if not path.exists():
            return ScanResult(errors=[f"File not found: {path}"])
        if not self._is_tool_available("detect-secrets"):
            return ScanResult(tool="detect-secrets", errors=["detect-secrets not installed"])

        start = time.monotonic()
        result = self._run_detect_secrets(path)
        result.duration_ms = (time.monotonic() - start) * 1000
        return result

    def scan_directory(self, directory: Path) -> ScanResult:
        """Scan an entire directory recursively for secrets.

        Parameters
        ----------
        directory:
            Root directory to scan.

        Returns
        -------
        ScanResult
        """
        import time

        if not directory.is_dir():
            return ScanResult(errors=[f"Not a directory: {directory}"])
        if not self._is_tool_available("detect-secrets"):
            return ScanResult(tool="detect-secrets", errors=["detect-secrets not installed"])

        start = time.monotonic()
        result = ScanResult(tool="detect-secrets")

        # Build file list respecting exclude patterns
        import re
        exclude_re = [re.compile(p) for p in self._exclude]

        files: list[Path] = []
        for file_path in directory.rglob("*"):
            if not file_path.is_file():
                continue
            rel = str(file_path.relative_to(directory))
            if any(pat.search(rel) for pat in exclude_re):
                continue
            files.append(file_path)

        result.scanned_files = len(files)

        for file_path in files:
            sub = self._run_detect_secrets(file_path)
            result.findings.extend(sub.findings)
            result.errors.extend(sub.errors)

        result.duration_ms = (time.monotonic() - start) * 1000
        _log.info(
            "scanner.directory_scan_complete",
            directory=str(directory),
            files=result.scanned_files,
            findings=result.finding_count,
            duration_ms=round(result.duration_ms, 1),
        )
        return result

    def create_baseline(self, directory: Path, output: Path | None = None) -> Path:
        """Create a detect-secrets baseline file for a directory.

        Parameters
        ----------
        directory:
            Root directory to baseline.
        output:
            Output path; defaults to ``directory/.secrets.baseline``.

        Returns
        -------
        Path
            Path to the written baseline file.
        """
        if not self._is_tool_available("detect-secrets"):
            raise RuntimeError("detect-secrets not installed — run: pip install detect-secrets")

        output = output or (directory / ".secrets.baseline")
        result = subprocess.run(
            ["detect-secrets", "scan", "--all-files", str(directory)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        output.write_text(result.stdout, encoding="utf-8")
        _log.info("scanner.baseline_created", path=str(output))
        return output

    # ── gitleaks ──────────────────────────────────────────────────────────── #

    def scan_git_history(self, repo_path: Path) -> ScanResult:
        """Scan git history for leaked secrets using gitleaks.

        Parameters
        ----------
        repo_path:
            Path to the git repository root.

        Returns
        -------
        ScanResult
        """
        import time

        result = ScanResult(tool="gitleaks")
        if not self._is_tool_available("gitleaks"):
            _log.warning("scanner.gitleaks_missing",
                         note="Install: https://github.com/gitleaks/gitleaks")
            return ScanResult(tool="gitleaks", errors=["gitleaks not installed"])

        start = time.monotonic()
        try:
            proc = subprocess.run(
                [
                    "gitleaks",
                    "detect",
                    "--source", str(repo_path),
                    "--report-format", "json",
                    "--no-banner",
                    "--exit-code", "0",   # don't exit 1 on findings (we handle it)
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if proc.stdout.strip():
                findings_raw = json.loads(proc.stdout)
                for f in (findings_raw or []):
                    result.findings.append(ScanFinding(
                        file_path=f.get("File", ""),
                        line_number=f.get("StartLine", 0),
                        secret_type=f.get("RuleID", "unknown"),
                        hashed_secret=f.get("Secret", "")[:16] + "...",
                    ))
        except subprocess.TimeoutExpired:
            result.errors.append("gitleaks timed out after 120s")
        except json.JSONDecodeError as exc:
            result.errors.append(f"gitleaks JSON parse error: {exc}")
        except Exception as exc:
            result.errors.append(f"gitleaks error: {exc}")

        result.duration_ms = (time.monotonic() - start) * 1000
        if result.has_secrets:
            _log.warning(
                "scanner.gitleaks_findings",
                count=result.finding_count,
                repo=str(repo_path),
            )
        else:
            _log.info("scanner.gitleaks_clean", repo=str(repo_path))
        return result

    # ── bandit ────────────────────────────────────────────────────────────── #

    def run_bandit(self, target: Path) -> dict[str, Any]:
        """Run Bandit security linter on Python code.

        Parameters
        ----------
        target:
            File or directory to lint.

        Returns
        -------
        dict[str, Any]
            Bandit JSON report, or ``{"error": "..."}`` if bandit is unavailable.
        """
        if not self._is_tool_available("bandit"):
            _log.warning("scanner.bandit_missing", note="pip install bandit")
            return {"error": "bandit not installed"}

        try:
            proc = subprocess.run(
                [
                    "bandit",
                    "-r", str(target),
                    "-f", "json",
                    "-q",
                    # B101=assert, B603=subprocess, B607=partial path — used intentionally
                    "--skip", "B101,B603,B607",
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if proc.stdout.strip():
                return dict(json.loads(proc.stdout))
            return {"results": [], "metrics": {}}
        except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as exc:
            return {"error": str(exc)}

    # ── trivy ─────────────────────────────────────────────────────────────── #

    def scan_container_image(self, image: str) -> dict[str, Any]:
        """Scan a container image for vulnerabilities using Trivy.

        Parameters
        ----------
        image:
            Docker image name:tag to scan.

        Returns
        -------
        dict[str, Any]
            Trivy JSON report, or ``{"error": "..."}`` if trivy is unavailable.
        """
        if not self._is_tool_available("trivy"):
            _log.warning("scanner.trivy_missing", note="Install: https://trivy.dev")
            return {"error": "trivy not installed"}

        try:
            proc = subprocess.run(
                [
                    "trivy", "image",
                    "--format", "json",
                    "--quiet",
                    "--severity", "HIGH,CRITICAL",
                    image,
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if proc.stdout.strip():
                return dict(json.loads(proc.stdout))
            return {"Results": []}
        except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as exc:
            return {"error": str(exc)}

    # ── Internals ─────────────────────────────────────────────────────────── #

    def _run_detect_secrets(self, path: Path) -> ScanResult:
        """Run detect-secrets on a single file and return parsed results."""
        result = ScanResult(tool="detect-secrets", scanned_files=1)
        try:
            proc = subprocess.run(
                ["detect-secrets", "scan", str(path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if not proc.stdout.strip():
                return result

            data = json.loads(proc.stdout)
            for file_path, file_findings in data.get("results", {}).items():
                for finding in file_findings:
                    result.findings.append(ScanFinding(
                        file_path=file_path,
                        line_number=finding.get("line_number", 0),
                        secret_type=finding.get("type", "unknown"),
                        hashed_secret=finding.get("hashed_secret", ""),
                        is_verified=finding.get("is_verified", False),
                    ))
        except subprocess.TimeoutExpired:
            result.errors.append(f"detect-secrets timed out scanning {path}")
        except json.JSONDecodeError as exc:
            result.errors.append(f"detect-secrets JSON parse error: {exc}")
        except Exception as exc:
            result.errors.append(f"detect-secrets error: {exc}")
        return result

    @staticmethod
    def _is_tool_available(tool: str) -> bool:
        """Return True if ``tool`` is on PATH."""
        return shutil.which(tool) is not None
