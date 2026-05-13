#!/usr/bin/env python3
"""
agents/scribe.py — Changelog Scribe

Pipeline:
  1. Read the package name and last known version from debian/changelog (if present)
     or debian/control, falling back to the directory basename.
  2. Collect the last N git commit messages from the source directory.
     If no git history exists, prepare an 'Initial release' scaffold.
  3. Get the current RFC 5322 timestamp via `date -R`.
  4. Send commit messages to the LLM asking it to produce a single
     debian/changelog stanza in the standard format.
  5. Validate the entry has the expected stanza shape; fall back to a
     Python-built stub if the LLM response is malformed.
  6. Optionally write the entry to the top of debian/changelog.
  7. Return {"status": "success", "data": "<entry text>", "agent": "scribe",
             "written_to": path_or_none}

Usage:
    python3 agents/scribe.py <source_dir> [release] [--write]

    release  Ubuntu/Debian target suite name (default: noble)
    --write  Prepend the entry to <source_dir>/debian/changelog
"""

import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.brain import ask, backup_file

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_RELEASE  = "noble"
MAX_COMMITS      = 30       # git log lines to feed the LLM
DEFAULT_NAME     = "Your Name"
DEFAULT_EMAIL    = "you@example.com"
MAINTAINER_ENV   = "DEBEMAIL"   # standard Debian env var
MAINTAINER_NAME  = "DEBFULLNAME"

# Matches the first line of a changelog stanza:
#   package (version) release; urgency=medium
_STANZA_RE = re.compile(
    r'^(?P<pkg>[\w.+\-]+)\s+\((?P<ver>[^)]+)\)\s+(?P<rel>\S+)\s*;\s*urgency=\S+',
)

_SYSTEM_PROMPT = (
    "You are an Ubuntu Release Manager with deep knowledge of Debian packaging policy. "
    "Your task is to write a single debian/changelog entry in the exact standard format."
)

# ── Source metadata helpers ───────────────────────────────────────────────────

def _get_maintainer() -> tuple[str, str]:
    """Return (name, email) from env vars or git config, with sane defaults."""
    email = os.environ.get(MAINTAINER_ENV, "").strip()
    name  = os.environ.get(MAINTAINER_NAME, "").strip()

    if not email or not name:
        try:
            cfg_name  = subprocess.run(
                ["git", "config", "user.name"],  capture_output=True, text=True, timeout=5
            ).stdout.strip()
            cfg_email = subprocess.run(
                ["git", "config", "user.email"], capture_output=True, text=True, timeout=5
            ).stdout.strip()
            name  = name  or cfg_name  or DEFAULT_NAME
            email = email or cfg_email or DEFAULT_EMAIL
        except (OSError, subprocess.TimeoutExpired):
            name  = name  or DEFAULT_NAME
            email = email or DEFAULT_EMAIL

    return name, email


