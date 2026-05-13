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

# ── LLM budget ────────────────────────────────────────────────────────────────

_DEFAULT_BUDGET = 180  # seconds

def llm_budget_seconds() -> float:
    """
    Return the LLM budget in seconds for per-item calls across all agents.

    Reads the LLM_BUDGET environment variable (integer seconds).
    Defaults to 180 s (3 minutes).

    Example:
        LLM_BUDGET=600 python3 mentor.py    # 10-minute budget for large packages
    """
    raw = os.environ.get("LLM_BUDGET", "").strip()
    if raw.isdigit():
        return float(raw)
    return float(_DEFAULT_BUDGET)


# ── File backup utility ───────────────────────────────────────────────────────

import shutil as _shutil
from datetime import datetime as _datetime

def backup_file(path: str) -> str | None:
    """
    Copy *path* to *path*.<YYYYMMDD-HHMMSS>.bak if it exists.

    Returns the backup path on success, or None if the file didn't exist.
    Intended for 'Beginner' mode so learners can always recover the original.
    """
    if not os.path.isfile(path):
        return None
    stamp = _datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = f"{path}.{stamp}.bak"
    _shutil.copy2(path, backup_path)
    return backup_path


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


def _ask_demo(system_prompt: str, user_prompt: str, _timeout: int) -> str:
    """
    Instant canned responses for AI_PROVIDER=demo.
    Selects a response by matching the touchpoint phrase in the combined prompt.
    MOTU vs Beginner is distinguished by the system_prompt content.
    Used to record demo videos without waiting for a live LLM.
    """
    import time as _time
    _time.sleep(1.0)   # brief pause so the spinner is visible in the recording
    up  = user_prompt.lower()   # match on user_prompt only (more specific)
    sp  = system_prompt.lower()
    is_motu = "motu" in sp or "policy manual" in sp

    # ── Pre-skill explanations (from _EXPLAIN_PROMPTS["pre_skill"]) ───────────
    if "user is about to run the audit skill" in up:
        if is_motu:
            return (
                "Per Debian Policy §12.5 and the Ubuntu Packaging Guide, every source "
                "package must ship a machine-readable debian/copyright in DEP-5 format. "
                "The Audit skill automates running licensecheck(1) and normalising each "
                "SPDX identifier — saving you from a manual file-by-file survey before upload."
            )
        return (
            "The Audit skill scans every source file for its license and copyright "
            "holder, then produces a DEP-5 debian/copyright file — the machine-readable "
            "format Ubuntu's archive requires. Without it your package cannot be uploaded. "
            "Think of it as the legal receipt for all the code you're shipping."
        )
    if "user is about to run the detect skill" in up:
        if is_motu:
            return (
                "Debian Policy §7.6 requires every Build-Depends to be explicitly listed "
                "in debian/control. The Detect skill parses #include directives and "
                "PKG_CHECK_MODULES macros, running apt-file to resolve each to the "
                "correct -dev package — satisfying Policy without manual dependency tracing."
            )
        return (
            "Build-Depends lists every package the build system needs before it can "
            "compile your source. The Detect skill scans #include directives and "
            "autoconf macros, resolving them to Ubuntu -dev packages automatically — "
            "saving you from chasing missing headers by hand."
        )
    if "user is about to run the scribe skill" in up:
        if is_motu:
            return (
                "Debian Policy §4.4 mandates a debian/changelog entry for every upload, "
                "with the exact stanza format parsed by dpkg-parsechangelog. The Scribe "
                "skill reads your git log and produces a policy-compliant entry — "
                "ensuring the version, suite, and RFC 5322 trailer are all correct."
            )
        return (
            "debian/changelog is the official release history of your package. Each "
            "stanza records who changed what and when, and sets the version number dpkg "
            "uses. Scribe reads your git history and drafts a properly formatted entry "
            "so nothing gets lost between commits and release."
        )

    # ── Before-write guidance (from _EXPLAIN_PROMPTS["before_write"]) ─────────
    if "audit agent has finished generating a dep-5" in up:
        if is_motu:
            return (
                "Policy check: verify all SPDX identifiers are canonical "
                "(licensecheck --check-spdx), the Files: glob patterns don't overlap, "
                "and the Source: URL points to upstream. Run 'lintian --pedantic' "
                "before upload to catch any remaining copyright issues."
            )
        return (
            "Before saving: verify all copyright holders appear under their Files: "
            "stanza, License: identifiers are valid SPDX names (e.g. GPL-2.0-only), "
            "and any third-party vendored code has its own stanza."
        )
    if "detect agent has finished generating a build-depends" in up:
        if is_motu:
            return (
                "Policy check: confirm all -dev packages are in the correct "
                "Build-Depends (not Build-Depends-Indep unless arch-independent), "
                "version-constrain anything with a known ABI break, and strip "
                "anything already pulled in by debhelper transitively."
            )
        return (
            "Before saving: confirm every -dev package exists in the target release "
            "('apt-cache show <pkg>'), remove transitively-pulled duplicates, and make "
            "sure debhelper-compat is present with the right compat level."
        )
    if "scribe agent has finished generating a debian/changelog" in up:
        if is_motu:
            return (
                "Policy check: version must be higher than the last archive upload "
                "(check rmadison), suite must match the target pocket, and the "
                "' -- Maintainer <email>  Date' trailer must parse cleanly with "
                "dpkg-parsechangelog --show-field Date before you sign and upload."
            )
        return (
            "Before saving: check the version is higher than the last upload, the suite "
            "name matches your target (e.g. 'noble'), and the ' -- Name <email>  Date' "
            "trailer has exactly two spaces between the email and the date."
        )

    # ── Post-result guidance (from _EXPLAIN_PROMPTS["post_result"]) ───────────
    if "audit agent has generated the dep-5" in up:
        if is_motu:
            return (
                "Run 'lintian -i --pedantic' — look for copyright-file-contains-full-gpl-license "
                "and missing-license-paragraph-in-dep5. Next: Detect to fill Build-Depends, "
                "then test with 'sbuild --dist=noble'."
            )
        return (
            "Review for UNKNOWN license identifiers — those need manual research. "
            "Next: run 'lintian' to catch remaining issues, then move on to Detect "
            "to fill in your Build-Depends."
        )
    if "detect agent has produced the build-depends" in up:
        if is_motu:
            return (
                "Verify with 'dpkg-depcheck -d debian/rules build' inside a clean "
                "pbuilder chroot. Check for any missing Breaks/Replaces if you're "
                "splitting a package. Next: Scribe for the changelog, then sign with debsign."
            )
        return (
            "Cross-check with 'dpkg-depcheck -d debian/rules build' at build time. "
            "Next: run the Scribe skill to draft your debian/changelog entry, then "
            "test the full build with 'sbuild' or 'pbuilder'."
        )
    if "scribe agent has written the debian/changelog" in up:
        if is_motu:
            return (
                "Verify with 'dpkg-parsechangelog' — check Version, Distribution and "
                "Urgency fields. Then 'debsign -k<keyid>' and 'dput ubuntu <changes>' "
                "or request a sponsor if you don't have upload rights."
            )
        return (
            "Verify the version bumped correctly and the suite is right. "
            "Next: sign with 'debsign' and upload via 'dput' to your PPA or "
            "the Ubuntu archive."
        )

    # ── On-error guidance ─────────────────────────────────────────────────────
    if "reported an error" in up:
        return "Check that the required tool is installed and the source path is correct."

    # ── Agent internals ───────────────────────────────────────────────────────
    if "dep-5" in up or "spdx" in up or "normalize" in up or "convert the raw" in up:
        return "GPL-2.0-only"
    if "deduplic" in up:
        return '["libssl-dev", "zlib1g-dev", "libgnutls28-dev", "debhelper-compat (= 13)"]'
    if "build-depends" in up and ("apt-file" in up or "header" in up or "pkg-config" in up):
        return "libssl-dev"
    if "changelog" in up and "summarise" in up:
        return (
            "hello-package (1.0-1) noble; urgency=medium\n\n"
            "  * Initial release for Ubuntu noble.\n"
            "  * Added greeting binary with --name flag support.\n"
            "  * Included man page and bash completion script.\n\n"
            " -- Demo Maintainer <demo@ubuntu.com>  Wed, 13 May 2026 14:00:00 +0000\n"
        )

    # ── patch_manager internals ───────────────────────────────────────────────
    if "reply with only the relative path" in up:
        # Return the first .c or .py file from the index snippet in the prompt
        for line in user_prompt.splitlines():
            line = line.strip()
            if line.endswith(".c") or line.endswith(".py") or line.endswith(".sh"):
                return line
        return "src/hello.c"
    if "apply the following fix" in up:
        # Return a minimal no-op unified diff so the demo doesn't break patch(1)
        # Extract the filename from the prompt
        rel = "src/hello.c"
        for token in user_prompt.split("'"):
            if "/" in token and not token.startswith(" "):
                rel = token.strip()
                break
        return (
            f"--- a/{rel}\n"
            f"+++ b/{rel}\n"
            "@@ -1,3 +1,4 @@\n"
            " /* hello — greeting utility */\n"
            "+/* patch_manager demo patch applied */\n"
            " #include <stdio.h>\n"
            " #include <stdlib.h>\n"
        )

    return "Task completed."


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
    provider_fn = {"ollama": _ask_ollama, "copilot": _ask_copilot,
                   "demo": _ask_demo}.get(AI_PROVIDER, _ask_ollama)

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
