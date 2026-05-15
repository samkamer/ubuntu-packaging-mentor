# tests/test_guardian.py — unit tests for agents/guardian.py
import os
import sys
import tempfile
import textwrap

import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.guardian import (
    scan_secrets,
    run_blhc,
    _compute_score_and_verdict,
    _is_within,
    _safe_realpath,
    _HARDENING_CATEGORIES,
    audit,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_tree(files: dict[str, str]) -> str:
    """Create a temp dir with the given {relative_path: content} files. Returns dir path."""
    tmpdir = tempfile.mkdtemp()
    for rel, content in files.items():
        fpath = os.path.join(tmpdir, rel)
        os.makedirs(os.path.dirname(fpath), exist_ok=True)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)
    return tmpdir


# ── _is_within ─────────────────────────────────────────────────────────────────

class TestIsWithin:
    def test_exact_match(self, tmp_path):
        base = str(tmp_path)
        assert _is_within(base, base)

    def test_child_path(self, tmp_path):
        base = str(tmp_path)
        child = os.path.join(base, "subdir")
        assert _is_within(base, child)

    def test_sibling_not_within(self, tmp_path):
        base = str(tmp_path / "a")
        sibling = str(tmp_path / "b")
        assert not _is_within(base, sibling)

    def test_partial_name_not_matched(self, tmp_path):
        # /tmp/abc should not be "within" /tmp/ab
        base = str(tmp_path / "ab")
        candidate = str(tmp_path / "abc")
        assert not _is_within(base, candidate)


# ── scan_secrets — clean source ────────────────────────────────────────────────

class TestScanSecretsClean:
    def test_empty_directory(self, tmp_path):
        assert scan_secrets(str(tmp_path)) == []

    def test_plain_source_file(self, tmp_path):
        (tmp_path / "hello.c").write_text('#include <stdio.h>\nint main() { return 0; }\n')
        assert scan_secrets(str(tmp_path)) == []

    def test_example_password_in_comment_does_not_match(self, tmp_path):
        # "password" as a variable name with no quoted value — should NOT match
        (tmp_path / "auth.c").write_text("// password checking routine\nint check_password();\n")
        assert scan_secrets(str(tmp_path)) == []

    def test_skips_git_directory(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "COMMIT_EDITMSG").write_text("AKIA1234567890ABCDEF\n")
        assert scan_secrets(str(tmp_path)) == []

    def test_skips_binary_extension(self, tmp_path):
        (tmp_path / "image.png").write_bytes(b"\x89PNG\rAKIA1234567890ABCDEF")
        assert scan_secrets(str(tmp_path)) == []

    def test_skips_oversized_file(self, tmp_path):
        large = tmp_path / "large.c"
        # Write just over 1 MiB of safe content
        large.write_bytes(b"x" * (1024 * 1024 + 1))
        assert scan_secrets(str(tmp_path)) == []

    def test_nonexistent_dir_returns_empty(self):
        result = scan_secrets("/nonexistent/path/that/does/not/exist")
        # os.walk on a non-existent path produces nothing
        assert result == []


# ── scan_secrets — secret detection ───────────────────────────────────────────

