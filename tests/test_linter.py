# tests/test_linter.py — unit tests for agents/linter.py
import os
import sys

import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.linter import (
    _SUPPRESS_TAGS,
    lint,
    parse_lintian_output,
    run_lintian,
)


# ── parse_lintian_output ──────────────────────────────────────────────────────

class TestParseLintianOutput:
    def test_parses_error_line(self):
        out = "E: mypkg: no-copyright-file\n"
        errors, warnings = parse_lintian_output(out)
        assert len(errors) == 1
        assert errors[0]["tag"] == "no-copyright-file"
        assert errors[0]["severity"] == "E"

    def test_parses_warning_line(self):
        out = "W: mypkg: no-manual-page usr/bin/tool\n"
        errors, warnings = parse_lintian_output(out)
        assert len(warnings) == 1
        assert warnings[0]["tag"] == "no-manual-page"
        assert warnings[0]["severity"] == "W"

    def test_parses_mixed_output(self):
        out = (
            "E: mypkg: no-copyright-file\n"
            "W: mypkg: no-manual-page usr/bin/tool\n"
            "W: mypkg: initial-upload-closes-no-bugs\n"
        )
        errors, warnings = parse_lintian_output(out)
        assert len(errors) == 1
        assert len(warnings) == 2

    def test_ignores_non_tag_lines(self):
        out = (
            "Now checking mypkg...\n"
            "E: mypkg: some-error\n"
            "Finished.\n"
        )
        errors, warnings = parse_lintian_output(out)
        assert len(errors) == 1
        assert len(warnings) == 0

    def test_empty_output_returns_empty_lists(self):
        errors, warnings = parse_lintian_output("")
        assert errors == []
        assert warnings == []

    def test_detail_is_captured(self):
        out = "E: mypkg: binary-without-manpage usr/bin/foo\n"
        errors, _ = parse_lintian_output(out)
        assert "usr/bin/foo" in errors[0]["detail"]

    def test_skips_info_and_pedantic(self):
        out = (
            "I: mypkg: some-info-tag\n"
            "P: mypkg: some-pedantic-tag\n"
        )
        errors, warnings = parse_lintian_output(out)
        assert errors == []
        assert warnings == []


# ── suppress tags ─────────────────────────────────────────────────────────────

class TestSuppressTags:
    def test_initial_upload_suppressed(self):
        assert "initial-upload-closes-no-bugs" in _SUPPRESS_TAGS

    def test_groff_message_suppressed(self):
        assert "groff-message" in _SUPPRESS_TAGS


# ── lint() public API ─────────────────────────────────────────────────────────

class TestLintPublicApi:
    def test_missing_file_returns_error(self):
        result = lint("/nonexistent/path/foo.deb")
        assert result["status"] == "error"
        assert "not found" in result["error"].lower()
        assert result["agent"] == "linter"

    def test_missing_lintian_returns_error(self, tmp_path, monkeypatch):
        deb = tmp_path / "foo_1.0_all.deb"
        deb.write_bytes(b"fake")
        import shutil as _shutil
        original_which = _shutil.which
        monkeypatch.setattr(
            "agents.linter.shutil.which",
            lambda cmd: None if cmd == "lintian" else original_which(cmd),
        )
        result = lint(str(deb))
        assert result["status"] == "error"
        assert "lintian" in result["error"].lower()

    def test_result_has_required_keys(self, tmp_path, monkeypatch):
        deb = tmp_path / "foo_1.0_all.deb"
        deb.write_bytes(b"fake")
        import agents.linter as linter_mod
        monkeypatch.setattr(linter_mod, "run_lintian", lambda t: (0, "", ""))
        result = lint(str(deb))
        for key in ("status", "errors", "warnings", "analysis", "error_type", "agent"):
            assert key in result

    def test_clean_package_returns_success(self, tmp_path, monkeypatch):
        deb = tmp_path / "foo_1.0_all.deb"
        deb.write_bytes(b"fake")
        import agents.linter as linter_mod
        monkeypatch.setattr(linter_mod, "run_lintian", lambda t: (0, "", ""))
        result = lint(str(deb))
        assert result["status"] == "success"
        assert result["errors"] == []

    def test_warnings_only_returns_success(self, tmp_path, monkeypatch):
        deb = tmp_path / "foo_1.0_all.deb"
        deb.write_bytes(b"fake")
        import agents.linter as linter_mod
        monkeypatch.setattr(
            linter_mod,
            "run_lintian",
            lambda t: (1, "W: foo: no-manual-page usr/bin/foo\n", ""),
        )
        result = lint(str(deb))
        assert result["status"] == "success"
        assert len(result["warnings"]) == 1

    def test_errors_return_error_status(self, tmp_path, monkeypatch):
        deb = tmp_path / "foo_1.0_all.deb"
        deb.write_bytes(b"fake")
        import agents.linter as linter_mod
        monkeypatch.setattr(
            linter_mod,
            "run_lintian",
            lambda t: (1, "E: foo: no-copyright-file\n", ""),
        )
        with patch("agents.linter.ask", return_value="Missing copyright file — add debian/copyright."):
            result = lint(str(deb))
        assert result["status"] == "error"
        assert len(result["errors"]) == 1
        assert result["errors"][0]["tag"] == "no-copyright-file"

    def test_errors_include_analysis(self, tmp_path, monkeypatch):
        deb = tmp_path / "foo_1.0_all.deb"
        deb.write_bytes(b"fake")
        import agents.linter as linter_mod
        monkeypatch.setattr(
            linter_mod,
            "run_lintian",
            lambda t: (1, "E: foo: no-copyright-file\nE: foo: bad-distribution-in-changes-file\n", ""),
        )
        with patch("agents.linter.ask", return_value="Two errors found. Add debian/copyright and fix distribution."):
            result = lint(str(deb))
        assert result["analysis"] is not None
        assert len(result["analysis"]) > 0

    def test_tool_failure_returns_error(self, tmp_path, monkeypatch):
        deb = tmp_path / "foo_1.0_all.deb"
        deb.write_bytes(b"fake")
        import agents.linter as linter_mod
        monkeypatch.setattr(
            linter_mod,
            "run_lintian",
            lambda t: (2, "", "lintian: cannot open: no such file"),
        )
        result = lint(str(deb))
        assert result["status"] == "error"

    def test_error_type_always_lintian(self, tmp_path, monkeypatch):
        deb = tmp_path / "foo_1.0_all.deb"
        deb.write_bytes(b"fake")
        import agents.linter as linter_mod
        monkeypatch.setattr(linter_mod, "run_lintian", lambda t: (0, "", ""))
        result = lint(str(deb))
        assert result["error_type"] == "lintian"
