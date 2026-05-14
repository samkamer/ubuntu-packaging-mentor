# Ubuntu AI Packaging Mentor

An AI-powered multi-agent CLI that automates Debian/Ubuntu packaging tasks with
adaptive explanations tailored to your experience level.

---

## Demos

Replay locally with:

```bash
# Full workflow (Beginner: Audit → Detect → Scribe)
asciinema play demo/demo.cast

# Persona contrast (Beginner · MOTU · CoreDev)
asciinema play demo/demo_personas.cast
```

---

## Agents

| Agent | Skill | What it does |
|-------|------------|-------------|
| **auditor** | 1&nbsp;Audit | Scans source tree with `licensecheck`, builds a DEP-5 `debian/copyright` |
| **detective** | 2&nbsp;Detect | Scans C headers + autoconf/CMake macros → generates `Build-Depends` |
| **scribe** | 3&nbsp;Scribe | Reads git log → drafts a `debian/changelog` entry |
| **patch_manager** | 4&nbsp;Patch | AI-identifies the file to change, generates a unified diff, applies it as a quilt patch in `debian/patches/` |
| **builder** | 5&nbsp;Build | Runs `debuild -us -uc -b`; on failure uses AI to classify the error and recommend the recovery agent |
| **linter** | — | Runs `lintian` on a `.changes` or `.deb`; AI explains any `E:` errors. Called automatically by builder after a successful build |

---

### 1 · auditor — DEP-5 Copyright File

Runs `licensecheck -r --copyright` on the source tree, normalises each raw license string to a valid DEP-5 SPDX identifier (via LLM with regex fallback), groups files by `(license, copyright)`, and emits a ready-to-use `debian/copyright`.

```bash
# Print to stdout
python3 agents/auditor.py <source_dir>

# Write to debian/copyright
python3 agents/auditor.py <source_dir> --write
```

**Returns:**

```json
{
  "status": "success",
  "data": "Format: https://www.debian.org/...\n...",
  "agent": "auditor",
  "written_to": "path/to/debian/copyright",
  "backed_up": null
}
```

Requires `licensecheck`: `sudo apt install licensecheck`

---

### 2 · detective — Build-Depends Scanner

Walks the source tree scanning `#include` directives (C/C++), Python imports, and Go `go.mod` requires. Resolves each header/module to its Ubuntu `-dev` package via `apt-file`, then asks the LLM to consolidate and format the final `Build-Depends` list.

```bash
# Print detected dependencies
python3 agents/detective.py <source_dir>

# Write to debian/control (appends Build-Depends field)
python3 agents/detective.py <source_dir> --write
```

**Returns:**

```json
{
  "status": "success",
  "dependencies": ["libssl-dev", "zlib1g-dev"],
  "agent": "detective",
  "written_to": null
}
```

Requires `apt-file`: `sudo apt install apt-file && sudo apt-file update`

---

### 3 · scribe — Changelog Drafter

Reads up to 30 git commit messages from the source tree, sends them to the LLM to produce a properly formatted `debian/changelog` stanza. Falls back to a Python-built stub if git history is absent or the LLM response is malformed.

```bash
# Print changelog entry
python3 agents/scribe.py <source_dir>

# Target a specific release suite (default: noble)
python3 agents/scribe.py <source_dir> jammy

# Prepend to debian/changelog
python3 agents/scribe.py <source_dir> noble --write
```

**Returns:**

```json
{
  "status": "success",
  "data": "hello (2.10-2) noble; urgency=medium\n\n  * ...\n\n -- ...\n",
  "agent": "scribe",
  "written_to": "path/to/debian/changelog",
  "backed_up": null
}
```

Maintainer name/email read from `DEBFULLNAME` / `DEBEMAIL` env vars, then `git config`.

---

### 4 · patch_manager — Quilt Patch Workflow

Asks the LLM to identify which source file needs changing, generate a unified diff, then runs the full quilt workflow (`quilt new` → `quilt add` → `patch -p1` → `quilt refresh`). The resulting patch lands in `debian/patches/`.

```bash
# Dry run — preview the LLM diff without touching any files
python3 agents/patch_manager.py <source_dir> <patch-name> "<description>" --dry-run

# Apply for real
python3 agents/patch_manager.py <source_dir> fix-greeting-logic "Fix the greeting to say Hello"
```

**Success:**

