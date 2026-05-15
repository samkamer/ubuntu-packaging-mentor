#!/usr/bin/env python3
"""
agents/guardian.py — Security Guardian

Two sub-scanners:
  1. Secret Scanner:    recursively searches the source directory for exposed
                        credentials, private keys, and API tokens using regex.
  2. Hardening Auditor: runs 'blhc' (Build Log Hardening Check) against a
                        debuild log to identify missing compiler hardening flags
                        mandated by Ubuntu/Debian Policy §10.1 and
                        dpkg-buildflags(1).

Usage:
    python3 agents/guardian.py <source_dir> [build_log_path]

Returns JSON:
    {
        "status":            "success" | "error",
        "agent":             "guardian",
        "security_score":    0-100  (HEURISTIC — see scoring rules below),
        "verdict":           "pass" | "warn" | "fail",
        "vulnerabilities":   [
            {
                "type":        "secret" | "hardening",
                "severity":    "critical" | "high" | "medium",
                "match_type":  str,         # e.g. "private_key", "missing_relro"
                "file":        str,         # relative path  (secrets only)
                "line_number": int,         # 1-based        (secrets only)
                "description": str,         # hardening only
                "location":    str,         # hardening only
            },
            ...
        ],
        "secrets_found":     int,
        "missing_flags":     [str],         # flag category names blhc reported
        "hardening_status":  "clean" | "findings" | "unknown" | "skipped",
        "hardening_reason":  str,           # reason code for status
        "remediation_code":  str,           # deterministic debian/rules fix
        "llm_explanation":   str,           # LLM educational explanation
        "build_log_checked": bool,
    }

Scoring (HEURISTIC — not a policy compliance assessment):
    Start at 100.
    CRITICAL finding: -40   HIGH: -20   MEDIUM: -5
    Each missing hardening flag category: -10  (6 categories; max -60)
    Floor: 0.  If any CRITICAL finding: cap score at 20.
    Verdict: "fail" (score < 40 or critical), "warn" (< 70), "pass" (>= 70).

Secret scanning notes:
    - Symlinks are NOT followed; files whose realpath escapes source_dir skipped.
    - Binary files and files larger than 1 MiB are skipped.
    - NO secret content is emitted in findings — only file, line, and type.

Hardening note:
    hardening_status may be "unknown" when blhc is unavailable, times out, or
    returns output that cannot be mapped to known hardening categories.
    Non-authoritative heuristic scanning is intentionally avoided — blhc IS the
    authoritative tool for Debian Policy §10.1 compliance.
"""

import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.brain import ask

# ── Secret detection patterns ──────────────────────────────────────────────────
# Each entry: (compiled_regex, match_type, severity)
# Ordered CRITICAL → HIGH → MEDIUM; first match per line wins.
# Patterns target high-confidence indicators to minimise false positives.

_SECRET_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # Private keys (PEM headers) — highest confidence
    (re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
     "private_key", "critical"),
    # AWS key IDs: "AKIA" + 16 uppercase alphanumeric (long-term)
    (re.compile(r"AKIA[0-9A-Z]{16}"),
     "aws_access_key_id", "critical"),
    # AWS STS temporary keys start with "ASIA"
    (re.compile(r"ASIA[0-9A-Z]{16}"),
     "aws_sts_key", "critical"),
    # AWS secret access key: 40-char base64 value, with or without surrounding quotes
    (re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*[\"']?[A-Za-z0-9+/]{40}[\"']?"),
     "aws_secret_key", "critical"),
    # GitHub personal access tokens (classic: ghp_ prefix)
    (re.compile(r"ghp_[A-Za-z0-9_]{36}"),
     "github_pat_classic", "critical"),
    # GitHub fine-grained PATs (github_pat_ prefix, 82-char body)
    (re.compile(r"github_pat_[A-Za-z0-9_]{82}"),
     "github_pat_fine_grained", "critical"),
    # GitHub App installation tokens / secrets
    (re.compile(r"ghs_[A-Za-z0-9]{36}"),
     "github_app_secret", "critical"),
    # GitHub OAuth user-to-server tokens
    (re.compile(r"ghu_[A-Za-z0-9]{36}"),
     "github_oauth_token", "critical"),
    # Hard-coded passwords: password = "value" or password: 'value' (YAML/JSON/Python)
    (re.compile(r"(?i)password\s*[=:]\s*[\"'][^\"'\s]{6,}[\"']"),
     "hardcoded_password", "high"),
    # API keys/tokens in assignment form (= or :), optionally JSON-quoted key
    (re.compile(r"(?i)\"?api_key\"?\s*[=:]\s*[\"'][A-Za-z0-9_\-]{16,}[\"']"),
     "api_key", "high"),
    (re.compile(r"(?i)\"?api_token\"?\s*[=:]\s*[\"'][A-Za-z0-9_\-]{16,}[\"']"),
     "api_token", "high"),
    # Generic secret_key / access_token
    (re.compile(r"(?i)\"?secret_key\"?\s*[=:]\s*[\"'][^\"'\s]{8,}[\"']"),
     "secret_key", "medium"),
    (re.compile(r"(?i)\"?access_token\"?\s*[=:]\s*[\"'][A-Za-z0-9_\-\.]{20,}[\"']"),
     "access_token", "medium"),
]

