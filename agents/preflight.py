"""
agents/preflight.py — First-run environment detection and setup

Detects installed packaging tools and LLM reachability, then writes
~/.config/ubu-dev-mentor/config with good defaults.

Called automatically on first run (no config file) and by --setup flag.
"""

import json
import os
import shutil
import sys
import urllib.request
import urllib.error

from agents import config
from agents.network import get_host_ip

# ── Tool registry ─────────────────────────────────────────────────────────────

# Maps tool binary name → apt package that provides it
TOOLS = {
    "licensecheck": "licensecheck",
    "apt-file":     "apt-file",
    "debuild":      "devscripts",
    "quilt":        "quilt",
    "lintian":      "lintian",
    "patch":        "patch",
}

# ── Colour helpers ────────────────────────────────────────────────────────────

_RESET  = "\033[0m"
_GREEN  = "\033[32m"
_RED    = "\033[31m"
_YELLOW = "\033[33m"
_CYAN   = "\033[36m"
_BOLD   = "\033[1m"

def _c(colour: str, text: str) -> str:
    return f"{colour}{text}{_RESET}"

TICK  = _c(_GREEN,  "✓")
CROSS = _c(_RED,    "✗")
SEP   = _c(_CYAN,   "━" * 42)

# ── Tool detection ────────────────────────────────────────────────────────────

def detect_tools() -> dict[str, str | None]:
    """
    Probe for each known tool with shutil.which().
    Returns {tool_name: path_or_None}.
    """
    return {name: shutil.which(name) for name in TOOLS}


def _print_tools(found: dict[str, str | None]) -> int:
    """Print tool detection results. Returns count of missing tools."""
    missing = 0
    for name, path in found.items():
        if path:
            print(f"  {TICK} {name:<16} {path}")
        else:
            apt_pkg = TOOLS[name]
            print(f"  {CROSS} {name:<16} not found  "
                  f"{_c(_YELLOW, f'→  sudo apt install {apt_pkg}')}")
            missing += 1
    return missing


# ── Ollama / LLM detection ────────────────────────────────────────────────────

_OLLAMA_PORT    = 11434
_PROBE_TIMEOUT  = 3   # seconds per probe
_DEFAULT_MODEL  = "gemma3:latest"

def _probe_ollama(base_url: str) -> dict | None:
    """
    Try to reach Ollama at base_url/api/tags.
    Returns {"url": base_url, "model": first_model} on success, None on failure.
    """
    try:
        req = urllib.request.Request(
            f"{base_url}/api/tags",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode())
            models = [m.get("name", "") for m in data.get("models", [])]
            # Prefer gemma3 if present, else first model, else default
            model = next(
                (m for m in models if m.startswith("gemma3")),
                models[0] if models else _DEFAULT_MODEL,
            )
            return {"url": base_url, "model": model}
    except Exception:
        return None


def detect_ollama() -> dict:
    """
    Probe Ollama in order: localhost → 127.0.0.1 → host gateway IP.
    Returns {"reachable": bool, "url": str, "model": str}.
    """
    candidates = [
        f"http://localhost:{_OLLAMA_PORT}",
        f"http://127.0.0.1:{_OLLAMA_PORT}",
    ]
    try:
        host_ip = get_host_ip()
        candidates.append(f"http://{host_ip}:{_OLLAMA_PORT}")
    except RuntimeError:
        pass  # no gateway — skip host IP probe

    for url in candidates:
        result = _probe_ollama(url)
        if result:
            return {"reachable": True, "url": result["url"], "model": result["model"]}

    return {
        "reachable": False,
        "url":       f"http://localhost:{_OLLAMA_PORT}",
        "model":     _DEFAULT_MODEL,
    }


def _print_llm(ollama: dict) -> str:
    """Print LLM detection results. Returns resolved provider string."""
    if ollama["reachable"]:
        print(f"  {TICK} Ollama reachable at {ollama['url']}")
        print(f"  {TICK} Model: {ollama['model']}")
        return "ollama"
    else:
        print(f"  {CROSS} Ollama not reachable — using demo mode")
        print(f"  {_c(_YELLOW, 'Hint')}: start Ollama or edit "
              f"{config.config_path()} to set [llm] url")
        return "demo"


# ── Config builder ────────────────────────────────────────────────────────────

def _build_settings(tools: dict[str, str | None], ollama: dict,
                    provider: str) -> dict:
    """Assemble the flat settings dict from detection results."""
    settings = {
        "llm.provider": provider,
        "llm.url":      ollama["url"],
        "llm.model":    ollama["model"],
        "llm.budget":   "180",
    }
    for name, path in tools.items():
        if path:
            # Config key: replace hyphens with underscores for INI validity
            key = f"tools.{name.replace('-', '_')}"
            settings[key] = path
    return settings


# ── Main entry point ──────────────────────────────────────────────────────────

def run_setup(rerun: bool = False) -> dict:
    """
    Run environment detection and write config.

    Args:
        rerun: True when triggered by --setup (vs first-run).

    Returns:
        The settings dict that was written to config.
    """
    label = "re-running setup" if rerun else "first run setup"
    print(SEP)
    print(_c(_BOLD, f"  ubu-dev-mentor — {label}"))
    print(SEP)

    # ── Tools ────────────────────────────────────────────────────────────────
    print(f"\n{_c(_BOLD, 'Detecting packaging tools...')}")
    tools   = detect_tools()
    missing = _print_tools(tools)

    # ── LLM ─────────────────────────────────────────────────────────────────
    print(f"\n{_c(_BOLD, 'Detecting LLM provider...')}")
    ollama   = detect_ollama()
    provider = _print_llm(ollama)

    # ── Write config ─────────────────────────────────────────────────────────
    settings = _build_settings(tools, ollama, provider)
    path     = config.config_path()
    print(f"\n{_c(_BOLD, 'Writing config')} to {path} ...", end=" ", flush=True)
    config.write(settings)
    print(_c(_GREEN, "done"))

    # ── Summary ──────────────────────────────────────────────────────────────
    print()
    if missing == 0:
        print(_c(_GREEN, "  All tools found. Ready to run."))
    else:
        print(_c(_YELLOW,
                 f"  {missing} tool(s) missing — some skills will be limited."))
        print(_c(_YELLOW,
                 "  Run 'ubu-dev-mentor --setup' after installing missing tools."))

    print(SEP)
    print()
    return settings
