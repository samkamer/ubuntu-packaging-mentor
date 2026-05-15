#!/usr/bin/env python3
"""
mentor.py — Ubuntu AI Packaging Mentor (Orchestrator)
"""

import json
import os
import sys

# Agent imports are deferred until after config is loaded so that
# os.environ is set correctly before agents.brain initialises.
# These module-level names are populated in main() after config loading.
ask        = None
run_audit  = None
run_detect = None
run_scribe = None
run_patch  = None
run_build  = None
run_guard  = None

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
    "5": {
        "name": "Build",
        "agent": "builder.py",
        "description": "Package build — runs debuild and analyses failures with AI.",
    },
    "6": {
        "name": "Guard",
        "agent": "guardian.py",
        "description": "Security audit — scans for secrets and checks hardening flags.",
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
        "Build": (
            "The user is about to run the Build skill on a source package directory.\n"
            "The tool runs 'debuild -us -uc -b' to produce binary .deb packages, "
            "and if the build fails, uses AI to classify the error and suggest which "
            "agent (detective, patch_manager, or auditor) to run to fix it.\n"
            "Explain what debuild does, what a binary build produces, and why build testing matters."
        ),
        "Guard": (
            "The user is about to run the Guard skill on a source package directory.\n"
            "The tool has two parts: (1) a secret scanner that recursively searches "
            "all source files for exposed private keys, API tokens, passwords, and "
            "cloud credentials; (2) a hardening auditor that checks the build log "
            "for missing compiler security flags mandated by Ubuntu/Debian Policy §10.1.\n"
            "Explain why exposed secrets are critical, what compiler hardening flags "
            "like -fstack-protector-strong and -D_FORTIFY_SOURCE=2 protect against, "
            "and why running this audit before uploading to the archive matters."
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
        "Build": (
            "The Build agent is about to run 'debuild -us -uc -b'.\n"
            "Explain what this command does, what output files it produces, "
            "and what a successful build result looks like."
        ),
        "Guard": (
            "The Guard agent is about to scan the source tree for secrets and, "
            "if a build log is available, check hardening flags with blhc.\n"
            "Remind the user that secret scanning covers all text files recursively "
            "(no secret values are stored — only file and line number are reported), "
            "and that the hardening audit requires blhc to be installed."
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
        "Build": (
            "The Build agent has finished running debuild.\n"
            "If successful, explain what .deb files were produced and what to do next.\n"
            "If it failed, explain the error type identified and why the suggested agent "
            "and command will resolve it."
        ),
        "Guard": (
            "The Guard agent has completed its security audit.\n"
            "Explain what the security score means (it is a heuristic, not a compliance "
            "certificate), how to interpret each vulnerability type, and what the "
            "remediation steps in the output mean.  If secrets were found, emphasise that "
            "they must be rotated immediately — not just removed from the repository."
        ),
    },
    "on_error": {
        "Audit":  "The Audit agent reported an error running licensecheck on the package source.",
        "Detect": "The Detect agent reported an error scanning for Build-Depends.",
        "Scribe": "The Scribe agent reported an error generating the changelog entry.",
        "Patch":  "The Patch agent reported an error generating or applying the quilt patch.",
        "Build":  "The Build agent reported an error running debuild.",
        "Guard":  "The Guard agent reported an error during security scanning.",
    },
}

# CoreDev post-result one-liners (no LLM call needed)
_COREDEV_SUMMARY = {
    "Audit":  lambda r: f"debian/copyright generated ({len(r.get('data','').splitlines())} lines).",
    "Detect": lambda r: f"{len(r.get('dependencies', []))} Build-Depends resolved.",
    "Scribe": lambda r: "changelog entry drafted.",
    "Patch":  lambda r: f"patch {r.get('patch','')} applied to {r.get('file','')}.",
    "Build":  lambda r: "Build succeeded." if r.get("status") == "success" else f"Build failed: {r.get('error_type','unknown')} → {r.get('suggested_agent')}.",
    "Guard":  lambda r: f"Score: {r.get('security_score','?')}/100 — {r.get('verdict','?')}. Secrets: {r.get('secrets_found',0)}. Missing flags: {len(r.get('missing_flags',[]))}.",
}


def _show_write_status(result: dict) -> None:
    """Print written_to / backed_up / not-written messages."""
    if result.get("written_to"):
        print(c(GREEN, f"\n✓ Written to: {result['written_to']}"))
        if result.get("backed_up"):
            print(c(YELLOW, f"  ↩ Backup saved: {result['backed_up']}"))
    else:
        print(c(YELLOW, "\n(Not written to disk — answer 'y' at the prompt to save)"))


def _find_build_log(source_dir: str) -> str | None:
    """
    Locate the most recent build log saved by builder.py for this package.

    Checks lab/builds/<pkg_name>/build.log relative to the project root.
    Returns the path if the file exists, otherwise None.
    """
    project_root = os.path.dirname(os.path.abspath(__file__))
    pkg_name = os.path.basename(os.path.abspath(source_dir))
    candidate = os.path.join(project_root, "lab", "builds", pkg_name, "build.log")
    return candidate if os.path.isfile(candidate) else None


def _format_detective_warnings(warnings: dict, persona_name: str) -> str | None:
    """
    Format detective pipeline warnings for the contributor.

    Returns a formatted string, or None if there are no warnings to show.
    Persona determines verbosity:
      Beginner  — plain English guidance on what to check and why
      MOTU      — technical list with Debian Policy reference
      CoreDev   — raw JSON diff of pipeline decisions
    """
    if not warnings:
        return None

    fn_list  = warnings.get("possible_false_negatives", [])
    fp_list  = warnings.get("possible_false_positives", [])
    corr     = warnings.get("name_corrections", [])
    blocked  = warnings.get("blocklisted", [])

    if not any([fn_list, fp_list, corr, blocked]):
        return None

    if persona_name == "CoreDev":
        return c(YELLOW, "\n── Detective pipeline log ──────────────────────────\n") + \
               json.dumps(warnings, indent=2) + \
               "\n" + c(YELLOW, "────────────────────────────────────────────────────")

    lines = [c(YELLOW, "\n── Build-Depends verification needed ───────────────")]

    if persona_name == "MOTU":
        lines.append(
            c(YELLOW, "  Per Debian Policy §7.7, all direct build-time deps must be explicit.")
        )
        if fn_list:
            lines.append(c(YELLOW, "\n  Possibly missing (removed during dedup — reinstate if directly used):"))
            for item in fn_list:
                lines.append(f"    {item['pkg']}")
        if fp_list:
            lines.append(c(YELLOW, "\n  Competing implementations detected (keep one matching your build profile):"))
            for item in fp_list:
                lines.append(f"    {item['pkg']}  ← {item['reason']}")
        if corr:
            lines.append(c(YELLOW, "\n  Automatic name corrections applied:"))
            for item in corr:
                lines.append(f"    {item['from']}  →  {item['to']}")
        if blocked:
            lines.append(c(YELLOW, "\n  Filtered (always available or platform-specific):"))
            for item in blocked:
                lines.append(f"    {item['pkg']}  ({item['reason']})")

    else:  # Beginner
        if fn_list:
            lines.append(c(YELLOW, "\n  ⚠ These packages were found in your source but filtered out."))
            lines.append(  "    Add them back if your package actually uses them:")
            for item in fn_list:
                lines.append(f"    • {item['pkg']}")
        if fp_list:
            lines.append(c(YELLOW, "\n  ⚠ These packages may not all be needed at the same time."))
            lines.append(  "    Usually only one implementation of each library is required.")
            lines.append(  "    Delete the ones you are not using:")
            for item in fp_list:
                lines.append(f"    • {item['pkg']}")
        if corr:
            lines.append(c(YELLOW, "\n  ✓ These package names were auto-corrected to Ubuntu naming:"))
            for item in corr:
                lines.append(f"    • {item['from']}  →  {item['to']}")
        if blocked:
            lines.append(c(YELLOW, "\n  ✓ These were removed (already in every Ubuntu build environment):"))
            for item in blocked:
                lines.append(f"    • {item['pkg']}")

    lines.append(c(YELLOW, "────────────────────────────────────────────────────"))
    return "\n".join(lines)


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

    elif sname == "Build":
        _persona_explain("before_write", sname, persona, label="Build: context")
        result = run_build(target)

    elif sname == "Guard":
        _persona_explain("before_write", sname, persona, label="Guard: context")
        # Auto-detect build log from the last builder run, or let user override
        auto_log = _find_build_log(target)
        if auto_log:
            print(c(CYAN, f"\n  Auto-detected build log: {auto_log}"))
            use_auto = prompt("Use this build log for hardening audit? [Y/n]:").lower()
            build_log_path = auto_log if use_auto != "n" else None
        else:
            print(c(YELLOW, "\n  No build log found — hardening audit will be skipped."))
            print(c(YELLOW, "  Run the Build skill first, or provide a path manually."))
            manual = prompt("Enter build log path (or press Enter to skip):")
            build_log_path = manual if manual and os.path.isfile(manual) else None
        result = run_guard(target, build_log_path)

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
        warnings = result.get("data", {}).get("warnings", {})
        warn_str = _format_detective_warnings(warnings, persona["name"])
        if warn_str:
            print(warn_str)

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

    elif sname == "Build":
        if result.get("status") == "success":
            print(c(GREEN, f"\n  ✓ {result['message']}"))
            print(c(CYAN,  f"  Build log lines: {result.get('log_lines', '?')}"))
            if result.get("build_log_path"):
                print(c(CYAN, f"  Build log saved: {result['build_log_path']}"))
                print(c(YELLOW, "  Tip: run the Guard skill to check hardening flags."))
            if is_coredev:
                print(c(CYAN, f"  {_COREDEV_SUMMARY['Build'](result)}"))
        else:
            print(c(RED,    f"\n  Build failed"))
            print(c(YELLOW, f"  Error type     : {result.get('error_type', 'unknown')}"))
            print(c(YELLOW, f"  Analysis       : {result.get('analysis', '')}"))
            print(c(CYAN,   f"\n  Suggested fix  → {result.get('suggested_agent')}"))
            print(c(CYAN,   f"  Command        : {result.get('suggested_command')}"))
            print(c(CYAN,   "\n── Last build output ──────────────────────────────"))
            print(result.get("log_tail", "").strip())
            print(c(CYAN,   "───────────────────────────────────────────────────"))
            if is_coredev:
                print(c(CYAN, f"  {_COREDEV_SUMMARY['Build'](result)}"))

    elif sname == "Guard":
        verdict  = result.get("verdict", "unknown")
        score    = result.get("security_score", "?")
        verdict_color = GREEN if verdict == "pass" else (YELLOW if verdict == "warn" else RED)

        print(c(verdict_color, f"\n  Security score : {score}/100 (heuristic)"))
        print(c(verdict_color, f"  Verdict        : {verdict.upper()}"))

        vulns = result.get("vulnerabilities", [])
        if vulns:
            print(c(CYAN, "\n── Vulnerabilities ────────────────────────────────"))
            for v in vulns:
                sev = v.get("severity", "?").upper()
                sev_color = RED if sev == "CRITICAL" else (YELLOW if sev == "HIGH" else CYAN)
                if v.get("type") == "secret":
                    print(c(sev_color,
                            f"  [{sev}] {v['match_type']}  "
                            f"{v['file']}:{v['line_number']}"))
                else:
                    print(c(sev_color,
                            f"  [{sev}] {v.get('description', v.get('match_type', ''))}"))
            print(c(CYAN, "────────────────────────────────────────────────────"))
        else:
            print(c(GREEN, "\n  No vulnerabilities found."))

        hstatus = result.get("hardening_status", "skipped")
        if hstatus == "unknown":
            print(c(YELLOW, "\n  Hardening status: unknown (blhc not installed)"))
            print(c(YELLOW, "  Install with: sudo apt install blhc"))
        elif hstatus == "skipped":
            print(c(YELLOW, "\n  Hardening audit skipped (no build log provided)."))

        if result.get("remediation_code"):
            print(c(CYAN, "\n── Remediation ──────────────────────────────────────"))
            print(result["remediation_code"].strip())
            print(c(CYAN, "─────────────────────────────────────────────────────"))

        if not is_coredev and result.get("llm_explanation"):
            print(c(GREEN, "\n── Security Explanation ─────────────────────────────"))
            print(result["llm_explanation"].strip())
            print(c(GREEN, "─────────────────────────────────────────────────────"))

        if is_coredev:
            print(c(CYAN, f"  {_COREDEV_SUMMARY['Guard'](result)}"))

    else:
        print(json.dumps(result, indent=2))

    # ── Touchpoint 3b: post-result explanation ─────────────────────────────────
    # Beginner: what results mean + next steps.
    # MOTU: compliance notes + next step.
    # CoreDev: skipped.
    _persona_explain("post_result", sname, persona, label=f"{sname}: next steps")


# ── Main loop ──────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    from agents import config as cfg
    from agents.preflight import run_setup

    parser = argparse.ArgumentParser(
        description="Ubuntu AI Packaging Mentor",
        add_help=True,
    )
    parser.add_argument(
        "--setup", action="store_true",
        help="Re-detect environment and rewrite config, then exit.",
    )
    args = parser.parse_args()

    # ── Preflight: first run or explicit --setup ──────────────────────────────
    if args.setup or not cfg.exists():
        run_setup(rerun=args.setup)
        if args.setup:
            return   # --setup just runs detection and exits

    # ── Load config → populate env before brain import ───────────────────────
    settings = cfg.load()
    if settings.get("llm.provider"):
        os.environ.setdefault("AI_PROVIDER", settings["llm.provider"])
    if settings.get("llm.url"):
        os.environ.setdefault("LLM_URL", settings["llm.url"])
    if settings.get("llm.model"):
        os.environ.setdefault("LLM_MODEL", settings["llm.model"])
    if settings.get("llm.budget"):
        os.environ.setdefault("LLM_BUDGET", settings["llm.budget"])

    # ── Lazy agent imports (after env is set) ─────────────────────────────────
    import mentor as _self
    from agents.brain import ask as _ask
    from agents.auditor import audit as _run_audit
    from agents.detective import detect as _run_detect
    from agents.scribe import scribe as _run_scribe
    from agents.patch_manager import patch as _run_patch
    from agents.builder import build as _run_build
    from agents.guardian import audit as _run_guard

    # Populate module-level names used by run_skill / _persona_explain
    _self.ask        = _ask
    _self.run_audit  = _run_audit
    _self.run_detect = _run_detect
    _self.run_scribe = _run_scribe
    _self.run_patch  = _run_patch
    _self.run_build  = _run_build
    _self.run_guard  = _run_guard

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
        choice = prompt("Enter choice [1-6]:")

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