# Binary extensions — skip these files entirely
_BINARY_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
    ".pdf", ".zip", ".gz", ".bz2", ".xz", ".tar", ".7z", ".rar",
    ".deb", ".rpm", ".so", ".a", ".o", ".pyc", ".pyo", ".class",
    ".jar", ".war", ".ear", ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".mp3", ".mp4", ".avi", ".mov", ".wav", ".ogg", ".flac",
    ".exe", ".dll", ".lib", ".bin", ".img", ".iso", ".vmdk",
})

# Directories to prune during tree walk
_SKIP_DIRS = frozenset({
    ".git", "__pycache__", ".tox", ".mypy_cache",
    "node_modules", ".hg", ".svn", ".bzr",
})

# Maximum file size to scan (1 MiB)
_MAX_FILE_SIZE = 1 * 1024 * 1024

# ── Hardening flag categories ──────────────────────────────────────────────────
# Per dpkg-buildflags(1) §FEATURE AREAS and Ubuntu/Debian Policy §10.1.
# Each key is the blhc category name; value is the canonical flag it enforces.

_HARDENING_CATEGORIES: dict[str, str] = {
    "stackprotector": "-fstack-protector-strong",
    "fortify":        "-D_FORTIFY_SOURCE=2",
    "format":         "-Werror=format-security",
    "relro":          "-Wl,-z,relro",
    "bindnow":        "-Wl,-z,now",
    "pie":            "-fPIE",
}

# ── Deterministic remediation ──────────────────────────────────────────────────

_REMEDIATION_HARDENING = """\
# Add to debian/rules (near the top, before any build rules):
export DEB_BUILD_MAINT_OPTIONS = hardening=+all

# The 'export' is required so dpkg-buildflags and debhelper subprocesses
# inherit the variable (debian/rules is a Makefile; unexported variables are
# not visible to child processes).
#
# This activates all hardening features via dpkg-buildflags(1):
#   stackprotector  -fstack-protector-strong  (stack buffer overflow detection)
#   fortify         -D_FORTIFY_SOURCE=2        (libc call safety checks)
#   format          -Wformat -Werror=format-security  (format string bugs as errors)
#   relro           -Wl,-z,relro               (partial RELRO: read-only relocations)
#   bindnow         -Wl,-z,now                 (full RELRO: resolve symbols at load)
#   pie             -fPIE -pie                 (position-independent executables)
#
# Reference: dpkg-buildflags(1), Debian Policy §10.1, Ubuntu Packaging Guide"""

_REMEDIATION_SECRETS = """\
# Remove exposed credentials from source immediately:
#   git rm --cached <file>
#   git commit -m "Remove accidentally committed secrets"
#   git filter-repo --path <file> --invert-paths  # rewrite history if already pushed
#
# Rotate any exposed keys/tokens immediately — treat them as compromised.
# Never embed credentials in source code; use environment variables or a
# secrets manager (e.g. Vault, AWS Secrets Manager, GitHub Secrets)."""


