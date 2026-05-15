#!/usr/bin/env python3
"""
agents/auditor.py — Legal/Copyright Auditor

Pipeline:
  1. Run `licensecheck -r --copyright .` inside the source directory
  2. Parse output with Python into structured entries
  3. Normalize each raw license string to a DEP-5 identifier via Gemma
     (15 s timeout per call; falls back to regex if LLM is slow/unavailable)
     Total LLM budget: 3 minutes — after that, regex-only for remaining files
  4. Group files by (license, copyright) and generate a proper DEP-5 file
  5. Return {"status": "success", "data": "<dep5 text>", "agent": "auditor"}

Usage:
    python3 agents/auditor.py <source_dir> [--write]

Flags:
    --write   Write the file to <source_dir>/debian/copyright
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.brain import ask, llm_budget_seconds, backup_file

# ── DEP-5 / SPDX known identifiers ───────────────────────────────────────────

VALID_DEP5_IDS = {
    "AFL-2.1", "AGPL-3", "AGPL-3+", "Apache-2.0", "Artistic", "Artistic-2.0",
    "BSD-2-Clause", "BSD-3-Clause", "BSD-4-Clause", "BSD-4-Clause-UC", "BSL-1.0",
    "CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "CDDL", "CPL-1.0", "EPL-1.0", "EPL-2.0",
    "EUPL-1.1", "Expat", "FSFAP", "FSFUL", "FSFULLR",
    "GFDL-1.1", "GFDL-1.1+", "GFDL-1.2", "GFDL-1.2+", "GFDL-1.3", "GFDL-1.3+",
    "GPL", "GPL-1", "GPL-1+", "GPL-2", "GPL-2+", "GPL-3", "GPL-3+",
    "ISC", "LGPL-2", "LGPL-2+", "LGPL-2.1", "LGPL-2.1+", "LGPL-3", "LGPL-3+",
    "LPPL-1.3c", "MIT", "MIT-0", "MPL-1.1", "MPL-2.0", "MS-PL", "MS-RL",
    "OLDAP-2.8", "public-domain", "Python-2.0", "Ruby", "Unlicense", "UNKNOWN",
    "W3C", "X11", "Zlib", "ZPL-2.0",
    # Project-specific identifiers common in Ubuntu/Debian packaging
    "curl",
}

# Regex matching a single valid DEP-5 identifier, including 'with <exception>' suffix
# and short custom identifiers (e.g. 'curl', 'Artistic').
_DEP5_ID_RE = re.compile(
    r'^[A-Za-z][A-Za-z0-9.\-+]*'              # base identifier
    r'(?:\s+with\s+[A-Za-z0-9][A-Za-z0-9 .\-]*)?$'  # optional exception clause
)

LLM_TIMEOUT_PER_CALL = 15    # seconds per individual license-normalization call
LLM_BUDGET_SECONDS   = None  # resolved at call time from LLM_BUDGET env var


def _is_valid_dep5(identifier: str) -> bool:
    # Split on ' or ' (not 'or later') and ' AND '/'and'
    parts = [p.strip() for p in re.split(r"\s+(?:AND|and|OR)\s+|\s+or\s+(?!later\b)", identifier)]
    non_empty = [p for p in parts if p]
    if not non_empty:
        return False
    for part in non_empty:
        # Accept known identifiers, known identifiers with 'with <exception>',
        # and any short custom identifier matching the DEP-5 format.
        base = re.sub(r"\s+with\s+.+$", "", part).strip()
        if base in VALID_DEP5_IDS or _DEP5_ID_RE.match(part):
            continue
        return False
    return True


# ── licensecheck ──────────────────────────────────────────────────────────────

def run_licensecheck(source_dir: str) -> str:
    result = subprocess.run(
        ["licensecheck", "-r", "--copyright", "."],
        capture_output=True, text=True, cwd=source_dir,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "licensecheck exited non-zero")
    return result.stdout


# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_licensecheck_output(output: str) -> list[dict]:
    """
    Parse licensecheck -r --copyright output into structured entries.

    Each block looks like:
        ./path/to/file.c: GNU General Public License v3.0 or later
          [Copyright: 2024 Some Author]
    """
    entries = []
    current = None

    for line in output.splitlines():
        file_match = re.match(r"^(\./\S+):\s+(.+)$", line)
        if file_match:
            if current:
                entries.append(current)
            current = {
                "file": file_match.group(1),
                "license": file_match.group(2).strip(),
                "copyrights": [],
            }
        elif current:
            copy_match = re.match(r"^\s+\[Copyright:\s*(.*?)\s*\]$", line)
            if copy_match:
                text = copy_match.group(1).strip()
                if text:
                    current["copyrights"].append(text)

    if current:
        entries.append(current)
    return entries


# ── License normalisation ─────────────────────────────────────────────────────

def _regex_fallback(raw: str) -> str:
    """Map a raw license string to a DEP-5 identifier using regex only."""
    cleaned = re.sub(r"\s*\[generated file\]", "", raw, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"^\*No copyright\*\s*", "", cleaned, flags=re.IGNORECASE).strip()
    # 'and/or' in license context means either license is acceptable → DEP-5 'or'
    cleaned = re.sub(r"\s+and/or\s+", " or ", cleaned, flags=re.IGNORECASE)

    # Split compound expressions on ' or ' (but NOT 'or later') and ' AND '
    if re.search(r"\s+or\s+(?!later\b)|\s+AND\s+", cleaned):
        parts = re.split(r"\s+or\s+(?!later\b)|\s+AND\s+", cleaned)
        return " or ".join(_regex_fallback(p.strip()) for p in parts if p.strip())

    n = cleaned.lower()
    mappings = [
        # Exception-aware patterns must precede plain GPL patterns (most specific first)
        (r"gnu general public license v?2.*(?:autoconf|data).*exception",
                                                   "GPL-2+ with Autoconf-data exception"),
        (r"gnu general public license v?2.*libtool.*exception",
                                                   "GPL-2+ with Libtool exception"),
        (r"gnu general public license v?3.*(?:autoconf|data).*exception",
                                                   "GPL-3+ with Autoconf-data exception"),
        # Plain GPL/LGPL/GFDL
        (r"gnu general public license v?3.*or later",           "GPL-3+"),
        (r"gnu general public license v?3",                     "GPL-3"),
        (r"gnu general public license v?2.*or later",           "GPL-2+"),
        (r"gnu general public license v?2",                     "GPL-2"),
        (r"gnu general public license",                         "GPL"),
        (r"gnu lesser general public license v?3.*or later",    "LGPL-3+"),
        (r"gnu lesser general public license v?3",              "LGPL-3"),
        (r"gnu lesser general public license v?2\.1.*or later", "LGPL-2.1+"),
        (r"gnu lesser general public license v?2\.1",           "LGPL-2.1"),
        (r"gnu lesser general public license v?2.*or later",    "LGPL-2+"),
        (r"gnu lesser general public license v?2",              "LGPL-2"),
        (r"gnu free documentation license v?1\.3.*or later",    "GFDL-1.3+"),
        (r"gnu free documentation license v?1\.3",              "GFDL-1.3"),
        (r"gnu free documentation license v?1\.2.*or later",    "GFDL-1.2+"),
        (r"gnu free documentation license v?1\.2",              "GFDL-1.2"),
        # FSF licenses
        (r"fsf unlimited license.*retention",                   "FSFULLR"),
        (r"fsf unlimited license",                              "FSFUL"),
        (r"fsf all permissive",                                 "FSFAP"),
        # Others
        (r"apache.*2",                                          "Apache-2.0"),
        (r"mit",                                                "MIT"),
        (r"x11",                                                "X11"),
        (r"isc",                                                "ISC"),
        (r"bsd.?2.?clause|simplified bsd",                      "BSD-2-Clause"),
        (r"bsd.?3.?clause|new bsd",                             "BSD-3-Clause"),
        (r"bsd.?4.?clause.*california|university.*california",  "BSD-4-Clause-UC"),
        (r"bsd.?4.?clause",                                     "BSD-4-Clause"),
        (r"open\s*ldap.*2\.8",                                  "OLDAP-2.8"),
        (r"^curl\s*licen|^the curl licen",                      "curl"),
        (r"mpl.*2",                                             "MPL-2.0"),
        (r"public.?domain",                                     "public-domain"),
        (r"unknown",                                            "UNKNOWN"),
    ]
    for pattern, dep5_id in mappings:
        if re.search(pattern, n):
            return dep5_id
    return cleaned if cleaned else "UNKNOWN"


def _call_llm(raw: str) -> str:
    """Ask the configured AI provider to map a raw license string to a DEP-5 identifier."""
    system = "You are a Debian packaging expert."
    user = (
        "Map the following raw license string to a valid DEP-5 machine-readable "
        "license identifier (e.g. GPL-2+, MIT, Apache-2.0, curl, OLDAP-2.8).\n"
        "Rules:\n"
        "- Reply with ONLY the short DEP-5 identifier, nothing else.\n"
        "- For alternative licenses (either may be chosen), join with ' or ' "
        "(e.g. 'GPL-2+ or Artistic', 'curl or ISC').\n"
        "- Use 'and' ONLY when both licenses apply simultaneously.\n"
        "- Preserve exception clauses exactly — never shorten them "
        "(e.g. 'GPL-2+ with Autoconf-data exception', NOT 'GPL-2+').\n"
        "- For project-specific licenses (e.g. the curl license), use the "
        "short project name as the identifier (e.g. 'curl').\n"
        "- If truly unknown, reply: UNKNOWN\n\n"
        f"Raw license string: {raw}\n"
        "DEP-5 identifier:"
    )
    # label="" suppresses the spinner — auditor shows its own per-file progress
    result = ask(system, user, label="", timeout=LLM_TIMEOUT_PER_CALL)
    return result.strip().split("\n")[0].strip()


def reason_license(raw: str, llm_budget: dict) -> str:
    """
    Normalize a raw license string to a DEP-5 identifier.
    Tries the configured AI provider first (with per-call and total budget timeouts),
    then falls back to regex.
    llm_budget is a mutable dict {"remaining": float} shared across the run.
    """
    if llm_budget["remaining"] > 0:
        t0 = time.time()
        try:
            result = _call_llm(raw)
            elapsed = time.time() - t0
            llm_budget["remaining"] -= elapsed
            if result and _is_valid_dep5(result):
                return result
            if result:
                print(f"  [~] AI returned unknown id {result!r} for {raw!r} — using regex",
                      file=sys.stderr)
        except (RuntimeError, OSError, KeyError, json.JSONDecodeError):
            llm_budget["remaining"] -= (time.time() - t0)
    else:
        print("  [~] LLM budget exhausted — using regex fallback", file=sys.stderr)

    return _regex_fallback(raw)


# ── Grouping ──────────────────────────────────────────────────────────────────

def group_by_license(entries: list[dict], llm_budget: dict) -> dict:
    unique_licenses = list({e["license"] for e in entries})
    total = len(unique_licenses)
    print(f"  Normalising {total} unique license string(s) ...", file=sys.stderr)

    # Normalise each unique license string once.
    # Track whether the raw string used 'and/or' — that signals ambiguity about
    # whether the compound expression is alternative (or) or simultaneous (and).
    cache = {}
    for i, raw in enumerate(unique_licenses, 1):
        label = f"  [{i}/{total}] {raw[:60]}"
        dep5_id = reason_license(raw, llm_budget)
        ambiguous = bool(re.search(r"\band/or\b", raw, re.IGNORECASE))
        print(f"{label} → {dep5_id}", file=sys.stderr)
        cache[raw] = (dep5_id, ambiguous)

    # Group files
    groups: dict = {}
    for entry in entries:
        dep5_id, ambiguous = cache[entry["license"]]
        key = (dep5_id, frozenset(entry["copyrights"]))
        if key not in groups:
            groups[key] = {
                "files":      [],
                "license":    dep5_id,
                "copyrights": entry["copyrights"],
                "ambiguous":  ambiguous,
            }
        groups[key]["files"].append(entry["file"])

    return groups


# ── DEP-5 generation ──────────────────────────────────────────────────────────

def build_dep5(groups: dict, source_name: str) -> str:
    lines = []
    year = date.today().year

    lines += [
        "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/",
        f"Upstream-Name: {source_name}",
        "Upstream-Contact: FIXME <maintainer@example.com>",
        f"Source: {source_name}",
        "",
    ]

    seen_licenses = []
    for info in groups.values():
        dep5_id = info["license"]
        files_glob = " ".join(sorted(info["files"])) if info["files"] else "*"
        copyrights = sorted(info["copyrights"]) or [f"{year} FIXME <upstream>"]

        lines.append(f"Files: {files_glob}")
        for cp in copyrights:
            lines.append(f"Copyright: {cp}")
        # If licensecheck used 'and/or', we defaulted to 'or' (alternative licenses).
        # Add a FIXME so the maintainer can verify — change to 'and' if both licenses
        # apply simultaneously rather than as a choice.
        if info.get("ambiguous"):
            lines.append(
                "# FIXME: licensecheck reported 'and/or' — using 'or' (either license "
                "may be chosen). Change to 'and' if both licenses apply simultaneously."
            )
        lines.append(f"License: {dep5_id}")
        lines.append("")

        if dep5_id not in seen_licenses:
            seen_licenses.append(dep5_id)

    for dep5_id in seen_licenses:
        lines.append(f"License: {dep5_id}")
        lines.append(f" Full license text available at:")
        lines.append(f" https://spdx.org/licenses/{dep5_id}.html")
        lines.append("")

    return "\n".join(lines)


# ── Main audit function ───────────────────────────────────────────────────────

def audit(source_dir: str, write: bool = False, backup: bool = False) -> dict:
    if not os.path.isdir(source_dir):
        return {"status": "error", "data": None, "agent": "auditor",
                "error": f"Directory not found: {source_dir}"}

    if shutil.which("licensecheck") is None:
        return {"status": "error", "data": None, "agent": "auditor",
                "error": "licensecheck not installed. Run: sudo apt install licensecheck"}

    source_name = os.path.basename(os.path.abspath(source_dir))

    print("  [*] Running licensecheck ...", file=sys.stderr)
    try:
        raw_output = run_licensecheck(source_dir)
    except RuntimeError as e:
        return {"status": "error", "data": None, "agent": "auditor", "error": str(e)}

    entries = parse_licensecheck_output(raw_output)
    if not entries:
        return {"status": "error", "data": None, "agent": "auditor",
                "error": "No license/copyright information found in source tree."}

    print(f"  [*] Found {len(entries)} file(s). Resolving license identifiers ...", file=sys.stderr)
    budget = llm_budget_seconds()
    print(f"  [*] LLM budget: {int(budget)}s (set LLM_BUDGET env var to change)", file=sys.stderr)
    llm_budget = {"remaining": budget}
    groups = group_by_license(entries, llm_budget)

    dep5_text = build_dep5(groups, source_name)

    written_to = None
    backed_up  = None
    if write:
        debian_dir = os.path.join(source_dir, "debian")
        os.makedirs(debian_dir, exist_ok=True)
        written_to = os.path.join(debian_dir, "copyright")
        if backup:
            backed_up = backup_file(written_to)
            if backed_up:
                print(f"  [✓] Backup: {backed_up}", file=sys.stderr)
        with open(written_to, "w", encoding="utf-8") as fh:
            fh.write(dep5_text)

    return {"status": "success", "data": dep5_text, "agent": "auditor",
            "written_to": written_to, "backed_up": backed_up}


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Usage: python3 agents/auditor.py <source_dir> [--write]", file=sys.stderr)
        sys.exit(1)

    result = audit(args[0], write="--write" in args)
    print(json.dumps(result, indent=2))
