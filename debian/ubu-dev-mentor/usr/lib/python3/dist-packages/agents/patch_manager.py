#!/usr/bin/env python3
"""
agents/patch_manager.py — Quilt Patch Manager

Pipeline:
  1. Accept a source directory, patch name, and plain-English fix description.
  2. Ask the LLM to identify which file in the source tree needs changing.
  3. Ask the LLM to produce a unified diff (or the corrected file content).
  4. Run the quilt workflow:
       quilt new <patch_name>.patch
       quilt add <file>
       apply the diff / replacement
       quilt refresh
  5. Return {"status": "success", "patch": "<name>.patch", "file": "<path>",
             "agent": "patch_manager", "patch_path": "<debian/patches/name.patch>"}

Usage:
    python3 agents/patch_manager.py <source_dir> <patch_name> "<description>"

    patch_name    Short slug, e.g. 'fix-greeting-logic' (no .patch extension needed)
    description   Plain English description of the fix to apply

Flags:
    (none — patch is always written; use --dry-run to preview only)
    --dry-run     Show LLM output without running quilt or touching the source
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.brain import ask, llm_budget_seconds

# ── Constants ──────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are an expert Debian/Ubuntu package maintainer with deep knowledge of "
    "C, Python, shell scripting, and the quilt patch management workflow."
)

# Unified diff header regex — detects a proper diff output from the LLM
_DIFF_RE = re.compile(r'^---\s+\S+.*^(\+\+\+\s+\S+)', re.MULTILINE | re.DOTALL)

# Files and directories to skip when building the source tree index
_SKIP_DIRS = {
    ".git", ".pc", "autom4te.cache",
    "win32", "windows", "macos", "osx",
    "test", "tests", "docs", "doc",
}
_SKIP_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".pdf", ".ps", ".dvi",
    ".o", ".a", ".so", ".lo", ".la",
    ".pyc", ".pyo",
    ".gz", ".bz2", ".xz", ".zip", ".tar",
    ".mo", ".gmo",    # compiled gettext catalogs
}

# Maximum source lines fed to the LLM for context (keep prompt manageable)
_MAX_CONTEXT_LINES = 150


# ── Source tree helpers ────────────────────────────────────────────────────────

def _build_file_index(source_dir: str) -> list[str]:
    """
    Return a sorted list of relative source file paths, filtered to text files
    that are likely candidates for patching.
    """
    paths = []
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs
                   if d not in _SKIP_DIRS and not d.startswith(".")]
        rel_root = os.path.relpath(root, source_dir)
        for fname in files:
            _, ext = os.path.splitext(fname.lower())
            if ext in _SKIP_EXTS:
                continue
            rel = os.path.normpath(os.path.join(rel_root, fname))
            if rel.startswith("debian" + os.sep):
                continue
            paths.append(rel)
    return sorted(paths)


def _read_file_context(source_dir: str, rel_path: str,
                       max_lines: int = _MAX_CONTEXT_LINES) -> str:
    """Read up to max_lines from a file, returning numbered lines."""
    full = os.path.join(source_dir, rel_path)
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        if len(lines) > max_lines:
            half = max_lines // 2
            lines = (lines[:half]
                     + [f"\n... ({len(lines) - max_lines} lines omitted) ...\n"]
                     + lines[-half:])
        return "".join(f"{i+1:4}: {l}" for i, l in enumerate(lines))
    except OSError:
        return "(could not read file)"


# ── LLM steps ─────────────────────────────────────────────────────────────────

def _identify_file(source_dir: str, description: str,
                   file_index: list[str]) -> str:
    """
    Ask the LLM which file in the index most likely needs changing.
    Returns a relative path string.
    """
    index_text = "\n".join(file_index[:300])  # cap to avoid huge prompts
    user_prompt = (
        f"You are looking at a source tree with the following files:\n\n"
        f"{index_text}\n\n"
        f"The fix needed is:\n  {description}\n\n"
        "Reply with ONLY the relative path of the single most relevant file "
        "to change (exactly as shown in the list above). "
        "Do not explain. Do not add any other text."
    )
    raw = ask(_SYSTEM_PROMPT, user_prompt, label="Identifying file to patch").strip()
    # Clean up any markdown backticks or surrounding quotes the LLM might add
    raw = raw.strip("`\"' \t\n")
    # Validate the LLM's answer is actually in the index
    if raw in file_index:
        return raw
    # Try a case-insensitive fallback match
    lower_map = {p.lower(): p for p in file_index}
    if raw.lower() in lower_map:
        return lower_map[raw.lower()]
    # Accept partial suffix match (LLM sometimes omits leading path components)
    for p in file_index:
        if p.endswith(raw) or raw.endswith(os.path.basename(p)):
            return p
    print(f"  [~] LLM suggested {raw!r} which is not in the index — "
          "using first file as fallback", file=sys.stderr)
    return file_index[0] if file_index else ""


def _generate_diff(source_dir: str, rel_path: str, description: str) -> str:
    """
    Ask the LLM to produce a unified diff for rel_path that implements
    the requested fix. Returns the raw LLM text.
    """
    context = _read_file_context(source_dir, rel_path)
    user_prompt = (
        f"Here is the current content of '{rel_path}':\n\n"
        f"{context}\n\n"
        f"Apply the following fix:\n  {description}\n\n"
        "Output ONLY a valid unified diff (--- / +++ / @@ format). "
        "Use 'a/{rel_path}' as the old file path and 'b/{rel_path}' as the new. "
        "Do not include any explanation — diff output only."
    ).replace("{rel_path}", rel_path)
    return ask(_SYSTEM_PROMPT, user_prompt, label="Generating patch diff")


# ── Diff application ───────────────────────────────────────────────────────────

def _extract_diff(llm_output: str) -> str:
    """
    Extract the unified diff block from LLM output, stripping any prose
    or markdown fences the LLM may have added.
    """
    # Strip markdown code fences
    cleaned = re.sub(r"```[a-z]*\n?", "", llm_output).strip()

    # Find the first --- line that starts a diff header
    lines = cleaned.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if line.startswith("--- "):
            start = i
            break
    if start is None:
        return cleaned  # hand back whatever the LLM gave us; patch will error
    return "".join(lines[start:])


def _apply_diff(source_dir: str, rel_path: str, diff_text: str) -> bool:
    """
    Write the diff to a temp file and apply it with `patch -p1`.
    Returns True on success.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".patch",
                                     delete=False, encoding="utf-8") as tf:
        tf.write(diff_text)
        tmp_path = tf.name
    try:
        result = subprocess.run(
            ["patch", "-p1", "--input", tmp_path],
            cwd=source_dir, capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"  [!] patch failed:\n{result.stderr}", file=sys.stderr)
            return False
        print(f"  [✓] patch applied cleanly", file=sys.stderr)
        return True
    finally:
        os.unlink(tmp_path)


