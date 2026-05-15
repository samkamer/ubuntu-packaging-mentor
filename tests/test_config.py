# tests/test_config.py — unit tests for agents/config.py
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import config


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Point config to a temp dir for every test."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    yield tmp_path


class TestConfigPath:
    def test_respects_xdg_config_home(self, tmp_path):
        assert str(config.config_path()).startswith(str(tmp_path))

    def test_path_ends_with_config(self):
        assert config.config_path().name == "config"

    def test_parent_is_ubu_dev_mentor(self):
        assert config.config_path().parent.name == "ubu-dev-mentor"


class TestExists:
    def test_returns_false_when_missing(self):
        assert config.exists() is False

    def test_returns_true_after_write(self):
        config.write({"llm.provider": "demo"})
        assert config.exists() is True


class TestWrite:
    def test_creates_parent_dirs(self):
        config.write({"llm.provider": "demo"})
        assert config.config_path().is_file()

    def test_none_values_omitted(self):
        config.write({"llm.provider": "demo", "tools.debuild": None})
        settings = config.load()
        assert "tools.debuild" not in settings

    def test_all_sections_written(self):
        config.write({
            "llm.provider": "ollama",
            "llm.url":      "http://localhost:11434",
            "tools.quilt":  "/usr/bin/quilt",
        })
        settings = config.load()
        assert settings["llm.provider"] == "ollama"
        assert settings["tools.quilt"] == "/usr/bin/quilt"

    def test_missing_tools_dont_break_write(self):
        settings = {
            "llm.provider": "demo",
            "tools.debuild": None,
            "tools.quilt":   None,
            "tools.lintian": "/usr/bin/lintian",
        }
        config.write(settings)
        loaded = config.load()
        assert loaded["llm.provider"] == "demo"
        assert "tools.debuild" not in loaded
        assert loaded["tools.lintian"] == "/usr/bin/lintian"

    def test_keys_without_dot_are_ignored(self):
        config.write({"nodot": "value", "llm.provider": "demo"})
        loaded = config.load()
        assert "nodot" not in loaded


class TestLoad:
    def test_returns_empty_dict_when_no_file(self):
        assert config.load() == {}

    def test_returns_empty_dict_on_corrupt_file(self, tmp_path):
        path = config.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("NOT VALID INI ][[[")
        assert config.load() == {}

    def test_roundtrip(self):
        original = {
            "llm.provider": "ollama",
            "llm.url":      "http://localhost:11434",
            "llm.model":    "gemma3:latest",
            "llm.budget":   "180",
            "tools.quilt":  "/usr/bin/quilt",
        }
        config.write(original)
        loaded = config.load()
        for k, v in original.items():
            assert loaded[k] == v


class TestGet:
    def test_returns_value(self):
        config.write({"llm.provider": "demo"})
        assert config.get("llm.provider") == "demo"

    def test_returns_default_when_missing(self):
        assert config.get("llm.provider", "fallback") == "fallback"

    def test_returns_empty_string_default(self):
        assert config.get("nonexistent") == ""
