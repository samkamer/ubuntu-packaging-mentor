# ulissys_copyright_agent.py - Ubuntu License Scanning Agent
# Automated Ubuntu packaging assistant for generating DEP-5 debian/copyright files.

import sys
import os
import re
import json
import shutil
import subprocess
import tempfile
import urllib.request
import urllib.error
from collections import defaultdict  # retained for potential future use
from datetime import date

OLLAMA_MODEL = os.environ.get("ULISSYS_MODEL", "gemma3")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")

# Known valid DEP-5 / SPDX identifiers (covers the most common cases).
# Full list: https://spdx.org/licenses/ and Debian's DEP-5 extensions.
VALID_DEP5_IDS = {
    "AFL-2.1",
    "AGPL-3",
    "AGPL-3+",
    "Apache-2.0",
    "Artistic",
    "Artistic-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "BSD-4-Clause",
    "BSL-1.0",
    "CC0-1.0",
    "CC-BY-4.0",
    "CC-BY-SA-4.0",
    "CDDL",
    "CPL-1.0",
    "EPL-1.0",
    "EPL-2.0",
    "EUPL-1.1",
    "Expat",
    "FSFAP",
    "FSFUL",
    "FSFULLR",
    "GFDL-1.1",
    "GFDL-1.1+",
    "GFDL-1.2",
    "GFDL-1.2+",
    "GFDL-1.3",
    "GFDL-1.3+",
    "GPL",
    "GPL-1",
    "GPL-1+",
    "GPL-2",
    "GPL-2+",
    "GPL-3",
    "GPL-3+",
    "ISC",
    "LGPL-2",
    "LGPL-2+",
    "LGPL-2.1",
    "LGPL-2.1+",
    "LGPL-3",
    "LGPL-3+",
    "LPPL-1.3c",
    "MIT",
    "MIT-0",
    "MPL-1.1",
    "MPL-2.0",
    "MS-PL",
    "MS-RL",
    "public-domain",
    "Python-2.0",
    "Ruby",
    "Unlicense",
    "UNKNOWN",
    "W3C",
    "X11",
    "Zlib",
    "ZPL-2.0",
}


def _is_valid_dep5(identifier):
    """Return True if identifier (or all parts of a compound) are known DEP-5 ids."""
    parts = [p.strip() for p in re.split(r"\s+AND\s+|\s+OR\s+", identifier)]
    return all(p in VALID_DEP5_IDS for p in parts)


# ---------------------------------------------------------------------------
# Argument handling
# ---------------------------------------------------------------------------


def parse_args():
    if len(sys.argv) != 2:
        print("Usage: python3 ulissys_copyright_agent.py <path|github-url>")
        sys.exit(1)
    return sys.argv[1]


def is_github_url(arg):
    return arg.startswith("https://github.com") or arg.startswith("git@github.com")


# ---------------------------------------------------------------------------
# Environment preparation
# ---------------------------------------------------------------------------


def clone_repo(url, target_dir):
    print(f"[*] Cloning {url} ...")
    result = subprocess.run(
        ["git", "clone", "--depth", "1", url, target_dir],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"[!] git clone failed:\n{result.stderr}")
        sys.exit(1)
    print("[*] Clone complete.")


def verify_directory(path):
    if not os.path.isdir(path):
        print(f"[!] Directory not found: {path}")
        sys.exit(1)


def is_licensecheck_installed():
    return shutil.which("licensecheck") is not None


# ---------------------------------------------------------------------------
# Data extraction
# ---------------------------------------------------------------------------


def run_licensecheck(target_dir):
    print("[*] Running licensecheck ...")
    result = subprocess.run(
        ["licensecheck", "-r", "--copyright", "."],
        capture_output=True,
        text=True,
        cwd=target_dir,
    )
    return result.stdout


def run_ulissys(target_dir):
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ulissys.py")
    print("[*] licensecheck not found — running ulissys.py fallback ...")
    result = subprocess.run(
        ["python3", script], capture_output=True, text=True, cwd=target_dir
    )
    return result.stdout


# ---------------------------------------------------------------------------
# Data processing
# ---------------------------------------------------------------------------


