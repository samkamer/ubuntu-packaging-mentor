# tests/test_builder.py — unit tests for agents/builder.py
import os
import sys

import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.builder import (
    _classify_error,
    _parse_llm_response,
    _regex_recovery,
    build,
    LOG_TAIL_LINES,
)


# ── _classify_error ───────────────────────────────────────────────────────────

class TestClassifyError:
    def test_unmet_build_dependencies(self):
        log = "dpkg-checkbuilddeps: error: unmet build dependencies: libssl-dev"
        assert _classify_error(log) == "missing_dependency"

    def test_cannot_find_header(self):
        log = "fatal error: openssl/ssl.h: No such file or directory"
        assert _classify_error(log) == "missing_dependency"

    def test_syntax_error(self):
        log = "src/main.c:42:5: error: expected ';' before '}'"
        assert _classify_error(log) == "syntax_error"

    def test_make_error(self):
        log = "make[2]: *** [src/CMakeFiles/hello.dir/main.c.o] Error 1"
        assert _classify_error(log) == "syntax_error"

    def test_dh_error(self):
        log = "dh_installdocs: error: debian/docs does not exist"
        assert _classify_error(log) == "packaging_mistake"

    def test_debian_rules(self):
        log = "make: *** [debian/rules:15: build] Error 2"
        assert _classify_error(log) == "packaging_mistake"

    def test_dpkg_source(self):
        log = "dpkg-source: error: cannot represent change to src/file"
        assert _classify_error(log) == "packaging_mistake"

    def test_unknown_falls_back(self):
        log = "something completely unexpected happened"
        assert _classify_error(log) == "unknown"

    def test_case_insensitive(self):
        log = "FATAL ERROR: Cannot Find Library"
        assert _classify_error(log) == "missing_dependency"


# ── _regex_recovery ───────────────────────────────────────────────────────────

class TestRegexRecovery:
    def test_missing_dependency_suggests_detective(self, tmp_path):
        result = _regex_recovery("missing_dependency", str(tmp_path), "")
        assert result["suggested_agent"] == "detective"
        assert "detective" in result["suggested_command"]
        assert result["error_type"] == "missing_dependency"

    def test_syntax_error_suggests_patch_manager(self, tmp_path):
        result = _regex_recovery("syntax_error", str(tmp_path), "")
        assert result["suggested_agent"] == "patch_manager"
        assert "patch_manager" in result["suggested_command"]

    def test_packaging_mistake_suggests_auditor(self, tmp_path):
        result = _regex_recovery("packaging_mistake", str(tmp_path), "")
        assert result["suggested_agent"] == "auditor"
        assert "auditor" in result["suggested_command"]

    def test_unknown_suggests_detective(self, tmp_path):
        result = _regex_recovery("unknown", str(tmp_path), "")
        assert result["suggested_agent"] == "detective"

    def test_reason_appended_to_analysis(self, tmp_path):
        result = _regex_recovery("unknown", str(tmp_path), "LLM timed out")
        assert "LLM timed out" in result["analysis"]

    def test_result_has_required_keys(self, tmp_path):
        result = _regex_recovery("missing_dependency", str(tmp_path), "")
        for key in ("error_type", "suggested_agent", "suggested_command", "analysis"):
            assert key in result


# ── _parse_llm_response ───────────────────────────────────────────────────────

class TestParseLlmResponse:
    _WELL_FORMED = (
        "ERROR_TYPE: missing_dependency\n"
        "AGENT: detective\n"
        "COMMAND: python3 agents/detective.py /src --write\n"
        "ANALYSIS: Missing libssl-dev from Build-Depends."
    )

    def test_parses_well_formed_response(self, tmp_path):
        result = _parse_llm_response(self._WELL_FORMED, str(tmp_path), "")
        assert result["error_type"] == "missing_dependency"
        assert result["suggested_agent"] == "detective"
        assert result["suggested_command"] == "python3 agents/detective.py /src --write"
        assert "libssl-dev" in result["analysis"]

    def test_falls_back_when_no_agent_line(self, tmp_path):
        bad = "Something went wrong but I can't tell what."
        log_tail = "dpkg-checkbuilddeps: error: unmet build dependencies: libfoo-dev"
        result = _parse_llm_response(bad, str(tmp_path), log_tail)
        # Should fall back to regex recovery based on log_tail content
        assert result["suggested_agent"] is not None
        assert result["suggested_command"] is not None

    def test_partial_response_uses_regex_fallback(self, tmp_path):
        partial = "ERROR_TYPE: syntax_error\n"  # no AGENT or COMMAND lines
        log_tail = "make[1]: *** Error 1"
        result = _parse_llm_response(partial, str(tmp_path), log_tail)
        assert result["suggested_agent"] is not None

    def test_result_always_has_required_keys(self, tmp_path):
        result = _parse_llm_response("garbage", str(tmp_path), "")
        for key in ("error_type", "suggested_agent", "suggested_command", "analysis"):
            assert key in result


# ── build() public API ────────────────────────────────────────────────────────