class TestScanSecretsDetection:
    def test_detects_pem_private_key(self, tmp_path):
        (tmp_path / "key.pem").write_text("-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n")
        findings = scan_secrets(str(tmp_path))
        assert len(findings) == 1
        assert findings[0]["match_type"] == "private_key"
        assert findings[0]["severity"] == "critical"
        assert findings[0]["line_number"] == 1

    def test_detects_openssh_private_key(self, tmp_path):
        (tmp_path / "id_rsa").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC...\n")
        findings = scan_secrets(str(tmp_path))
        assert any(f["match_type"] == "private_key" for f in findings)

    def test_detects_aws_access_key_id(self, tmp_path):
        (tmp_path / "config.py").write_text('AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')
        findings = scan_secrets(str(tmp_path))
        assert any(f["match_type"] == "aws_access_key_id" for f in findings)
        assert findings[0]["severity"] == "critical"

    def test_detects_aws_sts_key(self, tmp_path):
        (tmp_path / "config.py").write_text('KEY = "ASIAIOSFODNN7EXAMPLE"\n')
        findings = scan_secrets(str(tmp_path))
        assert any(f["match_type"] == "aws_sts_key" for f in findings)

    def test_detects_github_pat_classic(self, tmp_path):
        pat = "ghp_" + "A" * 36
        (tmp_path / "ci.yml").write_text(f"token: {pat}\n")
        findings = scan_secrets(str(tmp_path))
        assert any(f["match_type"] == "github_pat_classic" for f in findings)

    def test_detects_hardcoded_password(self, tmp_path):
        (tmp_path / "settings.py").write_text('password = "hunter2secret"\n')
        findings = scan_secrets(str(tmp_path))
        assert any(f["match_type"] == "hardcoded_password" for f in findings)
        assert findings[0]["severity"] == "high"

    def test_detects_api_key(self, tmp_path):
        (tmp_path / "config.json").write_text('{"api_key": "abcdef1234567890abcd"}\n')
        findings = scan_secrets(str(tmp_path))
        assert any(f["match_type"] == "api_key" for f in findings)

    def test_finding_has_no_secret_content(self, tmp_path):
        """Findings must NOT expose the secret value."""
        (tmp_path / "key.pem").write_text("-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n")
        findings = scan_secrets(str(tmp_path))
        assert len(findings) == 1
        finding = findings[0]
        # Only these keys should be present — no 'snippet', 'value', 'content' etc.
        allowed_keys = {"type", "severity", "match_type", "file", "line_number"}
        assert set(finding.keys()) == allowed_keys

    def test_relative_file_path_reported(self, tmp_path):
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "secrets.py").write_text('password = "supersecret"\n')
        findings = scan_secrets(str(tmp_path))
        assert findings[0]["file"] == os.path.join("src", "secrets.py")

    def test_line_number_correct(self, tmp_path):
        content = "# comment\n# another comment\npassword = 'mysecret12345'\n"
        (tmp_path / "config.py").write_text(content)
        findings = scan_secrets(str(tmp_path))
        assert findings[0]["line_number"] == 3

    def test_only_first_match_per_line(self, tmp_path):
        # A line with two patterns — only one finding should be emitted
        (tmp_path / "multi.py").write_text('password = "secret1234" # AKIAIOSFODNN7EXAMPLE\n')
        findings = scan_secrets(str(tmp_path))
        assert len(findings) == 1

    def test_binary_content_skipped_gracefully(self, tmp_path):
        # Write valid UTF-8 file name but with binary content — UnicodeDecodeError expected
        fpath = tmp_path / "binary.c"
        fpath.write_bytes(b"\xff\xfe" + b"AKIA1234567890ABCDEF")
        # Should not raise; binary content is silently skipped
        findings = scan_secrets(str(tmp_path))
        assert findings == []


# ── scan_secrets — symlink safety ─────────────────────────────────────────────

class TestScanSecretsSymlinks:
    def test_symlink_within_tree_is_scanned(self, tmp_path):
        real_file = tmp_path / "real.py"
        real_file.write_text('password = "hunter2secret"\n')
        link = tmp_path / "link.py"
        link.symlink_to(real_file)
        # Both real and symlinked file should produce findings
        findings = scan_secrets(str(tmp_path))
        assert len(findings) == 2

    def test_symlink_escaping_tree_is_skipped(self, tmp_path):
        external = tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        )
        external.write('password = "hunter2secret"\n')
        external.close()
        try:
            link = tmp_path / "escape.py"
            link.symlink_to(external.name)
            findings = scan_secrets(str(tmp_path))
            assert findings == []
        finally:
            os.unlink(external.name)


# ── run_blhc ───────────────────────────────────────────────────────────────────

