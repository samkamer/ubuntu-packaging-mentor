# tests/test_patch_manager.py — unit tests for agents/patch_manager.py
import os
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.patch_manager import (
    _build_file_index,
    _read_file_context,
    _extract_diff,
    _SKIP_DIRS,
    _SKIP_EXTS,
    patch,
)


# ── _build_file_index ─────────────────────────────────────────────────────────

class TestBuildFileIndex:
    def test_includes_c_files(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.c").write_text("int main(){}")
        (src / "util.h").write_text("#pragma once")
        index = _build_file_index(str(tmp_path))
        assert any("main.c" in p for p in index)
        assert any("util.h" in p for p in index)

    def test_includes_python_files(self, tmp_path):
        (tmp_path / "setup.py").write_text("from setuptools import setup")
        index = _build_file_index(str(tmp_path))
        assert any("setup.py" in p for p in index)

    def test_excludes_binary_extensions(self, tmp_path):
        (tmp_path / "image.png").write_bytes(b"\x89PNG")
        (tmp_path / "archive.gz").write_bytes(b"\x1f\x8b")
        (tmp_path / "object.o").write_bytes(b"\x7fELF")
        index = _build_file_index(str(tmp_path))
        assert not any(p.endswith(".png") for p in index)
        assert not any(p.endswith(".gz") for p in index)
        assert not any(p.endswith(".o") for p in index)

    def test_excludes_skip_dirs(self, tmp_path):
        for d in ["tests", ".git", "win32", "docs"]:
            (tmp_path / d).mkdir()
            (tmp_path / d / "file.c").write_text("// skip me")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "keep.c").write_text("// keep me")
        index = _build_file_index(str(tmp_path))
        for p in index:
            parts = p.replace("\\", "/").split("/")
            assert parts[0] not in _SKIP_DIRS, f"Skipped dir leaked into index: {p}"
        assert any("keep.c" in p for p in index)

    def test_excludes_debian_dir(self, tmp_path):
        debian = tmp_path / "debian"
        debian.mkdir()
        (debian / "control").write_text("Source: mypkg\n")
        (tmp_path / "main.c").write_text("int main(){}")
        index = _build_file_index(str(tmp_path))
        assert not any(p.startswith("debian") for p in index)

    def test_returns_sorted_list(self, tmp_path):
        for name in ["z.c", "a.c", "m.py"]:
            (tmp_path / name).write_text("")
        index = _build_file_index(str(tmp_path))
        assert index == sorted(index)

    def test_returns_empty_for_empty_dir(self, tmp_path):
        assert _build_file_index(str(tmp_path)) == []

    def test_hidden_dirs_skipped(self, tmp_path):
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "secret.c").write_text("// hidden")
        index = _build_file_index(str(tmp_path))
        assert not any(".hidden" in p for p in index)


# ── _read_file_context ────────────────────────────────────────────────────────

class TestReadFileContext:
    def test_reads_file_content(self, tmp_path):
        f = tmp_path / "hello.c"
        f.write_text("int main(){}\n")
        ctx = _read_file_context(str(tmp_path), "hello.c")
        assert "int main(){}" in ctx

    def test_adds_line_numbers(self, tmp_path):
        f = tmp_path / "hello.c"
        f.write_text("line1\nline2\nline3\n")
        ctx = _read_file_context(str(tmp_path), "hello.c")
        assert "1:" in ctx
        assert "2:" in ctx

    def test_truncates_long_files(self, tmp_path):
        f = tmp_path / "big.c"
        f.write_text("\n".join(f"line {i}" for i in range(500)) + "\n")
        ctx = _read_file_context(str(tmp_path), "big.c", max_lines=10)
        assert "omitted" in ctx

    def test_returns_placeholder_for_missing_file(self, tmp_path):
        ctx = _read_file_context(str(tmp_path), "nonexistent.c")
        assert "could not read file" in ctx


# ── _extract_diff ─────────────────────────────────────────────────────────────

class TestExtractDiff:
    def test_clean_diff_passes_through(self):
        diff = (
            "--- a/src/hello.c\n"
            "+++ b/src/hello.c\n"
            "@@ -1,2 +1,3 @@\n"
            " int main(){}\n"
            "+// added\n"
        )
        result = _extract_diff(diff)
        assert result.startswith("--- a/src/hello.c")

    def test_strips_markdown_fences(self):
        diff = (
            "```diff\n"
            "--- a/src/hello.c\n"
            "+++ b/src/hello.c\n"
            "@@ -1 +1,2 @@\n"
            " int main(){}\n"
            "+// added\n"
            "```\n"
        )
        result = _extract_diff(diff)
        assert "```" not in result
        assert result.startswith("--- a/src/hello.c")

    def test_strips_prose_before_diff(self):
        diff = (
            "Here is the diff you requested:\n\n"
            "--- a/src/foo.c\n"
            "+++ b/src/foo.c\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        result = _extract_diff(diff)
        assert result.startswith("--- a/src/foo.c")
        assert "Here is" not in result

    def test_returns_input_when_no_diff_header(self):
        text = "The LLM said something completely unrelated."
        result = _extract_diff(text)
        assert result == text


# ── patch() public API ────────────────────────────────────────────────────────

class TestPatchPublicApi:
    def test_bad_directory_returns_error(self):
        result = patch("/nonexistent/path", "fix-x", "do something")
        assert result["status"] == "error"
        assert "not found" in result["error"].lower()

    def test_empty_source_tree_returns_error(self, tmp_path):
        # No patchable files at all
        result = patch(str(tmp_path), "fix-x", "do something")
        assert result["status"] == "error"
        assert "no patchable" in result["error"].lower()

    def test_dry_run_returns_dry_run_status(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "demo")
        (tmp_path / "main.c").write_text("int main(){}\n")
        result = patch(str(tmp_path), "fix-x", "Add a comment", dry_run=True)
        assert result["status"] == "dry_run"
        assert result["patch"] == "fix-x"
        assert result["file"] is not None
        assert result["written_to"] is None

    def test_dry_run_contains_diff(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "demo")
        (tmp_path / "helper.py").write_text("def greet(): pass\n")
        result = patch(str(tmp_path), "fix-greet", "Fix greeting", dry_run=True)
        assert "diff" in result
        assert result["agent"] == "patch_manager"

    def test_patch_name_without_extension(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "demo")
        (tmp_path / "run.sh").write_text("#!/bin/bash\necho hello\n")
        result = patch(str(tmp_path), "my-fix", "fix something", dry_run=True)
        assert result["status"] == "dry_run"
        # Patch name stored without .patch in dry_run
        assert result["patch"] == "my-fix"