class TestBuildPublicApi:
    def test_bad_directory_returns_error(self):
        result = build("/nonexistent/path/xyz")
        assert result["status"] == "error"
        assert "not found" in result["error"].lower()

    def test_missing_debian_dir_returns_error(self, tmp_path):
        result = build(str(tmp_path))
        assert result["status"] == "error"
        assert "debian" in result["error"].lower()

    def test_missing_debuild_returns_error(self, tmp_path, monkeypatch):
        # Simulate debuild not installed
        import shutil as _shutil
        (tmp_path / "debian").mkdir()
        original_which = _shutil.which
        monkeypatch.setattr(
            "agents.builder.shutil.which",
            lambda cmd: None if cmd == "debuild" else original_which(cmd),
        )
        result = build(str(tmp_path))
        assert result["status"] == "error"
        assert "debuild" in result["error"].lower()

    def test_failure_result_has_suggested_agent(self, tmp_path, monkeypatch):
        """Simulate a failed debuild and verify the result structure."""
        (tmp_path / "debian").mkdir()
        import agents.builder as builder_mod
        monkeypatch.setattr(
            builder_mod, "run_debuild",
            lambda d: (1, "dpkg-checkbuilddeps: error: unmet build dependencies: libfoo-dev\n" * 25),
        )
        _mock_response = (
            "ERROR_TYPE: missing_dependency\n"
            "AGENT: detective\n"
            f"COMMAND: python3 agents/detective.py {tmp_path} --write\n"
            "ANALYSIS: Missing libfoo-dev from Build-Depends."
        )
        with patch("agents.builder.ask", return_value=_mock_response):
            result = build(str(tmp_path))
        assert result["status"] == "error"
        assert result["suggested_agent"] in ("detective", "patch_manager", "auditor")
        assert result["suggested_command"] is not None
        assert "log_tail" in result
        assert len(result["log_tail"].splitlines()) <= LOG_TAIL_LINES + 1

    def test_success_result_structure(self, tmp_path, monkeypatch):
        """Simulate a successful debuild with no .changes file found (lintian skipped)."""
        (tmp_path / "debian").mkdir()
        import agents.builder as builder_mod
        monkeypatch.setattr(
            builder_mod, "run_debuild",
            lambda d: (0, "dpkg-deb: building package 'hello' in '../hello_1.0_amd64.deb'\n"),
        )
        # No .changes file exists → lintian is skipped, lintian key is None
        monkeypatch.setattr(builder_mod, "_find_changes_file", lambda d, l: None)
        result = build(str(tmp_path))
        assert result["status"] == "success"
        assert result["message"] == "Package built successfully."
        assert "log_lines" in result
        assert result["agent"] == "builder"
        assert "lintian" in result

    def test_success_with_clean_lintian(self, tmp_path, monkeypatch):
        """Simulate a successful build where lintian also passes."""
        (tmp_path / "debian").mkdir()
        changes = tmp_path.parent / "foo_1.0_amd64.changes"
        changes.write_text("")
        import agents.builder as builder_mod
        import agents.linter as linter_mod
        monkeypatch.setattr(
            builder_mod, "run_debuild",
            lambda d: (0, "dpkg-genchanges: ...\n"),
        )
        monkeypatch.setattr(
            builder_mod, "_find_changes_file",
            lambda d, l: str(changes),
        )
        monkeypatch.setattr(
            linter_mod, "run_lintian",
            lambda t: (0, "", ""),
        )
        result = build(str(tmp_path))
        assert result["status"] == "success"
        assert result["lintian"]["status"] == "success"

    def test_success_with_lintian_errors_flips_status(self, tmp_path, monkeypatch):
        """Build succeeds but lintian finds errors → overall status becomes error."""
        (tmp_path / "debian").mkdir()
        changes = tmp_path.parent / "foo_1.0_amd64.changes"
        changes.write_text("")
        import agents.builder as builder_mod
        import agents.linter as linter_mod
        monkeypatch.setattr(builder_mod, "run_debuild", lambda d: (0, ""))
        monkeypatch.setattr(builder_mod, "_find_changes_file", lambda d, l: str(changes))
        monkeypatch.setattr(
            linter_mod, "run_lintian",
            lambda t: (1, "E: foo: no-copyright-file\n", ""),
        )
        monkeypatch.setattr(linter_mod, "ask", lambda *a, **kw: "Missing debian/copyright file.")
        result = build(str(tmp_path))
        assert result["status"] == "error"
        assert result["error_type"] == "lintian"
        assert result["lintian"]["errors"][0]["tag"] == "no-copyright-file"
        assert result["suggested_agent"] == "auditor"

    def test_log_tail_capped_at_constant(self, tmp_path, monkeypatch):
        (tmp_path / "debian").mkdir()
        import agents.builder as builder_mod
        long_log = "\n".join(f"log line {i}" for i in range(200))
        monkeypatch.setattr(builder_mod, "run_debuild", lambda d: (1, long_log))
        with patch("agents.builder.ask", return_value="ANALYSIS: build failed"):
            result = build(str(tmp_path))
        assert len(result["log_tail"].splitlines()) <= LOG_TAIL_LINES
