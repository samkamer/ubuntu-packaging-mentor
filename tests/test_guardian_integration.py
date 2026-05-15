# tests/test_guardian_integration.py — integration tests for agents/guardian.py
#
# Tests against:
#   1. Canary package — lab/sources/canary-package/canary-1.0/
#      Exact ground truth: 5 known fake-secret findings at precise file:line
#   2. Regression — hello-2.10, zlib-1.3 (skipped when sources absent)
#   3. Curl false positives — documents 2 known FPs so they aren't regressed
#   4. Fixture build logs — blhc parsing via mocked subprocess

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.guardian import (
    _HARDENING_CATEGORIES,
    _compute_score_and_verdict,
    audit,
    run_blhc,
    scan_secrets,
)

# ── Path constants ─────────────────────────────────────────────────────────────

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_SOURCES_DIR = os.path.join(_REPO_ROOT, "lab", "sources")
_CANARY_DIR = os.path.join(_HERE, "canary", "canary-1.0")
_FIXTURES_DIR = os.path.join(_HERE, "fixtures")

_CANARY_PRESENT = os.path.isdir(_CANARY_DIR)
_HELLO_DIR = os.path.join(_SOURCES_DIR, "hello-2.10")
_HELLO_PRESENT = os.path.isdir(_HELLO_DIR)
_ZLIB_DIR = os.path.join(_SOURCES_DIR, "zlib-1.3")
_ZLIB_PRESENT = os.path.isdir(_ZLIB_DIR)
_CURL_DIR = next(
    (
        os.path.join(_SOURCES_DIR, d)
        for d in (os.listdir(_SOURCES_DIR) if os.path.isdir(_SOURCES_DIR) else [])
        if d.startswith("curl-")
    ),
    None,
)
_CURL_PRESENT = _CURL_DIR is not None and os.path.isdir(_CURL_DIR or "")


# ── Ground truth ───────────────────────────────────────────────────────────────
# Each entry: (relative_file, line_number, match_type, severity)
# Line numbers must match the exact content of the canary source files.

_CANARY_EXPECTED = [
    ("config/settings.py",        2,  "api_key",             "high"),
    ("config/settings.py",        3,  "hardcoded_password",  "high"),
    ("creds/aws_config.ini",      4,  "aws_access_key_id",   "critical"),
    ("creds/aws_config.ini",      5,  "aws_secret_key",      "critical"),
    ("deploy/github_token.sh",    3,  "github_pat_classic",  "critical"),
]


# ── TestCanarySecretsFoundAllExpected ──────────────────────────────────────────

@pytest.mark.skipif(not _CANARY_PRESENT, reason="canary source not present")
class TestCanarySecretsFoundAllExpected:
    """Exact 5 findings at the precise file:line ground-truth coordinates."""

    def test_finding_count(self):
        findings = scan_secrets(_CANARY_DIR)
        assert len(findings) == 5, (
            f"Expected 5 canary findings, got {len(findings)}. "
            f"Actual: {[(f['file'], f['line_number'], f['match_type']) for f in findings]}"
        )

    def test_all_expected_findings_present(self):
        findings = scan_secrets(_CANARY_DIR)
        found_keys = {
            (f["file"], f["line_number"], f["match_type"]) for f in findings
        }
        for rel_file, lineno, match_type, _ in _CANARY_EXPECTED:
            assert (rel_file, lineno, match_type) in found_keys, (
                f"Expected finding not detected: "
                f"{rel_file}:{lineno} [{match_type}]"
            )

    def test_all_findings_have_correct_severity(self):
        findings = scan_secrets(_CANARY_DIR)
        by_key = {
            (f["file"], f["line_number"], f["match_type"]): f["severity"]
            for f in findings
        }
        for rel_file, lineno, match_type, severity in _CANARY_EXPECTED:
            key = (rel_file, lineno, match_type)
            assert by_key.get(key) == severity, (
                f"Severity mismatch for {rel_file}:{lineno} [{match_type}]: "
                f"expected {severity!r}, got {by_key.get(key)!r}"
            )

    def test_clean_c_has_no_findings(self):
        findings = scan_secrets(_CANARY_DIR)
        clean_findings = [f for f in findings if f["file"].endswith("src/clean.c")]
        assert clean_findings == [], (
            f"src/clean.c should have no findings; got: {clean_findings}"
        )

    def test_no_secret_values_in_findings(self):
        """Findings must never contain the matched secret substring."""
        findings = scan_secrets(_CANARY_DIR)
        forbidden_substrings = [
            "AKIAIOSFODNN7EXAMPLE",
            "wJalrXUtnFEMI",
            "abcdef1234567890",
            "hunter2secret",
            "ghp_AAAA",
        ]
        for f in findings:
            for substr in forbidden_substrings:
                assert substr not in str(f), (
                    f"Finding {f['file']}:{f['line_number']} contains secret value!"
                )


