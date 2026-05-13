#!/usr/bin/env python3
"""
mentor.py — Ubuntu AI Packaging Mentor (Orchestrator)
"""

import json
import os
import sys

from agents.brain import ask
from agents.auditor import audit as run_audit
from agents.detective import detect as run_detect
from agents.scribe import scribe as run_scribe

# ── Persona definitions ────────────────────────────────────────────────────────

PERSONAS = {
    "1": {
        "name": "Beginner",
        "description": "Explains the 'Why' — packaging concepts in plain language.",
        "system_prompt": (
            "You are a friendly Ubuntu packaging mentor. The user is a beginner. "
            "In 3-5 sentences, explain what the following packaging task does and "
            "why it matters, using simple language. Avoid jargon."
        ),
    },
    "2": {
        "name": "MOTU",
        "description": "Focuses on Ubuntu Policy and compliance.",
        "system_prompt": (
            "You are an expert Ubuntu MOTU (Masters of the Universe) mentor. "
            "In 3-5 sentences, explain what the following packaging task does, "
            "referencing the relevant Debian Policy Manual or Ubuntu Packaging Guide "
            "sections where applicable."
        ),
    },
    "3": {
        "name": "CoreDev",
        "description": "Raw output — logs, diffs, system state. No preamble.",
        "system_prompt": (
            "You are an Ubuntu Core Developer. In 2-3 sentences, describe what the "
            "following packaging task does at a technical level. Be terse. "
            "Focus on the what, not the why."
        ),
    },
}

# ── Skill definitions ──────────────────────────────────────────────────────────

SKILLS = {
    "1": {
        "name": "Audit",
        "agent": "auditor.py",
        "description": "Legal/copyright analysis — produces a DEP-5 debian/copyright file.",
        "mock_result": {
            "status": "ok",
            "data": {
                "license": "GPL-2.0-or-later",
                "copyright": "2024 Example Author <author@example.com>",
                "dep5_path": "debian/copyright",
            },
        },
    },
    "2": {
        "name": "Detect",
        "agent": "detective.py",
        "description": "Dependency discovery — resolves #include headers to Build-Depends packages.",
        "mock_result": {
            "status": "ok",
            "data": {
                "build_depends": ["libssl-dev", "zlib1g-dev", "pkg-config"],
            },
        },
    },
    "3": {
        "name": "Scribe",
        "agent": "scribe.py",
        "description": "Changelog generation — summarises git log into debian/changelog format.",
        "mock_result": {
            "status": "ok",
            "data": {
                "changelog_entry": "example (1.0-1) unstable; urgency=medium\n  * Initial packaging.\n",
            },
        },
    },
    "4": {
        "name": "Patch",
        "agent": "quilt_master.py",
        "description": "Source patching — manages quilt patch series on upstream source.",
        "mock_result": {
            "status": "ok",
            "data": {
                "patches_applied": 0,
                "series_file": "debian/patches/series",
            },
        },
    },
}

# ── Helpers ────────────────────────────────────────────────────────────────────

BANNER = """
╔══════════════════════════════════════════════════════╗
║      Ubuntu AI Packaging Mentor  (CoreDev Edition)   ║
╚══════════════════════════════════════════════════════╝
"""

RESET  = "\033[0m"
BOLD   = "\033[1m"
CYAN   = "\033[36m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"


def c(color: str, text: str) -> str:
    return f"{color}{text}{RESET}"


def print_menu(title: str, options: dict) -> None:
    print(f"\n{c(BOLD, title)}")
    for key, opt in options.items():
        name = opt["name"] if isinstance(opt, dict) else opt
        desc = f"  — {opt['description']}" if isinstance(opt, dict) and "description" in opt else ""
        print(f"  {c(CYAN, key)}) {name}{c(YELLOW, desc)}")
    print(f"  {c(CYAN, 'q')}) Quit")