def parse_licensecheck_output(output):
    """
    Parse licensecheck -r --copyright output.

    Each block looks like:
        ./path/to/file.c: GNU General Public License v3.0 or later
          [Copyright: 2024 Some Author]

    Returns list of dicts: [{file, license, copyrights}]
    """
    entries = []
    current = None

    for line in output.splitlines():
        # File line: './foo.c: LICENSE STRING'
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
            # Copyright line: '  [Copyright: YEAR Author Name]'
            copy_match = re.match(r"^\s+\[Copyright:\s*(.*?)\s*\]$", line)
            if copy_match:
                copyright_text = copy_match.group(1).strip()
                if copyright_text:
                    current["copyrights"].append(copyright_text)

    if current:
        entries.append(current)
    return entries


def parse_ulissys_output(json_output):
    """
    Parse JSON output from ulissys.py.

    Input: { "filepath": ["// Copyright (C) 2024 ...", "// License: MIT"] }
    Returns list of dicts: [{file, license, copyrights}]
    """
    try:
        data = json.loads(json_output)
    except json.JSONDecodeError:
        print("[!] Failed to parse ulissys.py output as JSON.")
        return []

    entries = []
    copyright_re = re.compile(r"Copyright[:\s]*(.*)", re.IGNORECASE)
    license_re = re.compile(r"License[:\s]*(.*)", re.IGNORECASE)

    for filepath, lines in data.items():
        copyrights = []
        licenses = []
        for line in lines:
            cm = copyright_re.search(line)
            if cm:
                copyrights.append(cm.group(1).strip(" */"))
            lm = license_re.search(line)
            if lm:
                licenses.append(lm.group(1).strip(" */"))
        entries.append(
            {
                "file": filepath,
                "license": ", ".join(licenses) if licenses else "UNKNOWN",
                "copyrights": copyrights,
            }
        )
    return entries


# ---------------------------------------------------------------------------
# Agentic reasoning — swap this function body for a live LLM call later
# ---------------------------------------------------------------------------


def _regex_fallback(raw_license_str):
    """Fast regex fallback when Ollama is unavailable."""
    cleaned = re.sub(
        r"\s*\[generated file\]", "", raw_license_str, flags=re.IGNORECASE
    ).strip()
    cleaned = re.sub(r"^\*No copyright\*\s*", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s+and/or\s+", " AND ", cleaned, flags=re.IGNORECASE)

    if " AND " in cleaned:
        parts = [_regex_fallback(p.strip()) for p in cleaned.split(" AND ")]
        return " AND ".join(parts)

    normalized = cleaned.lower()
    mappings = [
        (r"gnu general public license v?3.*or later", "GPL-3+"),
        (r"gnu general public license v?3", "GPL-3"),
        (r"gnu general public license v?2.*or later", "GPL-2+"),
        (r"gnu general public license v?2", "GPL-2"),
        (r"gnu general public license", "GPL"),
        (r"gnu lesser general public license v?3.*or later", "LGPL-3+"),
        (r"gnu lesser general public license v?3", "LGPL-3"),
        (r"gnu lesser general public license v?2\.1.*or later", "LGPL-2.1+"),
        (r"gnu lesser general public license v?2\.1", "LGPL-2.1"),
        (r"gnu lesser general public license v?2.*or later", "LGPL-2+"),
        (r"gnu lesser general public license v?2", "LGPL-2"),
        (r"gnu free documentation license v?1\.3.*or later", "GFDL-1.3+"),
        (r"gnu free documentation license v?1\.3", "GFDL-1.3"),
        (r"gnu free documentation license v?1\.2.*or later", "GFDL-1.2+"),
        (r"gnu free documentation license v?1\.2", "GFDL-1.2"),
        (r"fsf unlimited license.*retention", "FSFULLR"),
        (r"fsf unlimited license", "FSFUL"),
        (r"fsf all permissive", "FSFAP"),
        (r"apache.*2", "Apache-2.0"),
        (r"mit", "MIT"),
        (r"x11", "X11"),
        (r"isc", "ISC"),
        (r"bsd.?2.?clause|simplified bsd", "BSD-2-Clause"),
        (r"bsd.?3.?clause|new bsd", "BSD-3-Clause"),
        (r"mpl.*2", "MPL-2.0"),
        (r"public domain", "public-domain"),
        (r"unknown", "UNKNOWN"),
    ]
    for pattern, dep5_id in mappings:
        if re.search(pattern, normalized):
            return dep5_id
    return cleaned if cleaned else "UNKNOWN"


def _call_ollama(raw_license_str):
    """Call local Ollama to map a raw license string to a DEP-5 identifier."""
    prompt = (
        "You are a Debian packaging expert. Map the following raw license string "
        "to a valid DEP-5 machine-readable license identifier (e.g. GPL-2+, MIT, Apache-2.0).\n"
        "Rules:\n"
        "- Reply with ONLY the short DEP-5 identifier, nothing else.\n"
        "- If multiple licenses, join with ' AND ' (e.g. GPL-2+ AND LGPL-2.1+).\n"
        "- If truly unknown, reply: UNKNOWN\n\n"
        f"Raw license string: {raw_license_str}\n"
        "DEP-5 identifier:"
    )
    payload = json.dumps(
        {
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
        }
    ).encode()

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read())
        return result.get("response", "").strip().split("\n")[0].strip()


