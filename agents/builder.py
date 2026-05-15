#!/usr/bin/env python3
"""
agents/builder.py — Debian Package Builder

Pipeline:
  1. Run 'debuild -us -uc -b' inside the source directory.
  2. Capture full stdout + stderr.
  3. On success → return {"status": "success", ...}.
  4. On failure:
       a. Extract the last 20 lines of the combined build log.
       b. Send to LLM: classify error, suggest which agent fixes it, provide command.
       c. Return {"status": "error", "analysis": ..., "suggested_agent": ...,
                  "suggested_command": ..., "log_tail": ...}

Usage:
    python3 agents/builder.py <source_dir>
"""

import glob as _glob
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.brain import ask, llm_budget_seconds
from agents.linter import lint

# ── Constants ──────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are an Ubuntu Build Specialist with deep knowledge of debuild, dpkg-buildpackage, "
    "debian/rules, and the Ubuntu/Debian packaging toolchain."
)

_ERROR_CATEGORIES = {
    "missing_dependency": [
        r"cannot find",
        r"no such file or directory",
        r"package .* not found",
        r"unmet build dependencies",
        r"dpkg-checkbuilddeps",
        r"apt-get build-dep",
        r"error: could not find",
        r"fatal error:.*\.h: no such file",
        r"pkg-config: command not found",
        r"checking for .* \.\.\. no",
    ],
    "syntax_error": [
        r"syntax error",
        r"parse error",
        r"unexpected token",
        r"compilation failed",
        r"error:.*expected",
        r"make\[.*\]: \*\*\* \[.*\] error",
        r"cc1: error",
        r"ld returned [0-9]+ exit status",
    ],
    "packaging_mistake": [
        r"dpkg-source",
        r"dh_.*: error",
        r"debian/rules",
        r"debian/control",
        r"debian/copyright",
        r"debian/changelog",
        r"lintian",
        r"override_dh_",
        r"dh: error",
        r"fakeroot",
    ],
}

LOG_TAIL_LINES = 20

# Subdirectory under lab/builds/ where build artefacts are stored
_LAB_BUILDS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lab", "builds",
)


def _save_build_log(source_dir: str, log_content: str) -> str | None:
    """
    Persist the full debuild log to lab/builds/<pkg_name>/build.log.

    Returns the saved path on success, or None on failure (non-fatal).
    The log is always written regardless of build success/failure so that
    guardian.py can perform a hardening audit on both outcomes.
    """
    pkg_name = os.path.basename(os.path.abspath(source_dir))
    build_dir = os.path.join(_LAB_BUILDS_DIR, pkg_name)
    try:
        os.makedirs(build_dir, exist_ok=True)
        log_path = os.path.join(build_dir, "build.log")
        with open(log_path, "w", encoding="utf-8", errors="replace") as f:
            f.write(log_content)
        return log_path
    except OSError as exc:
        print(f"  [~] Could not save build log: {exc}", file=sys.stderr)
        return None


# ── Build runner ───────────────────────────────────────────────────────────────

def run_debuild(source_dir: str) -> tuple[int, str]:
    """
    Run 'debuild -us -uc -b' in source_dir.
    Returns (returncode, combined_output).
    """
    env = os.environ.copy()
    # Ensure debuild doesn't try to sign anything
    env.setdefault("DEBEMAIL", "builder@example.com")
    env.setdefault("DEBFULLNAME", "Package Builder")

    print("  [*] Running debuild -us -uc -b ...", file=sys.stderr)
    result = subprocess.run(
        ["debuild", "-us", "-uc", "-b"],
        cwd=source_dir,
        capture_output=True,
        text=True,
        env=env,
    )
    combined = result.stdout + ("\n" + result.stderr if result.stderr else "")
    return result.returncode, combined.strip()


# ── Error analysis ─────────────────────────────────────────────────────────────

def _classify_error(log_tail: str) -> str:
    """
    Quick regex pre-classification of the error type.
    Returns 'missing_dependency', 'syntax_error', 'packaging_mistake', or 'unknown'.
    """
    lower = log_tail.lower()
    for category, patterns in _ERROR_CATEGORIES.items():
        for pattern in patterns:
            if re.search(pattern, lower):
                return category
    return "unknown"


