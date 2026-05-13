#!/usr/bin/env python3
"""
agents/detective.py — Dependency Detective

Scans a source directory for external dependencies and resolves them to
Ubuntu Build-Depends packages using apt-file + LLM reasoning.

Supported source types:
  C/C++  : #include <header.h> → apt-file search → -dev packages
  Python : import / from ... import → apt-file search dist-packages → python3-* packages
  Go     : go.mod require lines → passed directly to LLM

Usage:
    python3 agents/detective.py <source_dir>

Returns JSON:
    {"status": "success", "dependencies": [...], "agent": "detective"}
"""

import ast
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.brain import ask

# ── Language detection / scanning ─────────────────────────────────────────────

C_EXTS    = {".c", ".cpp", ".cc", ".cxx", ".h", ".hpp"}
PY_EXTS   = {".py"}

# Python stdlib module names (3.11+)
try:
    import sys as _sys
    _STDLIB = getattr(_sys, "stdlib_module_names", set())
except Exception:
    _STDLIB = set()

# Well-known stdlib roots for older Pythons
_STDLIB_FALLBACK = {
    "os", "sys", "re", "json", "math", "io", "time", "datetime", "pathlib",
    "subprocess", "threading", "logging", "unittest", "typing", "collections",
    "itertools", "functools", "shutil", "tempfile", "hashlib", "base64",
    "urllib", "http", "email", "html", "xml", "csv", "sqlite3", "socket",
    "ssl", "struct", "copy", "abc", "enum", "dataclasses", "contextlib",
    "asyncio", "concurrent", "multiprocessing", "signal", "gc", "inspect",
    "importlib", "pkgutil", "platform", "stat", "glob", "fnmatch",
}


def _is_stdlib(module: str) -> bool:
    return module in _STDLIB or module in _STDLIB_FALLBACK


def scan_c_headers(source_dir: str) -> set[str]:
    """Extract unique #include <...> header paths from C/C++ source files."""
    headers = set()
    pattern = re.compile(r'#\s*include\s+<([^>]+)>')
    for root, _, files in os.walk(source_dir):
        for fname in files:
            if os.path.splitext(fname)[1].lower() in C_EXTS:
                path = os.path.join(root, fname)
                try:
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        for i, line in enumerate(fh):
                            if i > 200:  # only scan file headers
                                break
                            m = pattern.search(line)
                            if m:
                                headers.add(m.group(1))
                except OSError:
                    pass
    return headers


def scan_python_imports(source_dir: str) -> set[str]:
    """Extract unique third-party top-level module names from Python files."""
    modules = set()
    for root, _, files in os.walk(source_dir):
        for fname in files:
            if os.path.splitext(fname)[1] in PY_EXTS:
                path = os.path.join(root, fname)
                try:
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        source = fh.read()
                    tree = ast.parse(source, filename=path)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                modules.add(alias.name.split(".")[0])
                        elif isinstance(node, ast.ImportFrom):
                            if node.module and node.level == 0:
                                modules.add(node.module.split(".")[0])
                except (OSError, SyntaxError):
                    pass
    return {m for m in modules if m and not _is_stdlib(m)}


