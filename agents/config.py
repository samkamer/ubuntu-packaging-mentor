"""
agents/config.py — Persistent user configuration for ubu-dev-mentor

Config file location (INI format):
    $XDG_CONFIG_HOME/ubu-dev-mentor/config
    or ~/.config/ubu-dev-mentor/config

Sections:
    [llm]    provider, url, model, budget
    [tools]  one key per detected tool, value = absolute path
"""

import configparser
import os
from pathlib import Path

# ── Config path ───────────────────────────────────────────────────────────────

def _config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "ubu-dev-mentor"


def config_path() -> Path:
    return _config_dir() / "config"


# ── Read ──────────────────────────────────────────────────────────────────────

def exists() -> bool:
    return config_path().is_file()


def load() -> dict:
    """
    Read the config file and return a flat dict of all settings.
    Returns an empty dict if the file is absent or unparseable.
    Keys are namespaced as '<section>.<option>', e.g. 'llm.provider'.
    """
    path = config_path()
    if not path.is_file():
        return {}

    parser = configparser.ConfigParser()
    try:
        parser.read(str(path), encoding="utf-8")
    except configparser.Error:
        return {}

    result = {}
    for section in parser.sections():
        for key, value in parser.items(section):
            result[f"{section}.{key}"] = value
    return result


# ── Write ─────────────────────────────────────────────────────────────────────

def write(settings: dict) -> None:
    """
    Write settings to the config file.

    settings is a flat dict with keys like 'llm.provider', 'tools.debuild'.
    Missing-tool entries (None values) are silently omitted.
    """
    parser = configparser.ConfigParser()

    for namespaced_key, value in settings.items():
        if value is None:
            continue
        if "." not in namespaced_key:
            continue
        section, _, key = namespaced_key.partition(".")
        if not parser.has_section(section):
            parser.add_section(section)
        parser.set(section, key, str(value))

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        parser.write(fh)


# ── Convenience accessors ─────────────────────────────────────────────────────

def get(key: str, default: str = "") -> str:
    """Return a single config value by namespaced key, or default."""
    return load().get(key, default)