class TestRunBlhc:
    def test_returns_unknown_when_blhc_not_installed(self, tmp_path):
        log = tmp_path / "build.log"
        log.write_text("gcc -o hello hello.c\n")
        with patch("shutil.which", return_value=None):
            missing, status, raw = run_blhc(str(log))
        assert missing == []
        assert status == "unknown"
        assert raw == ""

    def test_clean_when_blhc_exits_zero(self, tmp_path):
        log = tmp_path / "build.log"
        log.write_text("gcc -fstack-protector-strong -o hello hello.c\n")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        with patch("shutil.which", return_value="/usr/bin/blhc"):
            with patch("subprocess.run", return_value=mock_result):
                missing, status, _ = run_blhc(str(log))
        assert missing == []
        assert status == "clean"

    def test_parses_missing_flags_from_blhc_output(self, tmp_path):
        log = tmp_path / "build.log"
        log.write_text("gcc -o hello hello.c\n")
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = (
            "CFLAGS missing: -fstack-protector-strong (hello.c)\n"
            "LDFLAGS missing: -Wl,-z,relro\n"
        )
        mock_result.stderr = ""
        with patch("shutil.which", return_value="/usr/bin/blhc"):
            with patch("subprocess.run", return_value=mock_result):
                missing, status, _ = run_blhc(str(log))
        assert "stackprotector" in missing
        assert "relro" in missing
        assert status == "findings"

    def test_timeout_returns_unknown(self, tmp_path):
        import subprocess as _subprocess
        log = tmp_path / "build.log"
        log.write_text("content\n")
        with patch("shutil.which", return_value="/usr/bin/blhc"):
            with patch("subprocess.run", side_effect=_subprocess.TimeoutExpired("blhc", 30)):
                missing, status, _ = run_blhc(str(log))
        assert status == "unknown"


# ── _compute_score_and_verdict ────────────────────────────────────────────────

class TestComputeScoreAndVerdict:
    def test_perfect_score_no_findings(self):
        score, verdict = _compute_score_and_verdict([], [])
        assert score == 100
        assert verdict == "pass"

    def test_critical_caps_at_20(self):
        findings = [{"severity": "critical"}]
        score, verdict = _compute_score_and_verdict(findings, [])
        assert score == min(100 - 40, 20)
        assert verdict == "fail"

    def test_multiple_critical_floor_at_zero(self):
        findings = [{"severity": "critical"}] * 5
        score, verdict = _compute_score_and_verdict(findings, [])
        assert score == 0
        assert verdict == "fail"

    def test_high_findings_reduce_score(self):
        findings = [{"severity": "high"}] * 2
        score, verdict = _compute_score_and_verdict(findings, [])
        assert score == 100 - 2 * 20
        assert verdict == "warn"

    def test_medium_findings_only_warn(self):
        findings = [{"severity": "medium"}] * 4
        score, verdict = _compute_score_and_verdict(findings, [])
        assert score == 100 - 4 * 5
        assert verdict == "pass"

    def test_missing_flags_deduct(self):
        score, verdict = _compute_score_and_verdict([], ["stackprotector", "relro", "bindnow"])
        assert score == 100 - 3 * 10
        assert verdict == "pass"

    def test_all_flags_missing_warns(self):
        score, verdict = _compute_score_and_verdict(
            [], list(_HARDENING_CATEGORIES.keys())
        )
        assert score == 100 - 6 * 10
        assert verdict == "warn"

    def test_score_floor_is_zero(self):
        findings = [{"severity": "critical"}] * 10
        flags = list(_HARDENING_CATEGORIES.keys())
        score, _ = _compute_score_and_verdict(findings, flags)
        assert score == 0

    def test_verdict_fail_below_40(self):
        findings = [{"severity": "high"}] * 4  # -80 → 20
        score, verdict = _compute_score_and_verdict(findings, [])
        assert score == 20
        assert verdict == "fail"


# ── audit() integration ────────────────────────────────────────────────────────