# ── Path safety helpers ────────────────────────────────────────────────────────

def _safe_realpath(path: str) -> str:
    try:
        return os.path.realpath(path)
    except OSError:
        return path


def _is_within(base_realpath: str, candidate: str) -> bool:
    """Return True if candidate's realpath is at or inside base_realpath."""
    real = _safe_realpath(candidate)
    sep = os.sep
    return real == base_realpath or real.startswith(base_realpath + sep)


# ── Secret Scanner ─────────────────────────────────────────────────────────────

def scan_secrets(source_dir: str) -> list[dict]:
    """
    Recursively scan source_dir for exposed secrets.

    Returns a list of findings, each containing:
        type, severity, match_type, file (relative path), line_number.

    No secret content is emitted — only location and type are reported.
    Symlinks are NOT followed.  Binary files and files >1 MiB are skipped.
    """
    findings: list[dict] = []
    base_real = _safe_realpath(source_dir)

    for dirpath, dirnames, filenames in os.walk(source_dir, followlinks=False):
        # Prune skip directories in-place (modifying dirnames affects os.walk)
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

        # Boundary check — skip subtrees that escape the source root
        if not _is_within(base_real, dirpath):
            dirnames.clear()
            continue

        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext in _BINARY_EXTS:
                continue

            fpath = os.path.join(dirpath, fname)

            # Skip symlinks that escape the source tree
            if os.path.islink(fpath) and not _is_within(base_real, fpath):
                continue

            # Skip large files
            try:
                if os.path.getsize(fpath) > _MAX_FILE_SIZE:
                    continue
            except OSError:
                continue

            rel_path = os.path.relpath(fpath, source_dir)
            _scan_file(fpath, rel_path, findings)

    return findings


def _scan_file(fpath: str, rel_path: str, findings: list[dict]) -> None:
    """Scan one file for secret patterns; append findings in-place."""
    try:
        with open(fpath, encoding="utf-8", errors="strict") as f:
            for lineno, line in enumerate(f, start=1):
                for pattern, match_type, severity in _SECRET_PATTERNS:
                    if pattern.search(line):
                        findings.append({
                            "type":        "secret",
                            "severity":    severity,
                            "match_type":  match_type,
                            "file":        rel_path,
                            "line_number": lineno,
                        })
                        break  # first match per line wins
    except (UnicodeDecodeError, OSError):
        # Binary content or unreadable file — skip silently
        pass


# ── Hardening Auditor ──────────────────────────────────────────────────────────

def run_blhc(log_path: str) -> tuple[list[str], str, str]:
    """
    Run blhc --all --debian against log_path.

    Returns:
        (missing_flag_categories, hardening_status, raw_blhc_output)

        hardening_status:
            "clean"    — blhc exited 0 (all flags present)
            "findings" — blhc exited non-zero and missing flag categories parsed
            "unknown"  — blhc not installed, timed out, or exited non-zero with
                         output that could not be matched to known flag categories

    If blhc is not installed, returns ([], "unknown", "").
    No heuristic fallback is performed: blhc is the authoritative tool for
    Debian Policy §10.1 compliance — guessing from raw log text would produce
    unreliable verdicts.
    """
    if not shutil.which("blhc"):
        return [], "unknown", ""

    try:
        result = subprocess.run(
            ["blhc", "--all", "--debian", log_path],
            capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return [], "unknown", str(exc)

    raw = (result.stdout + result.stderr).strip()

    if result.returncode == 0:
        return [], "clean", raw

    # Parse missing categories from blhc output.
    # blhc lines look like: "CFLAGS missing: -fstack-protector-strong"
    missing: list[str] = []
    for cat, flag in _HARDENING_CATEGORIES.items():
        if flag in raw:
            missing.append(cat)

    status = "findings" if missing else "unknown"
    return missing, status, raw


# ── Security Score ─────────────────────────────────────────────────────────────

_SEVERITY_DEDUCTIONS: dict[str, int] = {"critical": 40, "high": 20, "medium": 5}
_FLAG_DEDUCTION = 10


def _compute_score_and_verdict(
    findings: list[dict],
    missing_flags: list[str],
) -> tuple[int, str]:
    """
    Compute a HEURISTIC security score (0-100) and a verdict string.

    This score is a convenience indicator only — it is NOT a policy compliance
    assessment.  The 'verdict' field summarises pass/warn/fail status.
    """
    score = 100
    has_critical = False

    for f in findings:
        score -= _SEVERITY_DEDUCTIONS.get(f["severity"], 5)
        if f["severity"] == "critical":
            has_critical = True

    score -= len(missing_flags) * _FLAG_DEDUCTION
    score = max(0, score)

    if has_critical:
        score = min(score, 20)

    if has_critical or score < 40:
        verdict = "fail"
    elif score < 70:
        verdict = "warn"
    else:
        verdict = "pass"

    return score, verdict


# ── LLM Integration ────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are an Ubuntu Security Hardening Expert with deep knowledge of "
    "Debian/Ubuntu packaging security, compiler hardening flags (dpkg-buildflags), "
    "and secure software development practices. "
    "Explain security issues clearly and educationally for packaging developers."
)