# ── Quilt workflow ─────────────────────────────────────────────────────────────

def _run(cmd: list[str], cwd: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, env=env,
                          capture_output=True, text=True)


def _quilt_env(source_dir: str) -> dict:
    """Return os.environ extended with QUILT_PATCHES pointing at debian/patches."""
    env = os.environ.copy()
    env["QUILT_PATCHES"] = os.path.join(source_dir, "debian", "patches")
    return env


def run_quilt_workflow(source_dir: str, patch_name: str,
                       rel_path: str, diff_text: str) -> dict:
    """
    Execute the full quilt workflow:
      1. quilt new <patch_name>.patch
      2. quilt add <rel_path>
      3. Apply the diff via patch(1)
      4. quilt refresh

    Returns a result dict.
    """
    if shutil.which("quilt") is None:
        return {"status": "error",
                "error": "quilt not installed — run: sudo apt install quilt",
                "agent": "patch_manager"}

    patches_dir = os.path.join(source_dir, "debian", "patches")
    os.makedirs(patches_dir, exist_ok=True)

    env   = _quilt_env(source_dir)
    pname = patch_name if patch_name.endswith(".patch") else f"{patch_name}.patch"

    # 1. quilt new
    r = _run(["quilt", "new", pname], source_dir, env)
    if r.returncode != 0:
        return {"status": "error",
                "error": f"quilt new failed: {r.stderr.strip()}",
                "agent": "patch_manager"}
    print(f"  [✓] quilt new {pname}", file=sys.stderr)

    # 2. quilt add
    r = _run(["quilt", "add", rel_path], source_dir, env)
    if r.returncode != 0:
        return {"status": "error",
                "error": f"quilt add failed: {r.stderr.strip()}",
                "agent": "patch_manager"}
    print(f"  [✓] quilt add {rel_path}", file=sys.stderr)

    # 3. Apply the diff
    if not _apply_diff(source_dir, rel_path, diff_text):
        # Quilt has already registered the patch — pop it to leave a clean state
        _run(["quilt", "pop"], source_dir, env)
        return {"status": "error",
                "error": "patch(1) could not apply the LLM diff cleanly",
                "agent": "patch_manager"}

    # 4. quilt refresh
    r = _run(["quilt", "refresh"], source_dir, env)
    if r.returncode != 0:
        return {"status": "error",
                "error": f"quilt refresh failed: {r.stderr.strip()}",
                "agent": "patch_manager"}
    print(f"  [✓] quilt refresh", file=sys.stderr)

    patch_path = os.path.join("debian", "patches", pname)
    return {
        "status":     "success",
        "patch":      pname,
        "file":       rel_path,
        "patch_path": patch_path,
        "agent":      "patch_manager",
        "written_to": os.path.join(source_dir, patch_path),
    }


