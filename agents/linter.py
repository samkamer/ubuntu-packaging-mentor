#!/usr/bin/env python3
"""
agents/linter.py — Debian Package Linter

Pipeline:
  1. Run 'lintian --color never --suppress-tags=<noise_tags> <changes_or_deb>'
     on a built .changes file (preferred) or .deb file.
  2. Parse output into E: errors and W: warnings via regex.
  3. If any E: errors remain → send deduped tags to the LLM for recovery advice;
     set status = "error".
  4. If only W: warnings remain → status = "success".
  5. Return {"status": "success"|"error", "errors": [...], "warnings": [...],
             "analysis": str|None, "agent": "linter"}

Usage:
    python3 agents/linter.py <path_to.changes_or_deb>
"""

import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.brain import ask

# ── Constants ─────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are an Ubuntu Packaging Specialist with deep knowledge of Debian Policy, "
    "lintian tags, and how to fix packaging mistakes."
)

# Tags that are expected/unavoidable for new upstream packages — suppress them
# in the lintian invocation so they don't pollute error counts.
_SUPPRESS_TAGS = [
    "initial-upload-closes-no-bugs",
    "groff-message",
    "debian-watch-file-is-missing",
]

# lintian output line: "E: package: tag-name optional description"
_LINE_RE = re.compile(r"^([A-Z]):\s+\S+:\s+(\S+)(.*)")

# Maximum number of unique error tags to send to the LLM
_MAX_LLM_TAGS = 10


# ── Lintian runner ────────────────────────────────────────────────────────────

def run_lintian(target: str) -> tuple[int, str, str]:
    """
    Run lintian on target (.changes or .deb).
    Returns (returncode, stdout, stderr).
    """
    suppress = ",".join(_SUPPRESS_TAGS)
    cmd = [
        "lintian",
        "--color", "never",
        f"--suppress-tags={suppress}",
        target,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


# ── Output parser ─────────────────────────────────────────────────────────────

def parse_lintian_output(stdout: str) -> tuple[list[dict], list[dict]]:
    """
    Parse lintian output into (errors, warnings).
    Each entry: {"severity": "E"|"W", "tag": str, "detail": str, "raw": str}
    """
    errors   = []
    warnings = []

    for line in stdout.splitlines():
        line = line.rstrip()
        m = _LINE_RE.match(line)
        if not m:
            continue
        severity, tag, detail = m.group(1), m.group(2), m.group(3).strip()
        entry = {"severity": severity, "tag": tag, "detail": detail, "raw": line}
        if severity == "E":
            errors.append(entry)
        elif severity == "W":
            warnings.append(entry)

    return errors, warnings


# ── LLM analysis ──────────────────────────────────────────────────────────────

def _analyse_errors_with_llm(errors: list[dict], target: str) -> str:
    """
    Send unique lintian error tags to the LLM for recovery advice.
    Returns the LLM's analysis string.
    """
    # Deduplicate by tag, cap to _MAX_LLM_TAGS
    seen:  set  = set()
    unique: list = []
    for e in errors:
        if e["tag"] not in seen:
            seen.add(e["tag"])
            unique.append(e)
        if len(unique) >= _MAX_LLM_TAGS:
            break

    tag_block = "\n".join(f"  E: {e['tag']}  {e['detail']}" for e in unique)
    user_prompt = (
        f"The following lintian errors were found in the package '{os.path.basename(target)}':\n\n"
        f"{tag_block}\n\n"
        "For each lintian error tag, briefly explain:\n"
        "1. What the tag means and why it fails Debian Policy.\n"
        "2. The specific fix to apply in the debian/ packaging files.\n"
        "Keep the response concise and actionable."
    )
    try:
        return ask(_SYSTEM_PROMPT, user_prompt, label="Analysing lintian errors").strip()
    except RuntimeError as exc:
        return f"LLM unavailable ({exc}). Fix lintian errors manually using: lintian-explain-tags <tag>"


# ── Public API ────────────────────────────────────────────────────────────────

def lint(target: str) -> dict:
    """
    Main entry point.

    Args:
        target: Path to a .changes file (preferred) or .deb file.

    Returns:
        {
          "status":   "success" | "error",
          "errors":   [{"tag": ..., "detail": ..., "raw": ...}, ...],
          "warnings": [{"tag": ..., "detail": ..., "raw": ...}, ...],
          "analysis": str | None,   # LLM advice, only when errors present
          "error_type": "lintian",  # always set so builder can route correctly
          "agent":    "linter",
        }
    """
    if not os.path.isfile(target):
        return {
            "status":    "error",
            "errors":    [],
            "warnings":  [],
            "analysis":  None,
            "error_type": "lintian",
            "agent":     "linter",
            "error":     f"File not found: {target}",
        }

    if shutil.which("lintian") is None:
        return {
            "status":    "error",
            "errors":    [],
            "warnings":  [],
            "analysis":  None,
            "error_type": "lintian",
            "agent":     "linter",
            "error":     "lintian not installed — run: sudo apt install lintian",
        }

    print(f"  [*] Running lintian on {os.path.basename(target)} ...", file=sys.stderr)
    returncode, stdout, stderr = run_lintian(target)

    # A non-zero returncode from lintian means it found issues (not a tool failure)
    # unless stdout is empty and stderr has content (tool failure).
    if not stdout.strip() and stderr.strip() and returncode != 0:
        return {
            "status":    "error",
            "errors":    [],
            "warnings":  [],
            "analysis":  None,
            "error_type": "lintian",
            "agent":     "linter",
            "error":     f"lintian command failed: {stderr.strip()}",
        }

    errors, warnings = parse_lintian_output(stdout)

    if errors:
        print(f"  [!] lintian: {len(errors)} error(s), {len(warnings)} warning(s)",
              file=sys.stderr)
        analysis = _analyse_errors_with_llm(errors, target)
        # Strip raw field to keep JSON output clean
        clean_errors   = [{"tag": e["tag"], "detail": e["detail"]} for e in errors]
        clean_warnings = [{"tag": w["tag"], "detail": w["detail"]} for w in warnings]
        return {
            "status":     "error",
            "errors":     clean_errors,
            "warnings":   clean_warnings,
            "analysis":   analysis,
            "error_type": "lintian",
            "agent":      "linter",
        }

    print(f"  [✓] lintian: 0 errors, {len(warnings)} warning(s)", file=sys.stderr)
    clean_warnings = [{"tag": w["tag"], "detail": w["detail"]} for w in warnings]
    return {
        "status":     "success",
        "errors":     [],
        "warnings":   clean_warnings,
        "analysis":   None,
        "error_type": "lintian",
        "agent":      "linter",
    }


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Debian Package Linter — runs lintian and analyses errors with AI",
    )
    parser.add_argument("target", help="Path to .changes file (preferred) or .deb file")
    args = parser.parse_args()

    result = lint(args.target)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] == "success" else 1)