# ── TestCanaryAuditNoLog ───────────────────────────────────────────────────────

@pytest.mark.skipif(not _CANARY_PRESENT, reason="canary source not present")
class TestCanaryAuditNoLog:
    """Full audit pipeline with no build log — secrets only, score=0, verdict=fail."""

    def test_audit_returns_success_status(self):
        with patch("agents.guardian.ask", return_value="mocked explanation"):
            result = audit(_CANARY_DIR)
        assert result["status"] == "success"
        assert result["agent"] == "guardian"

    def test_secrets_found_count(self):
        with patch("agents.guardian.ask", return_value="mocked explanation"):
            result = audit(_CANARY_DIR)
        assert result["secrets_found"] == 5

    def test_score_is_zero(self):
        with patch("agents.guardian.ask", return_value="mocked explanation"):
            result = audit(_CANARY_DIR)
        assert result["security_score"] == 0

    def test_verdict_is_fail(self):
        with patch("agents.guardian.ask", return_value="mocked explanation"):
            result = audit(_CANARY_DIR)
        assert result["verdict"] == "fail"

    def test_hardening_status_is_skipped(self):
        with patch("agents.guardian.ask", return_value="mocked explanation"):
            result = audit(_CANARY_DIR)
        assert result["hardening_status"] == "skipped"
        assert result["build_log_checked"] is False

    def test_vulnerabilities_list_matches_findings(self):
        with patch("agents.guardian.ask", return_value="mocked explanation"):
            result = audit(_CANARY_DIR)
        secret_vulns = [v for v in result["vulnerabilities"] if v["type"] == "secret"]
        assert len(secret_vulns) == 5

    def test_remediation_code_present(self):
        with patch("agents.guardian.ask", return_value="mocked explanation"):
            result = audit(_CANARY_DIR)
        # Secrets found → remediation must mention secret remediation
        assert result["remediation_code"] != ""
        assert "git" in result["remediation_code"].lower() or "history" in result["remediation_code"].lower()


# ── TestRegressionCleanPackages ────────────────────────────────────────────────

@pytest.mark.skipif(not _HELLO_PRESENT, reason="hello-2.10 source not present")
class TestRegressionHelloClean:
    """hello-2.10 is a clean package — zero findings expected."""

    def test_no_secrets_in_hello(self):
        findings = scan_secrets(_HELLO_DIR)
        assert findings == [], (
            f"hello-2.10 should have no secret findings; got: "
            f"{[(f['file'], f['match_type']) for f in findings]}"
        )

    def test_hello_scores_100(self):
        score, verdict = _compute_score_and_verdict([], [])
        assert score == 100
        assert verdict == "pass"


@pytest.mark.skipif(not _ZLIB_PRESENT, reason="zlib-1.3 source not present")
class TestRegressionZlibClean:
    """zlib-1.3 is a clean package — zero findings expected."""

    def test_no_secrets_in_zlib(self):
        findings = scan_secrets(_ZLIB_DIR)
        assert findings == [], (
            f"zlib-1.3 should have no secret findings; got: "
            f"{[(f['file'], f['match_type']) for f in findings]}"
        )


# ── TestCurlExpectedFalsePositives ─────────────────────────────────────────────