class TestAudit:
    def test_nonexistent_source_dir(self):
        result = audit("/nonexistent/source/dir")
        assert result["status"] == "error"
        assert result["agent"] == "guardian"
        assert "not found" in result["error"]

    def test_clean_source_no_log(self, tmp_path):
        (tmp_path / "hello.c").write_text('#include <stdio.h>\nint main(){}\n')
        with patch("agents.guardian.ask", return_value="No issues found."):
            result = audit(str(tmp_path))
        assert result["status"] == "success"
        assert result["agent"] == "guardian"
        assert result["secrets_found"] == 0
        assert result["hardening_status"] == "skipped"
        assert result["build_log_checked"] is False
        assert result["verdict"] == "pass"
        assert result["security_score"] == 100

    def test_secret_found_reduces_score(self, tmp_path):
        (tmp_path / "key.pem").write_text("-----BEGIN RSA PRIVATE KEY-----\nMIIE\n")
        with patch("agents.guardian.ask", return_value="Explanation."):
            result = audit(str(tmp_path))
        assert result["status"] == "success"
        assert result["secrets_found"] == 1
        assert result["security_score"] <= 20  # critical caps at 20
        assert result["verdict"] == "fail"

    def test_nonexistent_build_log_skipped(self, tmp_path):
        (tmp_path / "hello.c").write_text("int main(){}\n")
        with patch("agents.guardian.ask", return_value="No issues."):
            result = audit(str(tmp_path), "/nonexistent/build.log")
        assert result["build_log_checked"] is False
        assert result["hardening_status"] == "skipped"

    def test_build_log_blhc_unavailable(self, tmp_path):
        (tmp_path / "hello.c").write_text("int main(){}\n")
        log = tmp_path / "build.log"
        log.write_text("gcc -o hello hello.c\n")
        with patch("shutil.which", return_value=None):
            with patch("agents.guardian.ask", return_value="Explanation."):
                result = audit(str(tmp_path), str(log))
        assert result["build_log_checked"] is True
        assert result["hardening_status"] == "unknown"
        assert result["hardening_reason"] == "blhc_not_installed"
        assert result["missing_flags"] == []

    def test_vulnerabilities_list_has_no_secret_values(self, tmp_path):
        (tmp_path / "creds.py").write_text('password = "topsecret1234"\n')
        with patch("agents.guardian.ask", return_value="Explanation."):
            result = audit(str(tmp_path))
        for v in result["vulnerabilities"]:
            if v.get("type") == "secret":
                assert "snippet" not in v
                assert "value" not in v
                assert "content" not in v

    def test_result_keys_present(self, tmp_path):
        (tmp_path / "hello.c").write_text("int main(){}\n")
        with patch("agents.guardian.ask", return_value="OK."):
            result = audit(str(tmp_path))
        required = {
            "status", "agent", "security_score", "verdict",
            "vulnerabilities", "secrets_found", "missing_flags",
            "hardening_status", "hardening_reason", "remediation_code", "llm_explanation",
            "build_log_checked",
        }
        assert required <= set(result.keys())

    def test_llm_unavailable_does_not_fail_audit(self, tmp_path):
        # Include a finding so the LLM path is exercised
        (tmp_path / "hello.c").write_text("int main(){}\n")
        (tmp_path / "creds.py").write_text('password = "hunter2secret"\n')
        with patch("agents.guardian.ask", side_effect=RuntimeError("LLM offline")):
            result = audit(str(tmp_path))
        assert result["status"] == "success"
        assert "LLM unavailable" in result["llm_explanation"]

    def test_hardening_findings_in_vulnerabilities(self, tmp_path):
        log = tmp_path / "build.log"
        log.write_text("gcc -o hello hello.c\n")
        (tmp_path / "hello.c").write_text("int main(){}\n")
        mock_blhc = MagicMock()
        mock_blhc.returncode = 1
        mock_blhc.stdout = "CFLAGS missing: -fstack-protector-strong\nLDFLAGS missing: -Wl,-z,relro\n"
        mock_blhc.stderr = ""
        with patch("shutil.which", return_value="/usr/bin/blhc"):
            with patch("subprocess.run", return_value=mock_blhc):
                with patch("agents.guardian.ask", return_value="Explanation."):
                    result = audit(str(tmp_path), str(log))
        hardening_vulns = [v for v in result["vulnerabilities"] if v["type"] == "hardening"]
        assert len(hardening_vulns) >= 1
        assert result["hardening_status"] == "findings"
