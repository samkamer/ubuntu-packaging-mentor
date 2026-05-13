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
import time

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
    """Map each C header to candidate apt packages (filtered to -dev packages)."""
    results = {}
    total = len(headers)
    for i, header in enumerate(sorted(headers), 1):
        print(f"  [apt-file {i}/{total}] searching: {header}", file=sys.stderr)
        candidates = _apt_file_search(header)
        dev_pkgs = [p for p in candidates if p.endswith("-dev")]
        results[header] = dev_pkgs if dev_pkgs else candidates[:5]
    return results


def resolve_python_modules(modules: set[str]) -> dict[str, list[str]]:
    """Map each Python module to candidate apt packages via dist-packages path."""
    results = {}
    total = len(modules)
    for i, module in enumerate(sorted(modules), 1):
        print(f"  [apt-file {i}/{total}] searching Python: {module}", file=sys.stderr)
        candidates = _apt_file_search(f"dist-packages/{module}")
        if not candidates:
            candidates = _apt_file_search(f"site-packages/{module}")
        py_pkgs = [p for p in candidates if p.startswith("python3-")]
        results[module] = py_pkgs[:5] if py_pkgs else candidates[:3]
    return results


# ── Per-item LLM reasoning ────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are an expert Ubuntu/Debian package maintainer. "
    "Your task is to determine the correct Build-Depends entries for a debian/control file."
)

LLM_TIMEOUT_PER_CALL = 15
LLM_BUDGET_SECONDS   = 180


def _regex_best_candidate(candidates: list[str]) -> str | None:
    """Pick the best candidate from apt-file results without LLM."""
    dev = [p for p in candidates if p.endswith("-dev")]
    return dev[0] if dev else (candidates[0] if candidates else None)


def _ask_item(description: str, candidates: list[str], llm_budget: dict) -> str | None:
    """
    Ask the LLM to pick the single best -dev package for one header/module.
    Falls back to the top apt-file candidate if budget is exhausted or LLM is slow.
    """
    if not candidates:
        return None

    if llm_budget["remaining"] <= 0:
        print("  [~] LLM budget exhausted — using apt-file top candidate", file=sys.stderr)
        return _regex_best_candidate(candidates)

    user_prompt = (
        f"Which single Ubuntu/Debian -dev package should be listed in Build-Depends "
        f"to satisfy this dependency?\n\n"
        f"Dependency: {description}\n"
        f"apt-file candidates: {', '.join(candidates)}\n\n"
        "Reply with ONLY the package name (e.g. libssl-dev). "
        "If none of the candidates are suitable, reply: SKIP"
    )
    t0 = time.time()
    try:
        result = ask(_SYSTEM_PROMPT, user_prompt, label="", timeout=LLM_TIMEOUT_PER_CALL)
        llm_budget["remaining"] -= (time.time() - t0)
        pkg = result.strip().split()[0].rstrip(".,;")
        if pkg and pkg != "SKIP":
            return pkg
    except (RuntimeError, OSError):
        llm_budget["remaining"] -= (time.time() - t0)

    return _regex_best_candidate(candidates)


def resolve_with_llm(c_results: dict, py_results: dict,
                     go_modules: list[str], llm_budget: dict) -> list[str]:
    """
    Call the LLM once per unique header/module to select the best package.
    Returns a raw (possibly duplicate) list of package names.
    """
    collected = []
    all_items = (
        [(f"C/C++ header <{h}>", pkgs) for h, pkgs in c_results.items()] +
        [(f"Python module '{m}'", pkgs) for m, pkgs in py_results.items()] +
        [(f"Go module '{m}'", []) for m in go_modules]
    )
    total = len(all_items)

    for i, (description, candidates) in enumerate(all_items, 1):
        print(f"  [LLM {i}/{total}] {description}", file=sys.stderr)
        pkg = _ask_item(description, candidates, llm_budget)
        if pkg:
            collected.append(pkg)

    return collected


# ── Final deduplication LLM call ──────────────────────────────────────────────

def _parse_json_list(text: str) -> list[str] | None:
    """Extract a JSON list from an LLM response string."""
    match = re.search(r'\[.*?\]', text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, list):
                return [str(d) for d in result]
        except json.JSONDecodeError:
            pass
    return None


