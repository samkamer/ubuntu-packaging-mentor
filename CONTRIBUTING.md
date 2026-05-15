# Contributing to Ubuntu AI Packaging Mentor

---

## Repository layout

```
mentor.py              — Orchestrator / main entry point
agents/
  brain.py             — LLM provider routing (ollama / copilot / demo)
  config.py            — Persistent INI config (XDG-aware)
  network.py           — Shared get_host_ip() helper
  preflight.py         — First-run environment detection and setup
  auditor.py           — DEP-5 copyright file generator
  detective.py         — Build-Depends scanner
  scribe.py            — Changelog drafter
  patch_manager.py     — Quilt patch workflow
  builder.py           — debuild wrapper + lintian integration
  linter.py            — Lintian policy checker
tests/                 — pytest suite (199 tests)
debian/                — Packaging tree for the .deb
demo/                  — asciinema cast files and record scripts
lab/sources/           — Sample source packages for integration testing
```

---

## Architecture

`mentor.py` is a thin orchestrator. It handles persona selection, the skill
menu, and per-persona explanation touchpoints. It does **not** contain any
packaging logic — that lives entirely in the agents.

Each agent follows the same contract:

- Accepts a `target` path (source directory or file) plus optional flags
- Returns a plain `dict` with at least `{"status": "success"|"error", "agent": "<name>"}`
- Never calls `sys.exit()` — raises exceptions or returns an error dict
- Never imports `brain` at module level — LLM calls are isolated inside agent functions

`brain.py` is initialised at import time, so **all `from agents.brain import`
calls in `mentor.py` must happen inside `main()`, after config has been loaded
and env vars set.**

---

## LLM providers

| `AI_PROVIDER` value | Description |
|---------------------|-------------|
| `ollama` (default) | Local Ollama via `http://<gateway>:11434` |
| `copilot` | GitHub Copilot API |
| `demo` | Canned responses — no LLM required |

Set via env var or config file `[llm] provider`. Env vars override config.

To add a canned demo response for a new agent, add an entry to the
`_DEMO_RESPONSES` dict in `brain.py` keyed by a substring of the system prompt.

---

## Running the tests

```bash
# All tests — uses demo mode, no LLM required
AI_PROVIDER=demo LLM_BUDGET=5 python3 -m pytest tests/ -q

# Single file
AI_PROVIDER=demo LLM_BUDGET=5 python3 -m pytest tests/test_builder.py -v
```

All tests must pass before opening a PR. The suite currently covers:

| File | What it tests |
|------|--------------|
| `test_auditor.py` | DEP-5 generation, SPDX normalisation, write/backup |
| `test_detective.py` | Header scanning, apt-file resolution, Build-Depends formatting |
| `test_scribe.py` | Changelog formatting, git log parsing, fallback stub |
| `test_patch_manager.py` | Diff generation, quilt workflow, dry-run mode |
| `test_builder.py` | debuild invocation, log parsing, lintian integration |
| `test_linter.py` | Lintian output parsing, E:/W: classification, LLM trigger |
| `test_config.py` | INI read/write, None omission, XDG path, corrupt file |
| `test_preflight.py` | Tool detection, Ollama probing, run_setup output and config |

---

## Adding a new agent

1. Create `agents/<name>.py` with a public function matching the agent contract above.
2. Add a canned demo response in `brain.py` → `_DEMO_RESPONSES`.
3. Wire the skill into `mentor.py` — add to `SKILLS`, import lazily inside `main()`, and add a dispatch branch in `run_skill()`.
4. Write `tests/test_<name>.py` — mock the external tool and `brain.ask`; cover success, error, and edge cases.
5. Add a row to the agents table in `README.md` and a full section below in this file.

---

## Building the .deb

```bash
# From the repo root
dpkg-buildpackage -us -uc -b

# Check the result
lintian ../ubu-dev-mentor_*.changes

# Install locally
sudo dpkg -i ../ubu-dev-mentor_*.deb
```

debhelper 13 is used. The `debian/rules` file installs:

- `mentor.py` → `/usr/bin/ubu-dev-mentor`
- `agents/*.py` → `/usr/lib/python3/dist-packages/agents/`
- `debian/ubu-dev-mentor.1` → man page