def scan_go_modules(source_dir: str) -> list[str]:
    """Parse go.mod require blocks and return module paths (without versions)."""
    go_mod = os.path.join(source_dir, "go.mod")
    if not os.path.isfile(go_mod):
        return []
    modules = []
    in_require = False
    try:
        with open(go_mod, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("require ("):
                    in_require = True
                    continue
                if in_require:
                    if line == ")":
                        in_require = False
                    elif line:
                        modules.append(line.split()[0])
                elif line.startswith("require ") and not line.endswith("("):
                    modules.append(line.split()[1])
    except OSError:
        pass
    return modules


# ── apt-file resolution ───────────────────────────────────────────────────────

def _apt_file_search(query: str) -> list[str]:
    """Run apt-file search and return matching package names."""
    if shutil.which("apt-file") is None:
        return []
    result = subprocess.run(
        ["apt-file", "search", query],
        capture_output=True, text=True, timeout=15,
    )
    packages = []
    for line in result.stdout.splitlines():
        if ": " in line:
            pkg = line.split(": ")[0].strip()
            packages.append(pkg)
    return packages


def resolve_c_headers(headers: set[str]) -> dict[str, list[str]]:
    """Map each C header to candidate apt packages (filtered to -dev or /usr/include/)."""
    results = {}
    total = len(headers)
    for i, header in enumerate(sorted(headers), 1):
        print(f"  [apt-file {i}/{total}] searching: {header}", file=sys.stderr)
        candidates = _apt_file_search(header)
        # Prefer packages that look like dev packages
        dev_pkgs = [p for p in candidates if p.endswith("-dev")]
        results[header] = dev_pkgs if dev_pkgs else candidates[:5]
    return results


def resolve_python_modules(modules: set[str]) -> dict[str, list[str]]:
    """Map each Python module to candidate apt packages via dist-packages path."""
    results = {}
    total = len(modules)
    for i, module in enumerate(sorted(modules), 1):
        print(f"  [apt-file {i}/{total}] searching Python: {module}", file=sys.stderr)
        # Search for the module path inside dist-packages or site-packages
        candidates = _apt_file_search(f"dist-packages/{module}")
        if not candidates:
            candidates = _apt_file_search(f"site-packages/{module}")
        py_pkgs = [p for p in candidates if p.startswith("python3-")]
        results[module] = py_pkgs[:5] if py_pkgs else candidates[:3]
    return results


# ── LLM reasoning ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are an expert Ubuntu/Debian package maintainer. "
    "Your task is to determine the correct Build-Depends entries for a debian/control file."
)

def ask_llm_for_deps(scan_summary: dict) -> list[str]:
    """Send the scan results to the LLM and parse its JSON list response."""
    user_prompt = (
        "Based on these source headers and apt-file results, provide a deduplicated "
        "list of Ubuntu -dev packages needed for Build-Depends. "
        "Format the output as a JSON list, for example: [\"libssl-dev\", \"zlib1g-dev\"]\n\n"
        f"Scan results:\n{json.dumps(scan_summary, indent=2)}"
    )
    response = ask(_SYSTEM_PROMPT, user_prompt, label="Asking AI for Build-Depends")

    # Extract JSON list from the response
    match = re.search(r'\[.*?\]', response, re.DOTALL)
    if match:
        try:
            deps = json.loads(match.group())
            if isinstance(deps, list):
                return [str(d) for d in deps]
        except json.JSONDecodeError:
            pass

    # Fallback: extract anything that looks like a package name
    print("  [~] Could not parse JSON from LLM — extracting package names with regex",
          file=sys.stderr)
    return sorted(set(re.findall(r'\b[\w][\w\-.]+(?:-dev|lib[\w-]+)\b', response)))


# ── Main detect function ──────────────────────────────────────────────────────

def detect(source_dir: str) -> dict:
    if not os.path.isdir(source_dir):
        return {"status": "error", "dependencies": None, "agent": "detective",
                "error": f"Directory not found: {source_dir}"}

    if shutil.which("apt-file") is None:
        return {"status": "error", "dependencies": None, "agent": "detective",
                "error": "apt-file not installed. Run: sudo apt install apt-file && sudo apt-file update"}

    print(f"  [*] Scanning {source_dir} ...", file=sys.stderr)

    c_headers  = scan_c_headers(source_dir)
    py_modules = scan_python_imports(source_dir)
    go_modules = scan_go_modules(source_dir)

    print(f"  [*] Found: {len(c_headers)} C headers, "
          f"{len(py_modules)} Python modules, "
          f"{len(go_modules)} Go modules", file=sys.stderr)

    if not c_headers and not py_modules and not go_modules:
        return {"status": "success", "dependencies": [], "agent": "detective",
                "note": "No external dependencies found in source tree."}

    # Resolve via apt-file
    scan_summary = {}
    if c_headers:
        scan_summary["c_headers"] = resolve_c_headers(c_headers)
    if py_modules:
        scan_summary["python_modules"] = resolve_python_modules(py_modules)
    if go_modules:
        scan_summary["go_modules"] = go_modules

    # Ask LLM to reason over the results
    deps = ask_llm_for_deps(scan_summary)

    return {"status": "success", "dependencies": deps, "agent": "detective"}


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Usage: python3 agents/detective.py <source_dir>", file=sys.stderr)
        sys.exit(1)

    result = detect(args[0])
    print(json.dumps(result, indent=2))
