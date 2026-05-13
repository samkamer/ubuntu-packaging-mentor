# tests/test_integration.py — integration tests against lab/sources/hello-package
#
# These tests require external tools: licensecheck, apt-file
# They run against a real package source and check agent outputs end-to-end.
#
# Skip gracefully if tools are missing (CI without packaging tools).

import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import HELLO_SRC

# ── Skip guards ────────────────────────────────────────────────────────────────

requires_licensecheck = pytest.mark.skipif(
    shutil.which("licensecheck") is None,
    reason="licensecheck not installed",
)
requires_apt_file = pytest.mark.skipif(
    shutil.which("apt-file") is None,
    reason="apt-file not installed",
)
requires_hello = pytest.mark.skipif(
    not os.path.isdir(HELLO_SRC),
    reason=f"hello-package source not found at {HELLO_SRC}",
)


# ── Auditor integration ────────────────────────────────────────────────────────

@requires_hello
@requires_licensecheck
class TestAuditorIntegration:
    def test_audit_returns_success(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "demo")
        monkeypatch.setenv("LLM_BUDGET", "5")
        from agents.auditor import audit
        result = audit(HELLO_SRC, write=False)
        assert result["status"] == "success", f"Auditor failed: {result.get('error')}"

    def test_audit_result_has_dep5_format_header(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "demo")
        monkeypatch.setenv("LLM_BUDGET", "5")
        from agents.auditor import audit
        result = audit(HELLO_SRC, write=False)
        assert "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/" \
               in result.get("data", "")

    def test_audit_result_contains_license_section(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "demo")
        monkeypatch.setenv("LLM_BUDGET", "5")
        from agents.auditor import audit
        result = audit(HELLO_SRC, write=False)
        assert "License:" in result.get("data", "")

    def test_audit_does_not_write_without_flag(self, monkeypatch, tmp_path):
        """With write=False the copyright file must not be created."""
        import shutil as _shutil
        monkeypatch.setenv("AI_PROVIDER", "demo")
        monkeypatch.setenv("LLM_BUDGET", "5")
        # Work on a copy so we don't touch the real hello source
        src_copy = str(tmp_path / "hello-2.10")
        _shutil.copytree(HELLO_SRC, src_copy)
        copyright_path = os.path.join(src_copy, "debian", "copyright")
        # Remove so we can detect if it gets created
        if os.path.exists(copyright_path):
            os.remove(copyright_path)
        from agents.auditor import audit
        audit(src_copy, write=False)
        assert not os.path.exists(copyright_path), "copyright was written without --write"

    def test_audit_bad_directory_returns_error(self):
        from agents.auditor import audit
        result = audit("/nonexistent/path/xyz")
        assert result["status"] == "error"


# ── Detective integration ──────────────────────────────────────────────────────

@requires_hello
@requires_apt_file
class TestDetectiveIntegration:
    def test_detect_returns_success(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "demo")
        monkeypatch.setenv("LLM_BUDGET", "5")
        from agents.detective import detect
        result = detect(HELLO_SRC)
        assert result["status"] == "success", f"Detective failed: {result.get('error')}"

    def test_detect_returns_list_of_deps(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "demo")
        monkeypatch.setenv("LLM_BUDGET", "5")
        from agents.detective import detect
        result = detect(HELLO_SRC)
        deps = result.get("dependencies", [])
        assert isinstance(deps, list)

    def test_detect_includes_debhelper(self, monkeypatch):
        """hello uses autoconf → debhelper-compat must always appear."""
        monkeypatch.setenv("AI_PROVIDER", "demo")
        monkeypatch.setenv("LLM_BUDGET", "5")
        from agents.detective import detect
        result = detect(HELLO_SRC)
        deps = result.get("dependencies", [])
        combined = " ".join(deps).lower()
        assert "debhelper" in combined, f"debhelper missing from: {deps}"

    def test_detect_bad_directory_returns_error(self):
        from agents.detective import detect
        result = detect("/nonexistent/path/xyz")
        assert result["status"] == "error"


# ── Scribe integration ─────────────────────────────────────────────────────────

@requires_hello
class TestScribeIntegration:
    def test_scribe_returns_success(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "demo")
        from agents.scribe import scribe
        result = scribe(HELLO_SRC, release="noble", write=False)
        assert result["status"] == "success", f"Scribe failed: {result.get('error')}"

    def test_scribe_entry_starts_with_stanza_header(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "demo")
        from agents.scribe import scribe, _STANZA_RE
        result = scribe(HELLO_SRC, release="noble", write=False)
        first_line = result.get("data", "").splitlines()[0]
        assert _STANZA_RE.match(first_line), f"Bad stanza header: {first_line!r}"

    def test_scribe_does_not_write_without_flag(self, monkeypatch, tmp_path):
        import shutil as _shutil
        monkeypatch.setenv("AI_PROVIDER", "demo")
        src_copy = str(tmp_path / "hello-2.10")
        _shutil.copytree(HELLO_SRC, src_copy)
        changelog = os.path.join(src_copy, "debian", "changelog")
        original = open(changelog).read() if os.path.exists(changelog) else ""

        from agents.scribe import scribe
        scribe(src_copy, release="noble", write=False)

        current = open(changelog).read() if os.path.exists(changelog) else ""
        assert current == original, "changelog was modified without write=True"

    def test_scribe_bad_directory_returns_error(self):
        from agents.scribe import scribe
        result = scribe("/nonexistent/path/xyz")
        assert result["status"] == "error"