`debian/postinst` prints a first-run hint on fresh installs only (guards on
`$1 = configure` and empty `$2`).

---

## Agent API contracts

### auditor

```bash
python3 agents/auditor.py <source_dir> [--write] [--backup]
```

```json
{
  "status": "success",
  "data": "Format: https://www.debian.org/...\n...",
  "agent": "auditor",
  "written_to": "path/to/debian/copyright",
  "backed_up": null
}
```

Requires: `licensecheck` (`sudo apt install licensecheck`)

---

### detective

```bash
python3 agents/detective.py <source_dir> [--write]
```

```json
{
  "status": "success",
  "dependencies": ["libssl-dev", "zlib1g-dev"],
  "agent": "detective",
  "written_to": null
}
```

Requires: `apt-file` (`sudo apt install apt-file && sudo apt-file update`)

---

### scribe

```bash
python3 agents/scribe.py <source_dir> [<suite>] [--write] [--backup]
```

Default suite: `noble`. Maintainer read from `DEBFULLNAME`/`DEBEMAIL` env vars,
then `git config`.

```json
{
  "status": "success",
  "data": "hello (2.10-2) noble; urgency=medium\n\n  * ...\n\n -- ...\n",
  "agent": "scribe",
  "written_to": "path/to/debian/changelog",
  "backed_up": null
}
```

---

### patch_manager

```bash
# Dry run
python3 agents/patch_manager.py <source_dir> <patch-name> "<description>" --dry-run

# Apply
python3 agents/patch_manager.py <source_dir> fix-greeting "Fix the greeting"
```

**Success:**

```json
{
  "status": "success",
  "patch": "fix-greeting.patch",
  "file": "src/hello.c",
  "patch_path": "debian/patches/fix-greeting.patch",
  "agent": "patch_manager",
  "written_to": "path/to/debian/patches/fix-greeting.patch"
}
```

**Dry run:**

```json
{
  "status": "dry_run",
  "patch": "fix-greeting",
  "file": "src/hello.c",
  "diff": "--- a/src/hello.c\n+++ b/src/hello.c\n...",
  "agent": "patch_manager",
  "written_to": null
}
```

Requires: `quilt`, `patch` (`sudo apt install quilt patch`)

---

### builder

```bash
python3 agents/builder.py <source_dir>
```

Calls the **linter** agent automatically after a successful build.

**Success (clean lintian):**

```json
{
  "status": "success",
  "message": "Package built successfully.",
  "log_lines": 42,
  "lintian": {"status": "success", "errors": [], "warnings": [], "agent": "linter"},
  "agent": "builder"
}
```

**Success (lintian errors — status flips to error):**

```json
{
  "status": "error",
  "error_type": "lintian",
  "suggested_agent": "auditor",
  "suggested_command": "python3 agents/auditor.py <source_dir> --write",
  "analysis": "...",
  "lintian": {"status": "error", "errors": [{"tag": "no-copyright-file", "detail": ""}], "agent": "linter"},
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

| `error_type` | Detected by | `suggested_agent` |
|--------------|-------------|-------------------|
| `missing_dependency` | `dpkg-checkbuilddeps` output | `detective` |
| `compilation_error` | `make` / compiler output | `patch_manager` |
| `packaging_error` | `dh_*` / `dpkg-source` output | `auditor` |
| `lintian` | lintian `E:` output | `auditor` |
| `unknown` | LLM fallback | `detective` |

Requires: `debuild`, `lintian` (`sudo apt install devscripts lintian`)

---

### linter

```bash
python3 agents/linter.py <path/to/package.changes>
python3 agents/linter.py <path/to/package.deb>
```

**Clean:**

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

**With errors:**

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

| Severity | Meaning | Effect on `status` |
|----------|---------|-------------------|
| `E:` | Debian Policy violation | `error`, LLM analysis triggered |
| `W:` | Recommended practice not followed | `success`, listed under `warnings` |

Suppressed by default: `initial-upload-closes-no-bugs`, `groff-message`,
`debian-watch-file-is-missing`

Requires: `lintian` (`sudo apt install lintian`)

---

## Re-recording demos

```bash
# Full workflow demo (Beginner: Audit → Detect → Scribe)
bash demo/record.sh

# Persona contrast demo
bash demo/record_personas.sh
```