def _analyse_with_llm(log_tail: str, source_dir: str) -> dict:
    """
    Send the build error tail to the LLM for classification and recovery advice.
    Returns a dict with keys: analysis, error_type, suggested_agent, suggested_command.
    """
    pkg_name = os.path.basename(os.path.abspath(source_dir))
    user_prompt = (
        f"Package source: {pkg_name}\n\n"
        f"Last {LOG_TAIL_LINES} lines of the debuild output:\n"
        f"```\n{log_tail}\n```\n\n"
        "Analyze this build failure.\n"
        "1. Determine the root cause: is it a missing dependency, a syntax error, "
        "or a packaging mistake?\n"
        "2. Suggest which agent should be used to fix it:\n"
        "   - 'detective'     → missing build dependencies (generates new Build-Depends)\n"
        "   - 'patch_manager' → source code error that needs a patch\n"
        "   - 'auditor'       → packaging file problem (copyright/control/changelog)\n"
        "3. Provide the specific CLI command to run the suggested agent.\n\n"
        "Reply in this exact format:\n"
        "ERROR_TYPE: <missing_dependency|syntax_error|packaging_mistake|unknown>\n"
        "AGENT: <detective|patch_manager|auditor>\n"
        "COMMAND: python3 agents/<agent>.py <args>\n"
        "ANALYSIS: <one or two sentence explanation of what went wrong and how to fix it>"
    )

    try:
        raw = ask(_SYSTEM_PROMPT, user_prompt, label="Analysing build failure")
        return _parse_llm_response(raw, source_dir, log_tail)
    except RuntimeError as exc:
        # LLM unavailable — fall back to regex classification
        error_type = _classify_error(log_tail)
        return _regex_recovery(error_type, source_dir, str(exc))


def _parse_llm_response(raw: str, source_dir: str, log_tail: str) -> dict:
    """Parse the structured LLM response into a clean dict."""
    result = {
        "error_type":        "unknown",
        "suggested_agent":   None,
        "suggested_command": None,
        "analysis":          raw.strip(),
    }

    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("ERROR_TYPE:"):
            result["error_type"] = line.split(":", 1)[1].strip()
        elif line.startswith("AGENT:"):
            result["suggested_agent"] = line.split(":", 1)[1].strip()
        elif line.startswith("COMMAND:"):
            result["suggested_command"] = line.split(":", 1)[1].strip()
        elif line.startswith("ANALYSIS:"):
            result["analysis"] = line.split(":", 1)[1].strip()

    # If LLM gave a well-formed response, prefer that analysis over the raw dump
    if result["suggested_agent"] and result["suggested_command"]:
        return result

    # LLM response wasn't structured — fall back to regex + build a command
    error_type = _classify_error(log_tail)
    fallback = _regex_recovery(error_type, source_dir, "")
    result["error_type"]        = fallback["error_type"]
    result["suggested_agent"]   = fallback["suggested_agent"]
    result["suggested_command"] = fallback["suggested_command"]
    if not result["analysis"] or result["analysis"] == raw.strip():
        result["analysis"] = fallback["analysis"]
    return result


def _regex_recovery(error_type: str, source_dir: str, reason: str) -> dict:
    """Build a recovery suggestion using regex classification alone."""
    agent_map = {
        "missing_dependency": (
            "detective",
            f"python3 agents/detective.py {source_dir} --write",
            "Missing build dependencies detected. Run detective to regenerate Build-Depends.",
        ),
        "syntax_error": (
            "patch_manager",
            f"python3 agents/patch_manager.py {source_dir} fix-build-error \"Fix compilation error\"",
            "Compilation or syntax error in source. Use patch_manager to apply a corrective patch.",
        ),
        "packaging_mistake": (
            "auditor",
            f"python3 agents/auditor.py {source_dir} --write",
            "Packaging file issue detected. Run auditor to regenerate debian/copyright.",
        ),
        "unknown": (
            "detective",
            f"python3 agents/detective.py {source_dir} --write",
            "Build failed for an unknown reason. Start by verifying Build-Depends with detective.",
        ),
    }
    agent, command, analysis = agent_map.get(error_type, agent_map["unknown"])
    if reason:
        analysis += f" (LLM unavailable: {reason})"
    return {
        "error_type":        error_type,
        "suggested_agent":   agent,
        "suggested_command": command,
        "analysis":          analysis,
    }


# ── .changes finder ───────────────────────────────────────────────────────────

