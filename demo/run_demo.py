#!/usr/bin/env python3
"""
demo/run_demo.py — Scripted interactive demo of Ubuntu AI Packaging Mentor

Drives mentor.py through a full Beginner workflow using pexpect:
  1. Select Beginner persona
  2. Target: lab/sources/hello-package
  3. Run Audit  → view copyright → write
  4. Run Detect → view Build-Depends → write
  5. Run Scribe → view changelog  → write
  6. Quit

Run via:
    AI_PROVIDER=demo python3 demo/run_demo.py

Or recorded:
    asciinema rec demo/demo.cast --command "AI_PROVIDER=demo python3 demo/run_demo.py"
"""

import os
import sys
import time

try:
    import pexpect
except ImportError:
    print("pexpect required: pip install pexpect", file=sys.stderr)
    sys.exit(1)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_DIR   = "lab/sources/hello-package"

# Generous timeout — licensecheck + apt-file can be slow on first run
TIMEOUT = 120


def slow_send(child, text: str, char_delay: float = 0.06) -> None:
    """Type text character by character for a realistic 'human typing' effect."""
    for ch in text:
        child.send(ch)
        time.sleep(char_delay)
    time.sleep(0.4)


def expect_or_die(child, pattern, label: str) -> None:
    """Expect a pattern; print a diagnostic and exit if it times out."""
    try:
        child.expect(pattern, timeout=TIMEOUT)
    except pexpect.TIMEOUT:
        print(f"\n[demo] TIMEOUT waiting for: {label}", file=sys.stderr)
        print(f"[demo] Last output: {child.before[-200:]!r}", file=sys.stderr)
        child.close(force=True)
        sys.exit(1)
    except pexpect.EOF:
        print(f"\n[demo] EOF waiting for: {label}", file=sys.stderr)
        child.close(force=True)
        sys.exit(1)


def run() -> None:
    env = os.environ.copy()
    env["AI_PROVIDER"] = "demo"
    env["LLM_BUDGET"]  = "30"
    env["PYTHONPATH"]  = PROJECT_ROOT

    print("\033[1;36m── Ubuntu AI Packaging Mentor — Live Demo ──\033[0m\n", flush=True)
    time.sleep(1)

    child = pexpect.spawn(
        "python3", ["mentor.py"],
        cwd=PROJECT_ROOT,
        env=env,
        encoding="utf-8",
        timeout=TIMEOUT,
        echo=False,        # don't double-echo our input
    )
    child.logfile_read = sys.stdout   # stream mentor.py output to terminal

    # ── Persona: Beginner ─────────────────────────────────────────────────────
    expect_or_die(child, r"Enter choice \[1-3\]", "persona menu")
    time.sleep(0.8)
    slow_send(child, "1\n")   # Beginner

    # ── Target directory ──────────────────────────────────────────────────────
    expect_or_die(child, r"Enter path to package source", "target dir prompt")
    time.sleep(0.8)
    slow_send(child, f"{SOURCE_DIR}\n")

    # ── Skill 1: Audit ────────────────────────────────────────────────────────
    expect_or_die(child, r"Enter choice \[1-6\]", "skill menu (1)")
    time.sleep(1.0)
    slow_send(child, "1\n")   # Audit

    expect_or_die(child, r"Write debian/copyright.*\[y/N\]", "Audit write prompt")
    time.sleep(1.0)
    slow_send(child, "y\n")

    expect_or_die(child, r"Run another skill\?", "run again (after Audit)")
    time.sleep(0.8)
    slow_send(child, "y\n")   # continue to Detect

    # ── Skill 2: Detect ───────────────────────────────────────────────────────
    expect_or_die(child, r"Enter choice \[1-6\]", "skill menu (2)")
    time.sleep(1.5)
    slow_send(child, "2\n")   # Detect

    expect_or_die(child, r"Write Build-Depends.*\[y/N\]", "Detect write prompt")
    time.sleep(1.0)
    slow_send(child, "y\n")

    expect_or_die(child, r"Run another skill\?", "run again (after Detect)")
    time.sleep(0.8)
    slow_send(child, "y\n")   # continue to Scribe

    # ── Skill 3: Scribe ───────────────────────────────────────────────────────
    expect_or_die(child, r"Enter choice \[1-6\]", "skill menu (3)")
    time.sleep(1.5)
    slow_send(child, "3\n")   # Scribe

    expect_or_die(child, r"Target release name", "Scribe release prompt")
    time.sleep(0.5)
    slow_send(child, "\n")    # accept default: noble

    expect_or_die(child, r"Prepend entry.*\[y/N\]", "Scribe write prompt")
    time.sleep(1.0)
    slow_send(child, "y\n")

    expect_or_die(child, r"Run another skill\?", "run again (after Scribe)")
    time.sleep(1.5)
    slow_send(child, "n\n")   # done — exit gracefully

    child.expect(pexpect.EOF, timeout=15)
    child.close()

    print("\n\033[1;32m── Demo complete ──\033[0m\n", flush=True)


if __name__ == "__main__":
    run()