def _ask_llm(findings: list[dict], missing_flags: list[str]) -> tuple[str, str]:
    """
    Ask the LLM to explain the detected security issues.

    Returns: (explanation, remediation_code)

    Remediation code is generated DETERMINISTICALLY (not by the LLM) to ensure
    it is always correct and policy-grounded.  The LLM provides the educational
    explanation of WHY each issue is dangerous.
    """
    has_secrets = bool(findings)
    has_hardening = bool(missing_flags)

    if not has_secrets and not has_hardening:
        return "No security issues found — source passed all checks.", ""

    # Build the user prompt.  Secrets: locations only, NO values.
    parts: list[str] = []

    if has_secrets:
        by_type: dict[str, list[str]] = {}
        for f in findings:
            by_type.setdefault(f["match_type"], []).append(
                f"{f['file']}:{f['line_number']}"
            )
        lines = []
        for mtype, locs in by_type.items():
            loc_str = ", ".join(locs[:3]) + (" ..." if len(locs) > 3 else "")
            lines.append(f"  - {mtype}: {loc_str}")
        parts.append("Exposed secrets detected (file:line only):\n" + "\n".join(lines))

    if has_hardening:
        flag_lines = "\n".join(
            f"  - {cat}: {_HARDENING_CATEGORIES[cat]}" for cat in missing_flags
        )
        parts.append(f"Missing compiler hardening flag categories:\n{flag_lines}")

    user_prompt = (
        "\n\n".join(parts) + "\n\n"
        "For each issue above:\n"
        "1. Explain in clear, educational language WHY it is dangerous "
        "(e.g. what Stack Smashing Protection prevents, why exposed credentials "
        "must be treated as compromised immediately).\n"
        "2. For hardening flags: confirm that "
        "'export DEB_BUILD_MAINT_OPTIONS = hardening=+all' in debian/rules resolves them "
        "(the 'export' is required so dpkg-buildflags subprocesses inherit the variable) "
        "and explain what each flag protects against.\n"
        "3. For exposed secrets: explain immediate remediation steps.\n"
        "Be concise and actionable."
    )

    # Determine remediation deterministically
    remediation_parts = []
    if has_hardening:
        remediation_parts.append(_REMEDIATION_HARDENING)
    if has_secrets:
        remediation_parts.append(_REMEDIATION_SECRETS)
    remediation = "\n\n".join(remediation_parts)

    try:
        explanation = ask(_SYSTEM_PROMPT, user_prompt, label="Security analysis")
    except RuntimeError as exc:
        explanation = (
            f"LLM unavailable ({exc}). "
            "Review findings manually using the remediation_code field."
        )

    return explanation, remediation


# ── Public API ─────────────────────────────────────────────────────────────────

