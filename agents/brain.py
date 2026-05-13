#!/usr/bin/env python3
"""
agents/brain.py — Provider-based LLM utility

Selects the AI backend from the AI_PROVIDER environment variable.

  AI_PROVIDER=ollama   (default) — local Gemma via Ollama on the host gateway
  AI_PROVIDER=copilot            — GitHub Copilot via `gh copilot explain`

Public API:
    ask(system_prompt, user_prompt, label="Thinking", timeout=600) -> str
"""

import itertools
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error

import requests

# ── Provider config ───────────────────────────────────────────────────────────

AI_PROVIDER = os.environ.get("AI_PROVIDER", "ollama").lower()

# ── Ollama config (only resolved when provider is ollama) ─────────────────────

def _get_host_ip() -> str:
    """Detect the host gateway IP from the routing table."""
    result = subprocess.run(
        ["ip", "route", "show", "default"],
        capture_output=True, text=True,
    )
    parts = result.stdout.split()
    try:
        return parts[parts.index("via") + 1]
    except (ValueError, IndexError):
        raise RuntimeError(f"Could not detect default gateway: {result.stdout!r}")


if AI_PROVIDER == "ollama":
    HOST_IP   = _get_host_ip()
    OLLAMA_URL = f"http://{HOST_IP}:11434/api/generate"
    MODEL      = "gemma3:latest"
else:
    HOST_IP    = None
    OLLAMA_URL = None
    MODEL      = None

# ── Spinner ───────────────────────────────────────────────────────────────────

class Spinner:
    """Animated spinner with elapsed time, written to stderr."""

    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    EXPECTED = 120  # seconds, shown as hint before elapsed kicks in

    def __init__(self, label: str):
        self.label = label
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def _spin(self) -> None:
        start = time.time()
        for frame in itertools.cycle(self.FRAMES):
            if self._stop.is_set():
                break
            elapsed = int(time.time() - start)
            hint = f"(usually ~{self.EXPECTED}s)" if elapsed < 10 else f"elapsed: {elapsed}s"
            sys.stderr.write(f"\r  {frame} {self.label} … {hint}   ")
            sys.stderr.flush()
            time.sleep(0.1)
        sys.stderr.write("\r" + " " * 70 + "\r")
        sys.stderr.flush()

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join()


# ── Providers ─────────────────────────────────────────────────────────────────

def _ask_ollama(system_prompt: str, user_prompt: str, timeout: int) -> str:
    """POST to local Ollama and return the response text."""
    payload = {
        "model":  MODEL,
        "system": system_prompt,
        "prompt": user_prompt,
        "stream": False,
    }
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json().get("response", "")
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(f"Cannot connect to Ollama at {OLLAMA_URL}") from e
    except requests.exceptions.Timeout:
        raise RuntimeError(f"Ollama request timed out after {timeout}s")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Ollama request failed: {e}") from e


def _ask_copilot(system_prompt: str, user_prompt: str, timeout: int) -> str:
    """Call `gh copilot explain` and return the stripped response text."""
    combined = f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt
    try:
        result = subprocess.run(
            ["gh", "copilot", "explain", combined],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError("'gh' CLI not found. Install it from https://cli.github.com/")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"gh copilot explain timed out after {timeout}s")

    if result.returncode != 0:
        raise RuntimeError(
            f"gh copilot explain failed (exit {result.returncode}): {result.stderr.strip()}"
        )

    output = result.stdout.strip()
    # Strip leading "Explanation:" header if present
    output = re.sub(r"^Explanation:\s*", "", output, flags=re.IGNORECASE).strip()
    return output


# ── Public API ────────────────────────────────────────────────────────────────

def ask(system_prompt: str, user_prompt: str,
        label: str = "Thinking", timeout: int = 600) -> str:
    """
    Send a prompt to the configured AI provider and return the response text.

    Args:
        system_prompt: Role/context instructions for the model.
        user_prompt:   The actual question or task.
        label:         Spinner label shown while waiting. Pass "" to suppress.
        timeout:       Per-request timeout in seconds.

    Raises:
        RuntimeError: On connection failure, timeout, or provider error.
    """
    provider_fn = _ask_ollama if AI_PROVIDER == "ollama" else _ask_copilot

    if label:
        with Spinner(label):
            return provider_fn(system_prompt, user_prompt, timeout)
    else:
        return provider_fn(system_prompt, user_prompt, timeout)


# ── Backward-compat alias ─────────────────────────────────────────────────────

def ask_gemma(system_prompt: str, user_prompt: str,
              label: str = "Waiting for Gemma") -> str:
    """Deprecated alias for ask(). Use ask() in new code."""
    return ask(system_prompt, user_prompt, label=label)


# ── CLI self-test ─────────────────────────────────────────────────────────────

import re  # noqa: E402 (needed by _ask_copilot above, imported here for clarity)

if __name__ == "__main__":
    print(f"Provider : {AI_PROVIDER}")
    if AI_PROVIDER == "ollama":
        print(f"Host IP  : {HOST_IP}")
        print(f"URL      : {OLLAMA_URL}")
    print()
    answer = ask(
        system_prompt="You are a helpful Linux expert.",
        user_prompt="Why is Ubuntu the best Linux distro?",
        label=f"Asking {AI_PROVIDER}",
    )
    print(answer)