def prompt(msg: str) -> str:
    try:
        return input(f"\n{c(BOLD, msg)} ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)


# ── Persona selector ───────────────────────────────────────────────────────────

def select_persona() -> dict:
    print_menu("Select your experience level:", PERSONAS)
    while True:
        choice = prompt("Enter choice [1-3]:")
        if choice in PERSONAS:
            persona = PERSONAS[choice]
            print(c(GREEN, f"\n✓ Persona set to: {persona['name']} — {persona['description']}"))
            return persona
        if choice == "q":
            sys.exit(0)
        print(c(RED, "  Invalid choice, please enter 1, 2, or 3."))


# ── Skill runner ───────────────────────────────────────────────────────────────

def run_skill(skill: dict, target: str, persona: dict) -> None:
    print(f"\n{c(CYAN, '▶')} {c(BOLD, skill['name'])} on {c(YELLOW, target)}")

    # Step 1: ask Gemma to explain what this skill does for the chosen persona
    print(c(CYAN, "\n[Mentor] Asking Gemma for context..."))
    user_prompt = (
        f"Packaging task: {skill['name']}\n"
        f"Description: {skill['description']}\n"
        f"Target directory: {target}"
    )
    try:
        explanation = ask(persona["system_prompt"], user_prompt, label=f"Asking AI: {skill['name']}")
        print(f"\n{c(GREEN, '[' + persona['name'] + ']')} {explanation.strip()}\n")
    except RuntimeError as e:
        print(c(RED, f"  [brain] {e}"))

    # Step 2: invoke real agent or fall back to mock
    is_beginner = persona["name"] == "Beginner"
    print(c(CYAN, "Calling Agent..."))
    if skill["name"] == "Audit":
        write = prompt("Write debian/copyright to target directory? [y/N]:").lower() == "y"
        result = run_audit(target, write=write, backup=is_beginner)
    elif skill["name"] == "Detect":
        write = prompt("Write Build-Depends to debian/control? [y/N]:").lower() == "y"
        result = run_detect(target, write=write, backup=is_beginner)
    elif skill["name"] == "Scribe":
        release = prompt("Target release name [noble]:") or "noble"
        write   = prompt("Prepend entry to debian/changelog? [y/N]:").lower() == "y"
        result  = run_scribe(target, release=release, write=write, backup=is_beginner)
    else:
        result = skill["mock_result"]

    # Surface errors clearly
    if result.get("status") == "error":
        print(c(RED, f"\n[Error] {result.get('error')}"))
    else:
        print(c(GREEN, "\n[Result]"))
        if skill["name"] == "Audit" and result.get("data"):
            print(c(CYAN, "\n── Generated debian/copyright ──────────────────────"))
            print(result["data"].strip())
            print(c(CYAN, "────────────────────────────────────────────────────"))
            if result.get("written_to"):
                print(c(GREEN, f"\n✓ Written to: {result['written_to']}"))
                if result.get("backed_up"):
                    print(c(YELLOW, f"  ↩ Backup saved: {result['backed_up']}"))
            else:
                print(c(YELLOW, "\n(Not written to disk — answer 'y' at the prompt to save)"))
        elif skill["name"] == "Detect" and result.get("dependencies") is not None:
            deps = result["dependencies"]
            if deps:
                print(c(CYAN, "\n── Suggested Build-Depends ─────────────────────────"))
                print("Build-Depends: " + ",\n               ".join(deps))
                print(c(CYAN, "────────────────────────────────────────────────────"))
                if result.get("written_to"):
                    print(c(GREEN, f"\n✓ Written to: {result['written_to']}"))
                    if result.get("backed_up"):
                        print(c(YELLOW, f"  ↩ Backup saved: {result['backed_up']}"))
                else:
                    print(c(YELLOW, "\n(Not written to disk — answer 'y' at the prompt to save)"))
            else:
                print(c(YELLOW, "\nNo external dependencies detected."))
        elif skill["name"] == "Scribe" and result.get("data"):
            print(c(CYAN, "\n── Generated debian/changelog entry ────────────────"))
            print(result["data"].strip())
            print(c(CYAN, "────────────────────────────────────────────────────"))
            if result.get("written_to"):
                print(c(GREEN, f"\n✓ Written to: {result['written_to']}"))
                if result.get("backed_up"):
                    print(c(YELLOW, f"  ↩ Backup saved: {result['backed_up']}"))

            else:
                print(c(YELLOW, "\n(Not written to disk — answer 'y' at the prompt to save)"))
        else:
            print(json.dumps(result, indent=2))


# ── Main loop ──────────────────────────────────────────────────────────────────

def main() -> None:
    print(c(BOLD, BANNER))

    # 1. Persona selection
    persona = select_persona()

    # 2. Target directory
    while True:
        target = prompt("Enter path to package source directory:")
        if not target:
            print(c(RED, "  Path cannot be empty."))
            continue
        if not os.path.isdir(target):
            print(c(YELLOW, f"  Warning: '{target}' does not exist or is not a directory. Continue anyway? [y/N]"))
            if prompt("").lower() != "y":
                continue
        break

    # 3. Main skill loop
    while True:
        print_menu("Select a skill:", SKILLS)
        choice = prompt("Enter choice [1-4]:")

        if choice == "q":
            print(c(GREEN, "\nGoodbye!\n"))
            break
        if choice not in SKILLS:
            print(c(RED, "  Invalid choice."))
            continue

        run_skill(SKILLS[choice], target, persona)

        again = prompt("Run another skill? [Y/n]:").lower()
        if again == "n":
            print(c(GREEN, "\nGoodbye!\n"))
            break


if __name__ == "__main__":
    main()