def audit(source_dir: str, build_log: str | None = None) -> dict:
    """
    Run the full Guardian security audit pipeline.

    Args:
        source_dir: Path to the package source tree to scan for secrets.
        build_log:  Optional path to a debuild log for hardening analysis.

    Returns:
        JSON-serialisable dict (see module docstring).
    """
    if not os.path.isdir(source_dir):
        return {
            "status": "error",
            "agent":  "guardian",
            "error":  f"Source directory not found: {source_dir}",
        }

    # ── Phase 1: Secret scan ─────────────────────────────────────────────────
    print("  [*] Scanning for exposed secrets ...", file=sys.stderr)
    findings = scan_secrets(source_dir)
    secret_count = len(findings)

    if secret_count > 0:
        sev_counts: dict[str, int] = {}
        for f in findings:
            sev_counts[f["severity"]] = sev_counts.get(f["severity"], 0) + 1
        crit = sev_counts.get("critical", 0)
        print(
            f"  [!] {secret_count} secret(s) found ({crit} critical) "
            "— see vulnerabilities list",
            file=sys.stderr,
        )
    else:
        print("  [✓] No secrets found", file=sys.stderr)

    # ── Phase 2: Hardening audit ─────────────────────────────────────────────
    missing_flags: list[str] = []
    hardening_status = "skipped"
    hardening_reason = "skipped_no_build_log"
    build_log_checked = False

    if build_log:
        if not os.path.isfile(build_log):
            hardening_reason = "build_log_not_found"
            print(
                f"  [~] Build log not found: {build_log} — skipping hardening audit",
                file=sys.stderr,
            )
        else:
            print("  [*] Running hardening audit (blhc) ...", file=sys.stderr)
            missing_flags, hardening_status, raw_blhc = run_blhc(build_log)
            build_log_checked = True

            if hardening_status == "unknown":
                if shutil.which("blhc") is None:
                    hardening_reason = "blhc_not_installed"
                elif "timed out" in raw_blhc.lower():
                    hardening_reason = "blhc_timeout"
                else:
                    hardening_reason = "blhc_unrecognized_output"
                print(
                    "  [~] Hardening status unknown "
                    "(blhc unavailable, timed out, or output unrecognized)",
                    file=sys.stderr,
                )
                if hardening_reason == "blhc_not_installed":
                    print(
                        "      Install with: sudo apt install blhc",
                        file=sys.stderr,
                    )
            elif hardening_status == "findings":
                hardening_reason = "blhc_missing_flags"
                print(
                    f"  [!] Missing hardening: {', '.join(missing_flags)}",
                    file=sys.stderr,
                )
            else:
                hardening_reason = "blhc_clean"
                print("  [✓] All hardening flags present", file=sys.stderr)

    # ── Phase 3: Merge vulnerabilities ──────────────────────────────────────
    vulnerabilities: list[dict] = list(findings)
    for flag_cat in missing_flags:
        vulnerabilities.append({
            "type":        "hardening",
            "severity":    "high",
            "match_type":  f"missing_{flag_cat}",
            "description": f"Missing hardening flag: {_HARDENING_CATEGORIES[flag_cat]}",
            "location":    "build_log",
        })

    # ── Phase 4: Score, verdict, LLM ─────────────────────────────────────────
    score, verdict = _compute_score_and_verdict(findings, missing_flags)
    explanation, remediation = _ask_llm(findings, missing_flags)

    icon = "✓" if verdict == "pass" else "!"
    print(
        f"  [{icon}] Security score: {score}/100 (heuristic) — verdict: {verdict}",
        file=sys.stderr,
    )

    return {
        "status":            "success",
        "agent":             "guardian",
        "security_score":    score,
        "verdict":           verdict,
        "vulnerabilities":   vulnerabilities,
        "secrets_found":     secret_count,
        "missing_flags":     missing_flags,
        "hardening_status":  hardening_status,
        "hardening_reason":  hardening_reason,
        "remediation_code":  remediation,
        "llm_explanation":   explanation,
        "build_log_checked": build_log_checked,
    }


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Security Guardian — scans for exposed secrets and checks "
            "build hardening flags (requires blhc for hardening audit)."
        ),
    )
    parser.add_argument("source_dir", help="Path to the package source tree")
    parser.add_argument(
        "build_log", nargs="?", default=None,
        help="Optional path to a debuild log for hardening analysis",
    )
    args = parser.parse_args()

    result = audit(args.source_dir, args.build_log)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] == "success" else 1)
