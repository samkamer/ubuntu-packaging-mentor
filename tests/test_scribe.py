# tests/test_scribe.py — unit tests for agents/scribe.py
import os
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.scribe import (
    _get_package_name,
    _get_last_version,
    _get_git_log,
    _extract_bullets,
    _validate_entry,
    _build_stub,
    _STANZA_RE,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def pkg_tree(tmp_path):
    """Minimal debian/ tree: control + changelog."""
    debian = tmp_path / "debian"
    debian.mkdir()
    (debian / "control").write_text(
        "Source: coolpkg\nBuild-Depends: debhelper-compat (= 13)\n",
        encoding="utf-8",
    )
    (debian / "changelog").write_text(
        "coolpkg (2.3-4) noble; urgency=medium\n\n"
        "  * Something was fixed.\n\n"
        " -- Dev <dev@example.com>  Mon, 01 Jan 2024 00:00:00 +0000\n",
        encoding="utf-8",
    )
    return tmp_path


# ── _get_package_name ─────────────────────────────────────────────────────────

class TestGetPackageName:
    def test_reads_source_from_control(self, pkg_tree):
        assert _get_package_name(str(pkg_tree)) == "coolpkg"

    def test_falls_back_to_dirname(self, tmp_path):
        # No debian/control present
        assert _get_package_name(str(tmp_path)) == tmp_path.name

    def test_handles_missing_source_line(self, tmp_path):
        (tmp_path / "debian").mkdir()
        (tmp_path / "debian" / "control").write_text(
            "Package: binarypkg\n", encoding="utf-8"
        )
        # No Source: line — should fall back to dirname
        assert _get_package_name(str(tmp_path)) == tmp_path.name


# ── _get_last_version ─────────────────────────────────────────────────────────

class TestGetLastVersion:
    def test_bumps_debian_revision(self, pkg_tree):
        version = _get_last_version(str(pkg_tree))
        assert version == "2.3-5"  # 4 → 5

    def test_returns_default_when_no_changelog(self, tmp_path):
        assert _get_last_version(str(tmp_path)) == "1.0-1"

    def test_handles_non_numeric_revision(self, tmp_path):
        debian = tmp_path / "debian"
        debian.mkdir()
        (debian / "changelog").write_text(
            "mypkg (1.0ubuntu1) noble; urgency=medium\n\n * x\n\n -- A <a@b.com>  Mon, 01 Jan 2024 00:00:00 +0000\n",
            encoding="utf-8",
        )
        version = _get_last_version(str(tmp_path))
        # Non-digit suffix: just append -1
        assert version == "1.0ubuntu1-1"


# ── _get_git_log ──────────────────────────────────────────────────────────────

class TestGetGitLog:
    def test_returns_empty_when_no_dot_git(self, tmp_path):
        commits = _get_git_log(str(tmp_path))
        assert commits == []

    def test_returns_list_when_git_present(self, tmp_path):
        # Initialise a real git repo so _get_git_log can run
        import subprocess
        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)
        (tmp_path / "README").write_text("hello")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(tmp_path), capture_output=True)

        commits = _get_git_log(str(tmp_path))
        assert isinstance(commits, list)
        assert len(commits) >= 1
        assert "Initial commit" in commits[0]

    def test_does_not_walk_to_parent(self, tmp_path):
        """Source dir without .git must not fall back to parent repo's history."""
        child = tmp_path / "package-src"
        child.mkdir()
        commits = _get_git_log(str(child))
        assert commits == []


# ── _extract_bullets ──────────────────────────────────────────────────────────

class TestExtractBullets:
    def test_extracts_star_bullets(self):
        text = "* Fix CVE-1234\n* Bump version\n"
        bullets = _extract_bullets(text)
        assert bullets == ["Fix CVE-1234", "Bump version"]

    def test_extracts_dash_bullets(self):
        text = "- Fix CVE-1234\n- Bump version\n"
        assert _extract_bullets(text) == ["Fix CVE-1234", "Bump version"]

    def test_ignores_non_bullet_lines(self):
        text = "Some header\n* A bullet\nSome footer\n"
        assert _extract_bullets(text) == ["A bullet"]

    def test_returns_empty_for_no_bullets(self):
        assert _extract_bullets("No bullets here.") == []


# ── _validate_entry ───────────────────────────────────────────────────────────

VALID_STANZA = textwrap.dedent("""\
    coolpkg (2.3-5) noble; urgency=medium

      * Fix a bug.

     -- Dev <dev@example.com>  Mon, 01 Jan 2024 00:00:00 +0000
""")

_COMMON_ARGS = dict(
    pkg="coolpkg", version="2.3-5", release="noble",
    name="Dev", email="dev@example.com",
    rfc_date="Mon, 01 Jan 2024 00:00:00 +0000",
)


class TestValidateEntry:
    def test_valid_stanza_passes_through(self):
        result = _validate_entry(VALID_STANZA, **_COMMON_ARGS)
        assert result.startswith("coolpkg (2.3-5) noble")
        assert "Fix a bug." in result

    def test_bad_header_rescued_via_bullets(self):
        bad = "Some explanation\n* Fixed the thing\n* Updated deps\n"
        result = _validate_entry(bad, **_COMMON_ARGS)
        assert _STANZA_RE.match(result.splitlines()[0])
        assert "Fixed the thing" in result

    def test_no_bullets_produces_stub(self):
        result = _validate_entry("completely unrelated text", **_COMMON_ARGS)
        assert _STANZA_RE.match(result.splitlines()[0])
        assert "Initial release." in result


# ── _build_stub ───────────────────────────────────────────────────────────────

class TestBuildStub:
    def test_stub_header_matches_stanza_regex(self):
        stub = _build_stub("mypkg", "1.0-1", "noble",
                           "Dev", "dev@example.com",
                           "Mon, 01 Jan 2024 00:00:00 +0000")
        assert _STANZA_RE.match(stub.splitlines()[0])

    def test_stub_with_custom_items(self):
        stub = _build_stub("mypkg", "1.0-1", "noble",
                           "Dev", "dev@example.com",
                           "Mon, 01 Jan 2024 00:00:00 +0000",
                           items=["Fix CVE-9999", "Update deps"])
        assert "Fix CVE-9999" in stub
        assert "Update deps" in stub

    def test_stub_contains_trailer(self):
        stub = _build_stub("mypkg", "1.0-1", "noble",
                           "Dev", "dev@example.com",
                           "Mon, 01 Jan 2024 00:00:00 +0000")
        assert " -- Dev <dev@example.com>" in stub