def _find_changes_file(source_dir: str, build_log: str) -> str | None:
    """
    Find the .changes file produced by debuild.

    Strategy (in order of preference):
      1. Parse debuild stdout for a line mentioning a .changes file.
      2. Glob the parent directory for *.changes sorted by mtime (newest).
    """
    # 1. Parse build log — dpkg-genchanges prints the path
    for line in build_log.splitlines():
        if line.strip().endswith(".changes"):
            candidate = line.strip()
            # Path may be relative to source_dir's parent
            if not os.path.isabs(candidate):
                candidate = os.path.normpath(
                    os.path.join(os.path.dirname(os.path.abspath(source_dir)), candidate)
                )
            if os.path.isfile(candidate):
                return candidate

    # 2. Glob parent dir for any .changes files
    parent = os.path.dirname(os.path.abspath(source_dir))
    candidates = _glob.glob(os.path.join(parent, "*.changes"))
    if candidates:
        return max(candidates, key=os.path.getmtime)

    return None


# ── Public API ─────────────────────────────────────────────────────────────────

def build(source_dir: str) -> dict:
    """
    Main entry point.

    Args:
        source_dir: Path to the package source tree (must contain debian/).

    Returns:
        JSON-serialisable dict.
        Success: {status, message, agent, log_lines}
        Failure: {status, error_type, analysis, suggested_agent,
                  suggested_command, log_tail, agent}
    """
    if not os.path.isdir(source_dir):
        return {"status": "error", "agent": "builder",
                "error": f"Directory not found: {source_dir}"}

    if not os.path.isdir(os.path.join(source_dir, "debian")):
        return {"status": "error", "agent": "builder",
                "error": "No debian/ directory found — is this a Debian source tree?"}

    if shutil.which("debuild") is None:
        return {"status": "error", "agent": "builder",
                "error": "debuild not installed — run: sudo apt install devscripts"}

    print(f"\n  LLM budget: {llm_budget_seconds():.0f}s", file=sys.stderr)

    returncode, full_log = run_debuild(source_dir)

    log_lines = full_log.splitlines()
    log_tail  = "\n".join(log_lines[-LOG_TAIL_LINES:])

    # Persist the full log regardless of build outcome so that guardian.py
    # can perform a hardening audit on successful builds and the log is
    # available for post-mortem analysis on failures.
    log_path = _save_build_log(source_dir, full_log)

    if returncode == 0:
        print("  [✓] Build succeeded", file=sys.stderr)
        changes_file = _find_changes_file(source_dir, full_log)

        lintian_result = None
        if changes_file:
            lintian_result = lint(changes_file)
        else:
            print("  [~] Could not locate .changes file — skipping lintian",
                  file=sys.stderr)

        base = {
            "status":          "success",
            "message":         "Package built successfully.",
            "agent":           "builder",
            "log_lines":       len(log_lines),
            "build_log_path":  log_path,
            "lintian":         lintian_result,
        }

        # Lintian errors flip the overall status but use a distinct error_type
        # so mentor.py can distinguish build failures from lint failures.
        if lintian_result and lintian_result.get("status") == "error":
            print(
                f"  [!] lintian found {len(lintian_result['errors'])} error(s) — "
                "package needs fixes before upload",
                file=sys.stderr,
            )
            base["status"]           = "error"
            base["error_type"]       = "lintian"
            base["analysis"]         = lintian_result.get("analysis")
            base["suggested_agent"]  = "auditor"
            base["suggested_command"] = (
                f"python3 agents/auditor.py {source_dir} --write"
            )

        return base

    # Build failed — analyse and suggest recovery
    print(f"  [!] Build failed (exit {returncode}) — analysing error ...",
          file=sys.stderr)
    analysis = _analyse_with_llm(log_tail, source_dir)

    print(f"  [→] Suggested agent   : {analysis['suggested_agent']}", file=sys.stderr)
    print(f"  [→] Suggested command : {analysis['suggested_command']}", file=sys.stderr)

    return {
        "status":            "error",
        "agent":             "builder",
        "error_type":        analysis["error_type"],
        "analysis":          analysis["analysis"],
        "suggested_agent":   analysis["suggested_agent"],
        "suggested_command": analysis["suggested_command"],
        "log_tail":          log_tail,
        "build_log_path":    log_path,
    }


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Debian Package Builder — runs debuild and analyses failures with AI",
    )
    parser.add_argument("source_dir", help="Path to the package source tree")
    args = parser.parse_args()

    result = build(args.source_dir)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] == "success" else 1)