@pytest.mark.skipif(not _CURL_PRESENT, reason="curl source not present")
class TestCurlExpectedFalsePositives:
    """
    Documents 2 known false positives in curl-8.18.0 that the secret scanner
    cannot distinguish from real secrets without value inspection.

    These are test fixture constants and documentation placeholders —
    not real credentials.  This test class serves as a regression anchor:
    if the scanner starts reporting MORE than 2 FPs in curl, something changed
    and the extra findings must be manually triaged.
    """

    _KNOWN_FPS = {
        # curl/tests/ftpserver.pl: `my $TEXT_PASSWORD = "secret"` — test fixture constant
        ("hardcoded_password",),
        # curl/docs/examples/usercertinmem.c: `-----BEGIN RSA PRIVATE KEY-----` with XXXX body
        ("private_key",),
    }

    def test_curl_has_at_most_known_false_positives(self):
        findings = scan_secrets(_CURL_DIR)
        match_types = [f["match_type"] for f in findings]
        unexpected = [
            f for f in findings
            if (f["match_type"],) not in self._KNOWN_FPS
        ]
        assert len(unexpected) == 0, (
            f"curl has unexpected secret findings beyond the 2 documented FPs: "
            f"{[(f['file'], f['line_number'], f['match_type']) for f in unexpected]}"
        )

    def test_curl_fp_count_has_not_grown(self):
        """Ensure the total finding count in curl doesn't silently grow."""
        findings = scan_secrets(_CURL_DIR)
        assert len(findings) <= 2, (
            f"curl FP count grew beyond 2 (got {len(findings)}): "
            f"{[(f['file'], f['line_number'], f['match_type']) for f in findings]}"
        )


# ── TestFixtureBuildLogs ───────────────────────────────────────────────────────

class TestFixtureBuildLogs:
    """
    Validates blhc output parsing using mocked subprocess against the synthetic
    fixture build logs in tests/fixtures/.

    The unhardened log represents a gcc invocation without any hardening flags.
    The hardened log represents a gcc invocation with all 6 flag categories.
    """

    @pytest.fixture()
    def unhardened_log(self):
        path = os.path.join(_FIXTURES_DIR, "unhardened-build.log")
        assert os.path.isfile(path), f"Fixture missing: {path}"
        return path

    @pytest.fixture()
    def hardened_log(self):
        path = os.path.join(_FIXTURES_DIR, "hardened-build.log")
        assert os.path.isfile(path), f"Fixture missing: {path}"
        return path

    def test_unhardened_log_detects_missing_flags(self, unhardened_log):
        """Mocked blhc output for unhardened log → stackprotector, fortify, format missing."""
        blhc_output = (
            "CFLAGS missing: -fstack-protector-strong (src/clean.c)\n"
            "CFLAGS missing: -D_FORTIFY_SOURCE=2 (src/clean.c)\n"
            "CFLAGS missing: -Werror=format-security (src/clean.c)\n"
        )
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = blhc_output
        mock_result.stderr = ""
        with patch("shutil.which", return_value="/usr/bin/blhc"):
            with patch("subprocess.run", return_value=mock_result):
                missing, status, raw = run_blhc(unhardened_log)
        assert status == "findings"
        assert "stackprotector" in missing
        assert "fortify" in missing
        assert "format" in missing

    def test_hardened_log_is_clean(self, hardened_log):
        """Mocked blhc exits 0 for hardened log → status=clean, no missing flags."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        with patch("shutil.which", return_value="/usr/bin/blhc"):
            with patch("subprocess.run", return_value=mock_result):
                missing, status, _ = run_blhc(hardened_log)
        assert status == "clean"
        assert missing == []

    def test_unhardened_log_all_flag_categories(self, unhardened_log):
        """Full missing-flag scenario: all 6 categories reported by blhc."""
        lines = "\n".join(
            f"CFLAGS missing: {flag} (src/clean.c)"
            for flag in _HARDENING_CATEGORIES.values()
        )
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = lines
        mock_result.stderr = ""
        with patch("shutil.which", return_value="/usr/bin/blhc"):
            with patch("subprocess.run", return_value=mock_result):
                missing, status, _ = run_blhc(unhardened_log)
        assert status == "findings"
        assert set(missing) == set(_HARDENING_CATEGORIES.keys())

    def test_score_with_unhardened_flags(self, unhardened_log):
        """Score with 3 critical secrets + 3 missing flag categories."""
        critical_findings = [{"severity": "critical"}] * 3
        missing = ["stackprotector", "fortify", "format"]
        score, verdict = _compute_score_and_verdict(critical_findings, missing)
        # 3 critical → -120 → floor 0; cap at 20; 3 flag cats → -30 → still 0
        assert score == 0
        assert verdict == "fail"

    def test_score_all_flags_missing_no_secrets(self):
        """All 6 flag categories missing, no secrets → 100 - 60 = 40 → 'warn' (not < 40)."""
        missing = list(_HARDENING_CATEGORIES.keys())  # all 6
        score, verdict = _compute_score_and_verdict([], missing)
        assert score == 40
        assert verdict == "warn"
