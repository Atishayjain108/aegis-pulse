"""
tests.unit.security.test_scanner — Unit tests for SecretScanner.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestSecretScanner:
    def _make(self):
        from aegis.security.scanner import SecretScanner
        return SecretScanner()

    def test_instantiation(self) -> None:
        s = self._make()
        assert s is not None

    def test_is_tool_available_true(self) -> None:
        from aegis.security.scanner import SecretScanner
        with patch("shutil.which", return_value="/usr/bin/detect-secrets"):
            assert SecretScanner._is_tool_available("detect-secrets") is True

    def test_is_tool_available_false(self) -> None:
        from aegis.security.scanner import SecretScanner
        with patch("shutil.which", return_value=None):
            assert SecretScanner._is_tool_available("detect-secrets") is False

    def test_scan_string_no_tool(self) -> None:
        s = self._make()
        with patch("shutil.which", return_value=None):
            result = s.scan_string("api_key = 'secret123'")
        assert "detect-secrets not installed" in result.errors

    def test_scan_file_missing(self, tmp_path: Path) -> None:
        s = self._make()
        result = s.scan_file(tmp_path / "nonexistent.py")
        assert "not found" in result.errors[0]

    def test_scan_directory_not_a_dir(self, tmp_path: Path) -> None:
        s = self._make()
        result = s.scan_directory(tmp_path / "ghost")
        assert len(result.errors) > 0

    def test_scan_directory_no_tool(self, tmp_path: Path) -> None:
        (tmp_path / "test.py").write_text("x = 1")
        s = self._make()
        with patch("shutil.which", return_value=None):
            result = s.scan_directory(tmp_path)
        assert "not installed" in result.errors[0]

    def test_scan_result_properties(self) -> None:
        from aegis.security.scanner import ScanFinding, ScanResult
        r = ScanResult(scanned_files=5)
        assert r.has_secrets is False
        assert r.finding_count == 0
        r.findings.append(ScanFinding("f.py", 1, "AWS Key", "abc123"))
        assert r.has_secrets is True
        assert r.finding_count == 1

    def test_scan_result_to_dict(self) -> None:
        from aegis.security.scanner import ScanFinding, ScanResult
        r = ScanResult(scanned_files=2, tool="detect-secrets", duration_ms=12.5)
        r.findings.append(ScanFinding("a.py", 10, "PrivateKey", "hash1"))
        d = r.to_dict()
        assert d["scanned_files"] == 2
        assert d["finding_count"] == 1
        assert d["has_secrets"] is True
        assert d["duration_ms"] == 12.5

    def test_scan_finding_to_dict(self) -> None:
        from aegis.security.scanner import ScanFinding
        f = ScanFinding("main.py", 42, "AWS Access Key", "deadbeef", is_verified=True)
        d = f.to_dict()
        assert d["file"] == "main.py"
        assert d["line"] == 42
        assert d["type"] == "AWS Access Key"
        assert d["verified"] is True

    def test_run_bandit_no_tool(self, tmp_path: Path) -> None:
        s = self._make()
        with patch("shutil.which", return_value=None):
            result = s.run_bandit(tmp_path)
        assert "error" in result
        assert "not installed" in result["error"]

    def test_scan_container_image_no_trivy(self) -> None:
        s = self._make()
        with patch("shutil.which", return_value=None):
            result = s.scan_container_image("nginx:latest")
        assert "error" in result
        assert "not installed" in result["error"]

    def test_scan_git_history_no_gitleaks(self, tmp_path: Path) -> None:
        s = self._make()
        with patch("shutil.which", return_value=None):
            result = s.scan_git_history(tmp_path)
        assert "not installed" in result.errors[0]

    def test_detect_secrets_parse_findings(self, tmp_path: Path) -> None:
        """Verify JSON output from detect-secrets is parsed correctly."""
        s = self._make()
        mock_output = json.dumps({
            "results": {
                str(tmp_path / "config.py"): [
                    {
                        "type": "Base64 High Entropy String",
                        "line_number": 5,
                        "hashed_secret": "abc123def456",
                        "is_verified": False,
                    }
                ]
            }
        })
        mock_proc = MagicMock()
        mock_proc.stdout = mock_output
        with patch("shutil.which", return_value="/usr/bin/detect-secrets"), \
             patch("subprocess.run", return_value=mock_proc):
            result = s._run_detect_secrets(tmp_path / "config.py")
        assert result.has_secrets is True
        assert result.findings[0].secret_type == "Base64 High Entropy String"
        assert result.findings[0].line_number == 5

    def test_gitleaks_parse_findings(self, tmp_path: Path) -> None:
        """Verify gitleaks JSON output is parsed correctly."""
        s = self._make()
        mock_output = json.dumps([
            {
                "RuleID": "aws-access-token",
                "File": "secrets.py",
                "StartLine": 3,
                "Secret": "AKIAIOSFODNN7EXAMPLE",
            }
        ])
        mock_proc = MagicMock()
        mock_proc.stdout = mock_output
        with patch("shutil.which", return_value="/usr/bin/gitleaks"), \
             patch("subprocess.run", return_value=mock_proc):
            result = s.scan_git_history(tmp_path)
        assert result.has_secrets is True
        assert result.findings[0].secret_type == "aws-access-token"
