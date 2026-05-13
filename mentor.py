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
from agents.patch_manager import patch as run_patch

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
        "agent": "patch_manager.py",
        "description": "Source patching — generates and applies a quilt patch via LLM.",
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


# ── Persona-aware LLM narration ────────────────────────────────────────────────

# Per-skill, per-touchpoint prompts fed to the persona's system_prompt
_EXPLAIN_PROMPTS = {
    "pre_skill": {
        "Audit": (
            "The user is about to run the Audit skill on a source package directory.\n"
            "The tool will run licensecheck, parse every source file's license and "
            "copyright holder, then use AI to produce a DEP-5 debian/copyright file.\n"
            "Explain this process and its importance in Ubuntu/Debian packaging."
        ),
        "Detect": (
            "The user is about to run the Detect skill on a source package directory.\n"
            "The tool scans C/C++ headers, Python imports, Go modules, and autoconf "
            "macros to build a list of Ubuntu Build-Depends packages for debian/control.\n"
            "Explain what Build-Depends is, why it matters, and what this tool does."
        ),
        "Scribe": (
            "The user is about to run the Scribe skill on a source package directory.\n"
            "The tool reads git commit history and uses AI to write a properly formatted "
            "debian/changelog entry with the correct stanza structure.\n"
            "Explain the debian/changelog format and why it matters for packaging."
        ),
        "Patch": (
            "The user is about to run the Patch skill on a source package directory.\n"
            "The tool uses AI to identify which source file needs changing, generate a "
            "unified diff, and apply it as a new quilt patch in debian/patches/.\n"
            "Explain what quilt patches are, why Debian packages use them, and what this tool does."
        ),
    },
    "before_write": {
        "Audit": (
            "The Audit agent has finished generating a DEP-5 debian/copyright file.\n"
            "The user is about to decide whether to save it to debian/copyright.\n"
            "Explain what this file is for, what they should review before saving, "
            "and any common mistakes to watch out for."
        ),
        "Detect": (
            "The Detect agent has finished generating a Build-Depends list for debian/control.\n"
            "The user is about to decide whether to save it.\n"
            "Explain what Build-Depends does at build time, what they should verify "
            "in the list before saving, and any common pitfalls."
        ),
        "Scribe": (
            "The Scribe agent has finished generating a debian/changelog entry.\n"
            "The user is about to decide whether to prepend it to debian/changelog.\n"
            "Explain the changelog format rules, what to review, and why the trailer "
            "line format must be exact."
        ),
        "Patch": (
            "The Patch agent is about to generate and apply a quilt patch.\n"
            "The user provided a patch name and a description of the fix.\n"
            "Explain how quilt tracks patches in debian/patches/, what 'quilt refresh' does, "
            "and what to check after the patch is applied."
        ),
    },
    "post_result": {
        "Audit": (
            "The Audit agent has generated the DEP-5 debian/copyright file shown above.\n"
            "Explain what the user should review in this file, what the key sections mean, "
            "and what their next packaging step should be."
        ),
        "Detect": (
            "The Detect agent has produced the Build-Depends list shown above.\n"
            "Explain what the user should verify in this list, what each type of package "
            "does at build time, and what their next packaging step should be."
        ),
        "Scribe": (
            "The Scribe agent has written the debian/changelog entry shown above.\n"
            "Explain what the user should review in this entry, whether the version and "
            "release look correct, and what their next packaging step should be."
        ),
        "Patch": (
            "The Patch agent has applied the quilt patch shown above.\n"
            "Explain what the user should verify in the generated patch file, how to test "
            "the change builds correctly, and how to include it in a source package upload."
        ),
    },
    "on_error": {
        "Audit":  "The Audit agent reported an error running licensecheck on the package source.",
        "Detect": "The Detect agent reported an error scanning for Build-Depends.",
        "Scribe": "The Scribe agent reported an error generating the changelog entry.",
        "Patch":  "The Patch agent reported an error generating or applying the quilt patch.",
    },
}

# CoreDev post-result one-liners (no LLM call needed)
_COREDEV_SUMMARY = {
    "Audit":  lambda r: f"debian/copyright generated ({len(r.get('data','').splitlines())} lines).",
    "Detect": lambda r: f"{len(r.get('dependencies', []))} Build-Depends resolved.",
    "Scribe": lambda r: "changelog entry drafted.",
    "Patch":  lambda r: f"patch {r.get('patch','')} applied to {r.get('file','')}.",
}


def _show_write_status(result: dict) -> None:
    """Print written_to / backed_up / not-written messages."""
    if result.get("written_to"):
        print(c(GREEN, f"\n✓ Written to: {result['written_to']}"))
        if result.get("backed_up"):
            print(c(YELLOW, f"  ↩ Backup saved: {result['backed_up']}"))
    else:
        print(c(YELLOW, "\n(Not written to disk — answer 'y' at the prompt to save)"))