def deduplicate_with_llm(raw_deps: list[str]) -> list[str]:
    """
    Send the full collected package list to the LLM for a final deduplication pass.
    Removes redundant packages (e.g. libssl-dev when libcurl4-openssl-dev already pulls it),
    wrong entries, and SKIP placeholders. Falls back to simple set dedup on failure.
    """
    # Strip any SKIP placeholders from apt-file fallback
    candidates = sorted({d for d in raw_deps if d and d.upper() != "SKIP"})

    if not candidates:
        return []

    user_prompt = (
        "Below is a raw list of Ubuntu/Debian packages collected for Build-Depends. "
        "Please deduplicate it: remove redundant entries, non-dev packages that are "
        "pulled in transitively, and anything that isn't a real apt package name. "
        "Return ONLY a JSON list of the final Build-Depends packages.\n\n"
        f"Raw list: {json.dumps(candidates)}"
    )
    try:
        response = ask(_SYSTEM_PROMPT, user_prompt, label="Deduplicating Build-Depends")
        result = _parse_json_list(response)
        if result:
            return sorted(result)
        print("  [~] Could not parse dedup JSON — using sorted set", file=sys.stderr)
    except RuntimeError as e:
        print(f"  [~] Dedup LLM call failed ({e}) — using sorted set", file=sys.stderr)

    return candidates


# ── Main detect function ──────────────────────────────────────────────────────

def detect(source_dir: str, write: bool = False) -> dict:
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

    # Phase 1: resolve via apt-file
    c_results  = resolve_c_headers(c_headers)  if c_headers  else {}
    py_results = resolve_python_modules(py_modules) if py_modules else {}

    # Phase 2: per-item LLM calls (15s each, 3-min total budget)
    print(f"  [*] Phase 2: asking AI per dependency ...", file=sys.stderr)
    llm_budget = {"remaining": float(LLM_BUDGET_SECONDS)}
    raw_deps = resolve_with_llm(c_results, py_results, go_modules, llm_budget)

    # Phase 3: final deduplication LLM call
    print(f"  [*] Phase 3: deduplicating {len(raw_deps)} candidates ...", file=sys.stderr)
    deps = deduplicate_with_llm(raw_deps)

    written_to = None
    if write and deps:
        written_to = _write_control(source_dir, deps)

    return {"status": "success", "dependencies": deps, "agent": "detective",
            "written_to": written_to}


# ── debian/control writer ─────────────────────────────────────────────────────

def _write_control(source_dir: str, deps: list[str]) -> str:
    """
    Write or update debian/control with the detected Build-Depends.

    - If debian/control already exists, replaces the Build-Depends field.
    - If it doesn't exist, creates a minimal template.
    """
    debian_dir  = os.path.join(source_dir, "debian")
    control_path = os.path.join(debian_dir, "control")
    os.makedirs(debian_dir, exist_ok=True)

    build_depends = (
        "debhelper-compat (= 13),\n "
        + ",\n ".join(sorted(deps))
    )

    if os.path.isfile(control_path):
        with open(control_path, encoding="utf-8") as fh:
            content = fh.read()

        if re.search(r"^Build-Depends:", content, re.MULTILINE):
            # Replace existing Build-Depends field (may span multiple lines)
            content = re.sub(
                r"^Build-Depends:.*?(?=^\S|\Z)",
                f"Build-Depends: {build_depends}\n",
                content,
                flags=re.MULTILINE | re.DOTALL,
            )
        else:
            # Insert after the Source: paragraph's Standards-Version line (or at top)
            content = re.sub(
                r"(^Standards-Version:.*$)",
                rf"\1\nBuild-Depends: {build_depends}",
                content,
                flags=re.MULTILINE,
                count=1,
            )
            if "Build-Depends:" not in content:
                content = f"Build-Depends: {build_depends}\n\n" + content
    else:
        # Create a minimal debian/control template
        pkg_name = os.path.basename(os.path.abspath(source_dir))
        content = (
            f"Source: {pkg_name}\n"
            f"Section: misc\n"
            f"Priority: optional\n"
            f"Maintainer: FIXME <maintainer@example.com>\n"
            f"Build-Depends: {build_depends}\n"
            f"Standards-Version: 4.6.2\n"
            f"Rules-Requires-Root: no\n"
            f"\n"
            f"Package: {pkg_name}\n"
            f"Architecture: any\n"
            f"Depends: ${{shlibs:Depends}}, ${{misc:Depends}}\n"
            f"Description: FIXME short description\n"
            f" FIXME long description.\n"
        )

    with open(control_path, "w", encoding="utf-8") as fh:
        fh.write(content)

    return control_path


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Usage: python3 agents/detective.py <source_dir> [--write]", file=sys.stderr)
        sys.exit(1)

    result = detect(args[0], write="--write" in args)
    print(json.dumps(result, indent=2))
