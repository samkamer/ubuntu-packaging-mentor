#!/usr/bin/env python3
"""
demo/run_demo_personas.py — Multi-persona contrast demo

Runs three back-to-back mentor.py sessions to showcase how the same tool
adapts its explanations to different experience levels:

  Session 1: Beginner  → Audit  (full concept explanation + what to check)
  Session 2: MOTU      → Detect (policy references + compliance checks)
  Session 3: CoreDev   → Scribe (zero preamble, just output + terse summary)

Run via:
    AI_PROVIDER=demo python3 demo/run_demo_personas.py

Or recorded:
    bash demo/record_personas.sh
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
TIMEOUT      = 120

CYAN  = "\033[1;36m"
GREEN = "\033[1;32m"
YELLOW = "\033[1;33m"
RESET = "\033[0m"
BOLD  = "\033[1m"


def slow_send(child, text: str, char_delay: float = 0.06) -> None:
    for ch in text:
        child.send(ch)
        time.sleep(char_delay)
    time.sleep(0.4)


def expect_or_die(child, pattern, label: str) -> None:
    try:
        child.expect(pattern, timeout=TIMEOUT)
    except (pexpect.TIMEOUT, pexpect.EOF) as exc:
        print(f"\n[demo] {type(exc).__name__} waiting for: {label}", file=sys.stderr)
        print(f"[demo] Last output: {child.before[-300:]!r}", file=sys.stderr)
        child.close(force=True)
        sys.exit(1)


def title_card(text: str, color: str = CYAN) -> None:
    """Print a prominent section header with a short pause."""
    width = 60
    border = "─" * width
    print(f"\n{color}{border}{RESET}")
    print(f"{color}{BOLD}  {text}{RESET}")
    print(f"{color}{border}{RESET}\n")
    time.sleep(2.0)


def spawn_mentor(env: dict) -> pexpect.spawn:
    child = pexpect.spawn(
        "python3", ["mentor.py"],
        cwd=PROJECT_ROOT,
        env=env,
        encoding="utf-8",
        timeout=TIMEOUT,
        echo=False,
    )
    child.logfile_read = sys.stdout
    return child


def session_beginner_audit(env: dict) -> None:
    """Session 1: Beginner persona → Audit skill (view only, no write)."""
    title_card("SESSION 1 of 3 — Beginner Persona → Audit Skill")
    print(f"{YELLOW}Watch how the mentor explains concepts in plain language.{RESET}\n")
    time.sleep(1.5)

    child = spawn_mentor(env)

    expect_or_die(child, r"Enter choice \[1-3\]", "persona menu")
    time.sleep(0.8)
    slow_send(child, "1\n")   # Beginner

    expect_or_die(child, r"Enter path to package source", "target dir prompt")
    time.sleep(0.8)
    slow_send(child, f"{SOURCE_DIR}\n")

    expect_or_die(child, r"Enter choice \[1-4\]", "skill menu")
    time.sleep(1.0)
    slow_send(child, "1\n")   # Audit

    expect_or_die(child, r"Write debian/copyright.*\[y/N\]", "Audit write prompt")
    time.sleep(1.2)
    slow_send(child, "n\n")   # view only — don't write

    expect_or_die(child, r"Run another skill\?", "run again prompt")
    time.sleep(1.0)
    slow_send(child, "n\n")   # exit this session

    child.expect(pexpect.EOF, timeout=15)
    child.close()
    time.sleep(1.5)


def session_motu_detect(env: dict) -> None:
    """Session 2: MOTU persona → Detect skill (view only)."""
    title_card("SESSION 2 of 3 — MOTU Persona → Detect Skill", YELLOW)
    print(f"{YELLOW}Notice the policy references (Debian Policy §7.6) in the guidance.{RESET}\n")
    time.sleep(1.5)

    child = spawn_mentor(env)

    expect_or_die(child, r"Enter choice \[1-3\]", "persona menu")
    time.sleep(0.8)
    slow_send(child, "2\n")   # MOTU

    expect_or_die(child, r"Enter path to package source", "target dir prompt")
    time.sleep(0.8)
    slow_send(child, f"{SOURCE_DIR}\n")

    expect_or_die(child, r"Enter choice \[1-4\]", "skill menu")
    time.sleep(1.0)
    slow_send(child, "2\n")   # Detect

    expect_or_die(child, r"Write Build-Depends.*\[y/N\]", "Detect write prompt")
    time.sleep(1.2)
    slow_send(child, "n\n")   # view only

    expect_or_die(child, r"Run another skill\?", "run again prompt")
    time.sleep(1.0)
    slow_send(child, "n\n")

    child.expect(pexpect.EOF, timeout=15)
    child.close()
    time.sleep(1.5)


def session_coredev_scribe(env: dict) -> None:
    """Session 3: CoreDev persona → Scribe skill (view only)."""
    title_card("SESSION 3 of 3 — CoreDev Persona → Scribe Skill", GREEN)
    print(f"{YELLOW}CoreDev: no preamble, no explanations — straight to output.{RESET}\n")
    time.sleep(1.5)

    child = spawn_mentor(env)

    expect_or_die(child, r"Enter choice \[1-3\]", "persona menu")
    time.sleep(0.8)
    slow_send(child, "3\n")   # CoreDev

    expect_or_die(child, r"Enter path to package source", "target dir prompt")
    time.sleep(0.8)
    slow_send(child, f"{SOURCE_DIR}\n")

    expect_or_die(child, r"Enter choice \[1-4\]", "skill menu")
    time.sleep(1.0)
    slow_send(child, "3\n")   # Scribe

    expect_or_die(child, r"Target release name", "Scribe release prompt")
    time.sleep(0.5)
    slow_send(child, "\n")    # accept default: noble

    expect_or_die(child, r"Prepend entry.*\[y/N\]", "Scribe write prompt")
    time.sleep(1.2)
    slow_send(child, "n\n")   # view only

    expect_or_die(child, r"Run another skill\?", "run again prompt")
    time.sleep(1.0)
    slow_send(child, "n\n")

    child.expect(pexpect.EOF, timeout=15)
    child.close()
    time.sleep(1.0)


def run() -> None:
    env = os.environ.copy()
    env["AI_PROVIDER"] = "demo"
    env["LLM_BUDGET"]  = "30"
    env["PYTHONPATH"]  = PROJECT_ROOT

    print(f"\n{CYAN}{'═' * 60}{RESET}")
    print(f"{CYAN}{BOLD}  Ubuntu AI Packaging Mentor — Persona Contrast Demo{RESET}")
    print(f"{CYAN}  Beginner · MOTU · CoreDev — same tools, different depth{RESET}")
    print(f"{CYAN}{'═' * 60}{RESET}\n")
    time.sleep(2.0)

    session_beginner_audit(env)
    session_motu_detect(env)
    session_coredev_scribe(env)

    print(f"\n{GREEN}{'═' * 60}{RESET}")
    print(f"{GREEN}{BOLD}  Demo complete — three personas, three skills.{RESET}")
    print(f"{GREEN}{'═' * 60}{RESET}\n")


if __name__ == "__main__":
    run()