def _persona_explain(touchpoint: str, skill_name: str, persona: dict,
                     extra: str = "", label: str = "Thinking") -> None:
    """
    Emit a persona-appropriate LLM explanation at a named touchpoint.

    CoreDev: skipped entirely — no LLM call, no output.
    MOTU/Beginner: calls ask() with the touchpoint prompt + persona system_prompt.
    extra: appended to the prompt (e.g. error text or result summary).
    """
    if persona["name"] == "CoreDev":
        return

    base = _EXPLAIN_PROMPTS.get(touchpoint, {}).get(skill_name, "")
    if not base:
        return

    context = base + (f"\n\nAdditional context: {extra}" if extra else "")
    try:
        text = ask(persona["system_prompt"], context, label=label)
        print(f"\n{c(GREEN, '[' + persona['name'] + ']')} {text.strip()}\n")
    except RuntimeError:
        pass  # explanations are non-fatal


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
    sname = skill["name"]
    print(f"\n{c(CYAN, '▶')} {c(BOLD, sname)} on {c(YELLOW, target)}")

    # ── Touchpoint 1: pre-skill explanation ────────────────────────────────────
    # CoreDev: skipped.  MOTU: policy refs.  Beginner: full concept explanation.
    _persona_explain("pre_skill", sname, persona, label=f"{sname}: context")

    # ── Touchpoint 2: before-write prompt ──────────────────────────────────────
    # Shown BEFORE asking the user whether to save — explains what the file does.
    is_beginner = persona["name"] == "Beginner"
    is_coredev  = persona["name"] == "CoreDev"
    print(c(CYAN, "\nCalling Agent..."))

    if sname == "Audit":
        _persona_explain("before_write", sname, persona, label="Audit: before save")
        write  = prompt("Write debian/copyright to target directory? [y/N]:").lower() == "y"
        result = run_audit(target, write=write, backup=is_beginner)

    elif sname == "Detect":
        _persona_explain("before_write", sname, persona, label="Detect: before save")
        write  = prompt("Write Build-Depends to debian/control? [y/N]:").lower() == "y"
        result = run_detect(target, write=write, backup=is_beginner)

    elif sname == "Scribe":
        _persona_explain("before_write", sname, persona, label="Scribe: before save")
        release = prompt("Target release name [noble]:") or "noble"
        write   = prompt("Prepend entry to debian/changelog? [y/N]:").lower() == "y"
        result  = run_scribe(target, release=release, write=write, backup=is_beginner)

    elif sname == "Patch":
        _persona_explain("before_write", sname, persona, label="Patch: context")
        patch_name  = prompt("Patch name (e.g. fix-greeting-logic):")
        description = prompt("Describe the fix in plain English:")
        dry_run     = prompt("Dry run only — preview diff without applying? [y/N]:").lower() == "y"
        result      = run_patch(target, patch_name, description, dry_run=dry_run)

    else:
        result = {"status": "error", "error": f"Unknown skill: {sname}",
                  "agent": sname.lower()}

    # ── Error path ─────────────────────────────────────────────────────────────
    if result.get("status") == "error":
        err = result.get("error", "Unknown error")
        print(c(RED, f"\n[Error] {err}"))
        # Touchpoint 3a: on-error explanation
        # Beginner/MOTU get LLM guidance; CoreDev sees the raw message only.
        _persona_explain("on_error", sname, persona, extra=err, label="Error: guidance")
        return

    # ── Success path ───────────────────────────────────────────────────────────
    print(c(GREEN, "\n[Result]"))

    if sname == "Audit" and result.get("data"):
        print(c(CYAN, "\n── Generated debian/copyright ──────────────────────"))
        print(result["data"].strip())
        print(c(CYAN, "────────────────────────────────────────────────────"))
        _show_write_status(result)
        if is_coredev:
            print(c(CYAN, f"  {_COREDEV_SUMMARY['Audit'](result)}"))

    elif sname == "Detect" and result.get("dependencies") is not None:
        deps = result["dependencies"]
        if deps:
            print(c(CYAN, "\n── Suggested Build-Depends ─────────────────────────"))
            print("Build-Depends: " + ",\n               ".join(deps))
            print(c(CYAN, "────────────────────────────────────────────────────"))
            _show_write_status(result)
            if is_coredev:
                print(c(CYAN, f"  {_COREDEV_SUMMARY['Detect'](result)}"))
        else:
            print(c(YELLOW, "\nNo external dependencies detected."))

    elif sname == "Scribe" and result.get("data"):
        print(c(CYAN, "\n── Generated debian/changelog entry ────────────────"))
        print(result["data"].strip())
        print(c(CYAN, "────────────────────────────────────────────────────"))
        _show_write_status(result)
        if is_coredev:
            print(c(CYAN, f"  Scribe: {_COREDEV_SUMMARY['Scribe'](result)}"))

    elif sname == "Patch":
        status = result.get("status")
        if status == "dry_run":
            print(c(CYAN, "\n── Dry run — diff preview ────────────────────────"))
            print(result.get("diff", "").strip())
            print(c(CYAN, "──────────────────────────────────────────────────"))
            print(c(YELLOW, f"\n  Target file : {result.get('file')}"))
            print(c(YELLOW,  "  (No changes written — dry run mode)"))
        else:
            print(c(GREEN,  f"\n  Patch applied : {result.get('patch')}"))
            print(c(CYAN,   f"  Modified file : {result.get('file')}"))
            print(c(CYAN,   f"  Patch saved   : {result.get('written_to')}"))
            if is_coredev:
                print(c(CYAN, f"  {_COREDEV_SUMMARY['Patch'](result)}"))

    else:
        print(json.dumps(result, indent=2))

    # ── Touchpoint 3b: post-result explanation ─────────────────────────────────
    # Beginner: what results mean + next steps.
    # MOTU: compliance notes + next step.
    # CoreDev: skipped.
    _persona_explain("post_result", sname, persona, label=f"{sname}: next steps")


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
