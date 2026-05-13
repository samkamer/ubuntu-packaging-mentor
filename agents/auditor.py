#!/usr/bin/env python3
"""
agents/auditor.py — Legal/Copyright Auditor

Runs `licensecheck -r --copyright .` inside a source directory, then uses
Gemma to convert the raw output into a valid DEP-5 debian/copyright file.

Usage:
    python3 agents/auditor.py <source_dir> [--write]

Flags:
    --write   Write the generated copyright file to <source_dir>/debian/copyright
"""

import json
import os
import shutil
import subprocess
import sys

# Ensure project root is on sys.path when script is run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.brain import ask_gemma

# ── licensecheck ──────────────────────────────────────────────────────────────

def run_licensecheck(source_dir: str) -> str:
    """Run `licensecheck -r --copyright .` inside source_dir; return raw output."""
    result = subprocess.run(
        ["licensecheck", "-r", "--copyright", "."],
        capture_output=True,
        text=True,
        cwd=source_dir,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "licensecheck exited non-zero")
    return result.stdout


# ── Gemma: DEP-5 generation ───────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are an Ubuntu packaging expert. You will receive the raw output of \
`licensecheck -r --copyright` run on a source tree. Convert it into a valid \
DEP-5 debian/copyright file (format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/).

Rules:
- Start with the mandatory Header paragraph (Format:, Upstream-Name:, Source:).
- Group files sharing the same license and copyright into one Files stanza.
- Use SPDX license identifiers wherever possible.
- Use glob patterns (e.g. 'Files: *') to cover the whole tree where appropriate.
- If a license is UNKNOWN, write 'License: UNKNOWN' and add a comment asking the packager to review.
- Output ONLY the raw debian/copyright text — no markdown fences, no commentary.
"""


def generate_dep5(raw_output: str, package_name: str) -> str:
    """Ask Gemma to produce a DEP-5 file from raw licensecheck output."""
    user_prompt = (
        f"Package name: {package_name}\n\n"
        f"Raw licensecheck output:\n{raw_output}"
    )
    return ask_gemma(_SYSTEM_PROMPT, user_prompt, label="Generating DEP-5 copyright file")


# ── Main audit function ───────────────────────────────────────────────────────

def audit(source_dir: str, write: bool = False) -> dict:
    """
    Full audit pipeline. Returns a JSON-serialisable result dict:
      {"status": "success", "data": "<dep5 text>", "agent": "auditor"}
    or on error:
      {"status": "error",   "data": None, "agent": "auditor", "error": "<msg>"}
    """
    # Req 6a: check directory exists
    if not os.path.isdir(source_dir):
        return {
            "status": "error",
            "data": None,
            "agent": "auditor",
            "error": f"Directory not found: {source_dir}",
        }

    # Req 6b: check licensecheck is installed
    if shutil.which("licensecheck") is None:
        return {
            "status": "error",
            "data": None,
            "agent": "auditor",
            "error": "licensecheck is not installed. Run: sudo apt install licensecheck",
        }

    package_name = os.path.basename(os.path.abspath(source_dir))

    # Req 2: run inside the directory
    try:
        raw_output = run_licensecheck(source_dir)
    except RuntimeError as e:
        return {"status": "error", "data": None, "agent": "auditor", "error": str(e)}

    # Req 3/4: send raw output to Gemma for DEP-5 conversion
    try:
        dep5_text = generate_dep5(raw_output, package_name)
    except RuntimeError as e:
        return {"status": "error", "data": None, "agent": "auditor", "error": str(e)}

    # Optional: write to debian/copyright
    written_to = None
    if write:
        debian_dir = os.path.join(source_dir, "debian")
        os.makedirs(debian_dir, exist_ok=True)
        written_to = os.path.join(debian_dir, "copyright")
        with open(written_to, "w") as fh:
            fh.write(dep5_text)

    # Req 5: return the specified JSON shape
    return {
        "status": "success",
        "data": dep5_text,
        "agent": "auditor",
        "written_to": written_to,
    }


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Usage: python3 agents/auditor.py <source_dir> [--write]", file=sys.stderr)
        sys.exit(1)

    result = audit(args[0], write="--write" in args)
    print(json.dumps(result, indent=2))