def _get_package_name(source_dir: str) -> str:
    """
    Try to read Source: from debian/control; fall back to directory basename.
    """
    control = os.path.join(source_dir, "debian", "control")
    if os.path.isfile(control):
        try:
            with open(control, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.lower().startswith("source:"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return os.path.basename(os.path.abspath(source_dir))


def _get_last_version(source_dir: str) -> str:
    """
    Read the latest version from debian/changelog, or return '1.0-1'.
    Bumps the Debian revision by 1 to produce the new version.
    """
    changelog = os.path.join(source_dir, "debian", "changelog")
    if not os.path.isfile(changelog):
        return "1.0-1"
    try:
        with open(changelog, encoding="utf-8", errors="replace") as fh:
            first_line = fh.readline()
        m = _STANZA_RE.match(first_line)
        if m:
            ver = m.group("ver")
            # Bump Debian revision: 1.0-1 → 1.0-2
            parts = ver.rsplit("-", 1)
            if len(parts) == 2 and parts[1].isdigit():
                return f"{parts[0]}-{int(parts[1]) + 1}"
            return ver + "-1"
    except OSError:
        pass
    return "1.0-1"


# ── Git log helpers ───────────────────────────────────────────────────────────

def _get_git_log(source_dir: str, max_commits: int = MAX_COMMITS) -> list[str]:
    """
    Return the last `max_commits` git commit subject lines from source_dir.

    Only reads git history if source_dir is itself a git repository root
    (contains a .git entry). This prevents git from walking up into a
    parent repo when the package source has no git history of its own.
    """
    abs_src = os.path.realpath(source_dir)
    # Require an owned .git so we never accidentally read a parent repo
    if not os.path.exists(os.path.join(abs_src, ".git")):
        return []
    try:
        result = subprocess.run(
            ["git", "log", f"--max-count={max_commits}",
             "--pretty=format:* %s  (%h  %ad)", "--date=short"],
            cwd=abs_src, capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().splitlines()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return []


def _get_rfc_date() -> str:
    """Return current date in RFC 5322 format (required by debian/changelog)."""
    try:
        result = subprocess.run(
            ["date", "-R"], capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        # Fallback: format manually (less accurate TZ, but valid)
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")


# ── LLM call ──────────────────────────────────────────────────────────────────

def _build_entry_with_llm(
    pkg: str, version: str, release: str,
    commits: list[str], rfc_date: str,
    name: str, email: str,
) -> str:
    """Ask the LLM to write the changelog stanza. Returns raw LLM text."""
    if commits:
        commit_block = "\n".join(commits)
        user_prompt = (
            f"Write a single debian/changelog entry for the following package update.\n\n"
            f"Package   : {pkg}\n"
            f"Version   : {version}\n"
            f"Release   : {release}\n"
            f"Maintainer: {name} <{email}>\n"
            f"Date      : {rfc_date}\n\n"
            f"Git commits to summarise:\n{commit_block}\n\n"
            "Output ONLY the changelog entry text, starting with the package line "
            "and ending with the ' -- Maintainer <email>  Date' trailer. "
            "Use exactly 2-space indent for bullet items. Do not add any explanation."
        )
    else:
        user_prompt = (
            f"Write an 'Initial release' debian/changelog entry.\n\n"
            f"Package   : {pkg}\n"
            f"Version   : {version}\n"
            f"Release   : {release}\n"
            f"Maintainer: {name} <{email}>\n"
            f"Date      : {rfc_date}\n\n"
            "Output ONLY the changelog entry text, starting with the package line "
            "and ending with the ' -- Maintainer <email>  Date' trailer. "
            "Use exactly 2-space indent for bullet items. Do not add any explanation."
        )
    return ask(_SYSTEM_PROMPT, user_prompt, label="Drafting changelog entry")


# ── Entry validation / fallback ───────────────────────────────────────────────

# Matches bullet lines the LLM tends to produce: "* foo", "- foo", "  * foo"
_BULLET_RE = re.compile(r'^\s*[\*\-]\s+(.+)')
# Matches the trailer line: " -- Name <email>  Date"
_TRAILER_RE = re.compile(r'^\s*--\s+.+<.+>\s+\w{3},')


def _extract_bullets(text: str) -> list[str]:
    """Pull bullet item text out of a free-form LLM response."""
    bullets = []
    for line in text.splitlines():
        m = _BULLET_RE.match(line)
        if m:
            bullets.append(m.group(1).strip())
    return bullets


def _validate_entry(text: str, pkg: str, version: str,
                    release: str, name: str, email: str, rfc_date: str) -> str:
    """
    Validate the LLM response as a debian/changelog stanza.

    Strategy (in order):
      1. First line already matches the stanza header  → use as-is.
      2. The text contains bullet items                → rescue those bullets,
         build a correctly-structured stanza around them.
      3. Nothing useful                               → plain stub with
                                                        'Initial release.'
    """
    lines = text.strip().splitlines()

    # 1. Looks correct already
    if lines and _STANZA_RE.match(lines[0]):
        return text.strip() + "\n"

    print("  [~] LLM response header did not match — attempting to rescue bullets",
          file=sys.stderr)

    # 2. Rescue any bullet points the LLM wrote
    bullets = _extract_bullets(text)
    if bullets:
        print(f"  [~] Rescued {len(bullets)} bullet(s) from LLM output", file=sys.stderr)
        return _build_stub(pkg, version, release, name, email, rfc_date, bullets)

    # 3. Nothing useful — minimal stub
    print("  [~] No bullets found either — using bare stub", file=sys.stderr)
    return _build_stub(pkg, version, release, name, email, rfc_date)


def _build_stub(pkg: str, version: str, release: str,
                name: str, email: str, rfc_date: str,
                items: list[str] | None = None) -> str:
    """Build a syntactically valid minimal changelog stanza."""
    bullet_lines = "\n".join(f"  * {item}" for item in (items or ["Initial release."]))
    return (
        f"{pkg} ({version}) {release}; urgency=medium\n\n"
        f"{bullet_lines}\n\n"
        f" -- {name} <{email}>  {rfc_date}\n"
    )


# ── debian/changelog writer ───────────────────────────────────────────────────

def _write_changelog(source_dir: str, entry: str) -> str:
    """
    Prepend the new entry to debian/changelog (creates the file if absent).
    Returns the path written.
    """
    debian_dir = os.path.join(source_dir, "debian")
    os.makedirs(debian_dir, exist_ok=True)
    changelog_path = os.path.join(debian_dir, "changelog")

    existing = ""
    if os.path.isfile(changelog_path):
        with open(changelog_path, encoding="utf-8", errors="replace") as fh:
            existing = fh.read()

    with open(changelog_path, "w", encoding="utf-8") as fh:
        fh.write(entry)
        if existing:
            fh.write("\n")
            fh.write(existing)

    return changelog_path


# ── Public API ────────────────────────────────────────────────────────────────

def scribe(source_dir: str, release: str = DEFAULT_RELEASE,
           write: bool = False, backup: bool = False) -> dict:
    """
    Main entry point.

    Args:
        source_dir: Path to the package source tree.
        release:    Target Ubuntu/Debian suite (e.g. 'noble').
        write:      If True, prepend entry to debian/changelog.

    Returns:
        JSON-serialisable dict with status, data, agent, written_to.
    """
    if not os.path.isdir(source_dir):
        return {"status": "error", "data": None, "agent": "scribe",
                "error": f"Directory not found: {source_dir}"}

    pkg      = _get_package_name(source_dir)
    version  = _get_last_version(source_dir)
    name, email = _get_maintainer()
    rfc_date = _get_rfc_date()
    commits  = _get_git_log(source_dir)

    print(f"  [*] Package : {pkg}  →  {version}  ({release})", file=sys.stderr)
    if commits:
        print(f"  [*] Found {len(commits)} git commits", file=sys.stderr)
    else:
        print("  [*] No git history — drafting Initial release entry", file=sys.stderr)

    try:
        raw = _build_entry_with_llm(pkg, version, release, commits,
                                     rfc_date, name, email)
        entry = _validate_entry(raw, pkg, version, release, name, email, rfc_date)
    except RuntimeError as exc:
        print(f"  [~] LLM unavailable ({exc}) — using stub", file=sys.stderr)
        items = [line.lstrip("* ").strip() for line in commits[:5]] if commits else None
        entry = _build_stub(pkg, version, release, name, email, rfc_date, items)

    written_to = None
    backed_up  = None
    if write:
        if backup:
            changelog_path = os.path.join(source_dir, "debian", "changelog")
            backed_up = backup_file(changelog_path)
            if backed_up:
                print(f"  [✓] Backup: {backed_up}", file=sys.stderr)
        written_to = _write_changelog(source_dir, entry)
        print(f"  [✓] Written to {written_to}", file=sys.stderr)

    return {"status": "success", "data": entry, "agent": "scribe",
            "written_to": written_to, "backed_up": backed_up}


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--write"]
    do_write = "--write" in sys.argv

    if not args:
        print("Usage: python3 agents/scribe.py <source_dir> [release] [--write]",
              file=sys.stderr)
        sys.exit(1)

    source = args[0]
    rel    = args[1] if len(args) > 1 else DEFAULT_RELEASE
    result = scribe(source, release=rel, write=do_write)
    print(json.dumps(result, indent=2))