def reason_license(raw_license_str):
    """
    Map a raw license string to a valid DEP-5 identifier using Ollama (gemma3).
    Validates the LLM result against known DEP-5 identifiers.
    Falls back to regex mapping if Ollama is unavailable or returns garbage.

    To swap the LLM provider, replace _call_ollama() with your preferred API call.
    Model and URL are configurable via ULISSYS_MODEL and OLLAMA_URL env vars.
    """
    try:
        llm_result = _call_ollama(raw_license_str)
        if llm_result and _is_valid_dep5(llm_result):
            return llm_result
        elif llm_result:
            print(
                f"[~] LLM returned unrecognised identifier {llm_result!r} for {raw_license_str!r} — using regex fallback",
                file=sys.stderr,
            )
    except (urllib.error.URLError, OSError, KeyError, json.JSONDecodeError):
        pass  # Ollama not running — fall through to regex
    return _regex_fallback(raw_license_str)


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def group_by_license(entries):
    """Group files by (dep5_license, frozenset_of_copyrights) per DEP-5 spec."""
    groups = {}

    for entry in entries:
        dep5_license = reason_license(entry["license"])
        key = (dep5_license, frozenset(entry["copyrights"]))
        if key not in groups:
            groups[key] = {
                "files": [],
                "license": dep5_license,
                "copyrights": entry["copyrights"],
            }
        groups[key]["files"].append(entry["file"])

    return groups


# ---------------------------------------------------------------------------
# DEP-5 output generation
# ---------------------------------------------------------------------------


def write_output(content, output_path):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[*] Written to: {output_path}")


def generate_dep5(groups, source):
    lines = []
    year = date.today().year

    # Header paragraph
    lines.append(
        "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/"
    )
    lines.append(f"Upstream-Name: {os.path.basename(source.rstrip('/'))}")
    lines.append(f"Upstream-Contact: FIXME <maintainer@example.com>")
    lines.append(f"Source: {source}")
    lines.append("")

    # Files paragraphs
    seen_licenses = []
    for info in groups.values():
        dep5_license = info["license"]
        files_glob = " ".join(sorted(info["files"])) if info["files"] else "*"
        copyrights = sorted(info["copyrights"]) or [f"{year} FIXME <upstream>"]

        lines.append(f"Files: {files_glob}")
        for c in copyrights:
            lines.append(f"Copyright: {c}")
        lines.append(f"License: {dep5_license}")
        lines.append("")

        if dep5_license not in seen_licenses:
            seen_licenses.append(dep5_license)

    # License stand-alone paragraphs
    for dep5_license in seen_licenses:
        lines.append(f"License: {dep5_license}")
        lines.append(f" Full license text available at:")
        lines.append(f" https://spdx.org/licenses/{dep5_license}.html")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    arg = parse_args()
    tmpdir = None

    try:
        if is_github_url(arg):
            tmpdir = tempfile.mkdtemp(prefix="ulissys_")
            clone_repo(arg, tmpdir)
            target_dir = tmpdir
        else:
            verify_directory(arg)
            target_dir = arg

        if is_licensecheck_installed():
            raw_output = run_licensecheck(target_dir)
            entries = parse_licensecheck_output(raw_output)
        else:
            raw_output = run_ulissys(target_dir)
            entries = parse_ulissys_output(raw_output)

        if not entries:
            print("[!] No license or copyright information found.")
            sys.exit(0)

        groups = group_by_license(entries)
        dep5 = generate_dep5(groups, arg)

        print("\n" + "=" * 60)
        print("debian/copyright (DEP-5)")
        print("=" * 60 + "\n")
        print(dep5)

        output_path = os.path.join(os.getcwd(), "debian", "copyright.draft")
        write_output(dep5, output_path)

    finally:
        if tmpdir and os.path.exists(tmpdir):
            shutil.rmtree(tmpdir)


if __name__ == "__main__":
    main()
