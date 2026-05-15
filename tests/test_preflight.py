# tests/test_preflight.py — unit tests for agents/preflight.py
import json
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import preflight, config


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    yield tmp_path


# ── detect_tools ─────────────────────────────────────────────────────────────

class TestDetectTools:
    def test_returns_dict_with_all_tool_keys(self):
        result = preflight.detect_tools()
        for tool in preflight.TOOLS:
            assert tool in result

    def test_found_tool_has_non_none_path(self, monkeypatch):
        monkeypatch.setattr(
            "shutil.which",
            lambda name: f"/usr/bin/{name}" if name == "lintian" else None,
        )
        result = preflight.detect_tools()
        assert result["lintian"] == "/usr/bin/lintian"
        assert result["debuild"] is None

    def test_missing_tool_has_none(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: None)
        result = preflight.detect_tools()
        assert all(v is None for v in result.values())

    def test_all_found_returns_all_paths(self, monkeypatch):
        monkeypatch.setattr(
            "shutil.which", lambda name: f"/usr/bin/{name}"
        )
        result = preflight.detect_tools()
        assert all(v is not None for v in result.values())


# ── detect_ollama ─────────────────────────────────────────────────────────────

class TestDetectOllama:
    def _make_response(self, models: list[str]):
        body = json.dumps({"models": [{"name": m} for m in models]}).encode()
        resp = MagicMock()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__  = MagicMock(return_value=False)
        resp.status = 200
        resp.read   = MagicMock(return_value=body)
        return resp

    def test_reachable_localhost(self, monkeypatch):
        resp = self._make_response(["gemma3:latest"])
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: resp)
        result = preflight.detect_ollama()
        assert result["reachable"] is True
        assert "gemma3" in result["model"]

    def test_prefers_gemma3_model(self, monkeypatch):
        resp = self._make_response(["llama3:latest", "gemma3:4b", "mistral:latest"])
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: resp)
        result = preflight.detect_ollama()
        assert result["model"].startswith("gemma3")

    def test_falls_back_to_first_model(self, monkeypatch):
        resp = self._make_response(["llama3:latest", "mistral:latest"])
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: resp)
        result = preflight.detect_ollama()
        assert result["model"] == "llama3:latest"

    def test_empty_model_list_uses_default(self, monkeypatch):
        resp = self._make_response([])
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: resp)
        result = preflight.detect_ollama()
        assert result["model"] == preflight._DEFAULT_MODEL

    def test_unreachable_returns_not_reachable(self, monkeypatch):
        import urllib.error
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda *a, **kw: (_ for _ in ()).throw(
                urllib.error.URLError("refused")
            ),
        )
        result = preflight.detect_ollama()
        assert result["reachable"] is False

    def test_timeout_returns_not_reachable(self, monkeypatch):
        import socket
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda *a, **kw: (_ for _ in ()).throw(
                TimeoutError("timed out")
            ),
        )
        result = preflight.detect_ollama()
        assert result["reachable"] is False

    def test_unreachable_url_is_localhost(self, monkeypatch):
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda *a, **kw: (_ for _ in ()).throw(OSError()),
        )
        result = preflight.detect_ollama()
        assert "localhost" in result["url"] or "127.0.0.1" in result["url"]


# ── run_setup ──────────────────────────────────────────────────────────────────

class TestRunSetup:
    def _make_ollama_resp(self, models=None):
        if models is None:
            models = ["gemma3:latest"]
        body = json.dumps({"models": [{"name": m} for m in models]}).encode()
        resp = MagicMock()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__  = MagicMock(return_value=False)
        resp.status = 200
        resp.read   = MagicMock(return_value=body)
        return resp

    def test_writes_config_on_first_run(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr("urllib.request.urlopen",
                            lambda *a, **kw: self._make_ollama_resp())
        settings = preflight.run_setup(rerun=False)
        assert config.exists()
        assert settings["llm.provider"] == "ollama"

    def test_writes_config_on_rerun(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr("urllib.request.urlopen",
                            lambda *a, **kw: self._make_ollama_resp())
        settings = preflight.run_setup(rerun=True)
        assert config.exists()

    def test_demo_mode_when_ollama_absent(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: None)
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda *a, **kw: (_ for _ in ()).throw(OSError()),
        )
        settings = preflight.run_setup(rerun=False)
        assert settings["llm.provider"] == "demo"

    def test_found_tools_in_config(self, monkeypatch):
        monkeypatch.setattr(
            "shutil.which",
            lambda name: f"/usr/bin/{name}" if name == "lintian" else None,
        )
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda *a, **kw: (_ for _ in ()).throw(OSError()),
        )
        settings = preflight.run_setup(rerun=False)
        assert settings.get("tools.lintian") == "/usr/bin/lintian"
        assert "tools.debuild" not in settings

    def test_returns_settings_dict(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr("urllib.request.urlopen",
                            lambda *a, **kw: self._make_ollama_resp())
        settings = preflight.run_setup(rerun=False)
        assert isinstance(settings, dict)
        assert "llm.provider" in settings
        assert "llm.url" in settings
        assert "llm.model" in settings

    def test_prints_output(self, monkeypatch, capsys):
        monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr("urllib.request.urlopen",
                            lambda *a, **kw: self._make_ollama_resp())
        preflight.run_setup(rerun=False)
        out = capsys.readouterr().out
        assert "Detecting" in out
        assert "config" in out.lower()