```json
{
  "status": "success",
  "patch": "fix-greeting-logic.patch",
  "file": "src/hello.c",
  "patch_path": "debian/patches/fix-greeting-logic.patch",
  "agent": "patch_manager",
  "written_to": "path/to/debian/patches/fix-greeting-logic.patch"
}
```

**Dry run:**

```json
{
  "status": "dry_run",
  "patch": "fix-greeting-logic",
  "file": "src/hello.c",
  "diff": "--- a/src/hello.c\n+++ b/src/hello.c\n...",
  "agent": "patch_manager",
  "written_to": null
}
```

Requires `quilt` and `patch`: `sudo apt install quilt patch`

---

### 5 · builder — Debian Build + AI Failure Analysis

Runs `debuild -us -uc -b` (unsigned binary build) inside `<source_dir>`. On success, automatically calls the **linter** agent on the generated `.changes` file. On build failure, extracts the last 20 lines of the build log, sends them to the LLM for classification, and returns the suggested recovery agent and command.

```bash
python3 agents/builder.py <source_dir>
```

**Success (no lintian errors):**

```json
{
  "status": "success",
  "message": "Package built successfully.",
  "log_lines": 42,
  "lintian": {"status": "success", "errors": [], "warnings": [...], "agent": "linter"},
  "agent": "builder"
}
```

**Success (lintian errors found — status flips to error):**

```json
{
  "status": "error",
  "error_type": "lintian",
  "suggested_agent": "auditor",
  "suggested_command": "python3 agents/auditor.py <source_dir> --write",
  "analysis": "...",
  "lintian": {"status": "error", "errors": [{"tag": "no-copyright-file", ...}], ...},
  "agent": "builder"
}
```

**Build failure:**

```json
{
  "status": "error",
  "error_type": "missing_dependency",
  "suggested_agent": "detective",
  "suggested_command": "python3 agents/detective.py <source_dir> --write",
  "analysis": "Build-Depends is missing libssl-dev.",
  "log_tail": "...",
  "agent": "builder"
}
```

| Error type | Detected by | Suggested agent |
|------------|-------------|----------------|
| Missing `-dev` package / unmet build deps | `dpkg-checkbuilddeps` output | `detective` |
| Compilation or syntax error | `make` / compiler output | `patch_manager` |
| Packaging file problem | `dh_*` / `dpkg-source` output | `auditor` |
| Lintian `E:` errors in built package | lintian output | `auditor` |
| Unknown | LLM fallback | `detective` |

Requires `debuild` and `lintian`: `sudo apt install devscripts lintian`

---

### 6 · linter — Lintian Policy Checker

Runs `lintian` on a `.changes` file (preferred) or `.deb`, suppresses known-noisy tags, and uses the LLM to explain any remaining `E:` errors and suggest fixes. Called automatically by builder, but can also be run standalone on any pre-built package.

```bash
python3 agents/linter.py <path/to/package.changes>
python3 agents/linter.py <path/to/package.deb>
```

**Clean package:**

```json
{
  "status": "success",
  "errors": [],
  "warnings": [{"tag": "no-manual-page", "detail": "usr/bin/tool"}],
  "analysis": null,
  "error_type": "lintian",
  "agent": "linter"
}
```

**Package with errors:**

```json
{
  "status": "error",
  "errors": [{"tag": "no-copyright-file", "detail": ""}],
  "warnings": [],
  "analysis": "no-copyright-file: debian/copyright is missing...",
  "error_type": "lintian",
  "agent": "linter"
}
```

| Severity | Meaning | Effect on status |
|----------|---------|-----------------|
| `E:` error | Debian Policy violation | `status: error`, LLM analysis triggered |
| `W:` warning | Recommended practice not followed | `status: success`, listed under `warnings` |

Suppressed by default: `initial-upload-closes-no-bugs`, `groff-message`, `debian-watch-file-is-missing`

Requires `lintian`: `sudo apt install lintian`

## Persona levels

| Persona | Explanation style |
|---------|------------------|
| **Beginner** | Plain-language concept explanations, file backups before writes |
| **MOTU** | Action summary with Debian Policy section references |
| **CoreDev** | Terse one-liners only, zero extra LLM calls |

## Quick start

```bash
# Default: Ollama/Gemma3 on local network
python3 mentor.py

# Demo mode (no LLM required)
AI_PROVIDER=demo python3 mentor.py

# Increase LLM budget for large packages
LLM_BUDGET=600 python3 mentor.py
```

## Re-record demos

```bash
# Full workflow demo
bash demo/record.sh

# Persona contrast demo
bash demo/record_personas.sh
```
