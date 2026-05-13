# tests/test_brain.py — unit tests for agents/brain.py utility functions
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.brain import llm_budget_seconds, backup_file


class TestLlmBudgetSeconds:
    def test_default_is_180(self, monkeypatch):
        monkeypatch.delenv("LLM_BUDGET", raising=False)
        assert llm_budget_seconds() == 180.0

    def test_reads_env_var(self, monkeypatch):
        monkeypatch.setenv("LLM_BUDGET", "600")
        assert llm_budget_seconds() == 600.0

    def test_ignores_non_integer(self, monkeypatch):
        monkeypatch.setenv("LLM_BUDGET", "abc")
        assert llm_budget_seconds() == 180.0

    def test_ignores_empty(self, monkeypatch):
        monkeypatch.setenv("LLM_BUDGET", "")
        assert llm_budget_seconds() == 180.0

    def test_returns_float(self, monkeypatch):
        monkeypatch.setenv("LLM_BUDGET", "300")
        result = llm_budget_seconds()
        assert isinstance(result, float)


class TestBackupFile:
    def test_returns_none_when_file_missing(self, tmp_path):
        assert backup_file(str(tmp_path / "nonexistent.txt")) is None

    def test_creates_bak_file(self, tmp_path):
        f = tmp_path / "debian" / "copyright"
        f.parent.mkdir()
        f.write_text("original content", encoding="utf-8")

        bak = backup_file(str(f))

        assert bak is not None
        assert bak.endswith(".bak")
        assert os.path.isfile(bak)

    def test_bak_has_same_content(self, tmp_path):
        f = tmp_path / "control"
        f.write_text("Source: hello\n", encoding="utf-8")

        bak = backup_file(str(f))

        assert open(bak).read() == "Source: hello\n"

    def test_original_unchanged(self, tmp_path):
        f = tmp_path / "changelog"
        f.write_text("hello (1.0-1) noble;\n", encoding="utf-8")
        backup_file(str(f))

        assert f.read_text() == "hello (1.0-1) noble;\n"

    def test_bak_filename_contains_timestamp(self, tmp_path):
        f = tmp_path / "copyright"
        f.write_text("text", encoding="utf-8")
        bak = backup_file(str(f))
        # Should match pattern: <original>.<YYYYMMDD-HHMMSS>.bak
        import re
        assert re.search(r"\.\d{8}-\d{6}\.bak$", bak)

    def test_multiple_backups_do_not_clobber(self, tmp_path):
        import time
        f = tmp_path / "copyright"
        f.write_text("v1", encoding="utf-8")
        bak1 = backup_file(str(f))
        time.sleep(1)
        f.write_text("v2", encoding="utf-8")
        bak2 = backup_file(str(f))

        assert bak1 != bak2
        assert os.path.isfile(bak1)
        assert os.path.isfile(bak2)