# ── Public API ─────────────────────────────────────────────────────────────────

def patch(source_dir: str, patch_name: str, description: str,
          dry_run: bool = False) -> dict:
    """
    Main entry point.

    Args:
        source_dir:  Path to the package source tree.
        patch_name:  Short slug for the patch (e.g. 'fix-greeting-logic').
        description: Plain-English description of the fix to apply.
        dry_run:     If True, show LLM output but do not run quilt or touch files.

    Returns:
        JSON-serialisable dict with status, patch, file, agent, written_to.
    """
    if not os.path.isdir(source_dir):
        return {"status": "error", "agent": "patch_manager",
                "error": f"Directory not found: {source_dir}"}

    print(f"\n  LLM budget: {llm_budget_seconds():.0f}s", file=sys.stderr)
    print(f"  [*] Scanning source tree ...", file=sys.stderr)
    file_index = _build_file_index(source_dir)
    if not file_index:
        return {"status": "error", "agent": "patch_manager",
                "error": "No patchable source files found in the source tree"}

    print(f"  [*] {len(file_index)} source files indexed", file=sys.stderr)

    # Step 1 — identify the file
    print(f"  [*] Asking LLM to identify the file to patch ...", file=sys.stderr)
    try:
        rel_path = _identify_file(source_dir, description, file_index)
    except RuntimeError as exc:
        return {"status": "error", "agent": "patch_manager",
                "error": f"LLM unavailable: {exc}"}

    if not rel_path:
        return {"status": "error", "agent": "patch_manager",
                "error": "Could not determine which file to patch"}
    print(f"  [✓] Target file: {rel_path}", file=sys.stderr)

    # Step 2 — generate the diff
    print(f"  [*] Asking LLM to generate the diff ...", file=sys.stderr)
    try:
        raw_diff = _generate_diff(source_dir, rel_path, description)
    except RuntimeError as exc:
        return {"status": "error", "agent": "patch_manager",
                "error": f"LLM unavailable: {exc}"}

    diff_text = _extract_diff(raw_diff)

    if dry_run:
        print("\n── Dry run — diff preview ────────────────────────────────────",
              file=sys.stderr)
        print(diff_text, file=sys.stderr)
        print("─────────────────────────────────────────────────────────────",
              file=sys.stderr)
        return {
            "status":     "dry_run",
            "patch":      patch_name,
            "file":       rel_path,
            "diff":       diff_text,
            "agent":      "patch_manager",
            "written_to": None,
        }

    # Step 3 — quilt workflow
    return run_quilt_workflow(source_dir, patch_name, rel_path, diff_text)


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Quilt Patch Manager — generate and apply a quilt patch via LLM",
    )
    parser.add_argument("source_dir",  help="Path to the package source tree")
    parser.add_argument("patch_name",  help="Patch slug, e.g. fix-greeting-logic")
    parser.add_argument("description", help="Plain-English description of the fix")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show LLM output without running quilt or touching files")
    args = parser.parse_args()

    result = patch(args.source_dir, args.patch_name, args.description,
                   dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] in ("success", "dry_run") else 1)
