# Test Suite

130 regression tests covering all five agents and shared utilities.

## Quick start

```bash
cd /home/hackathon/Ubu-dev-mentor
AI_PROVIDER=demo LLM_BUDGET=5 python3 -m pytest tests/ -v
```

### Why `AI_PROVIDER=demo`?

The integration tests call the real agent code end-to-end, which internally
invokes `brain.ask()`. Setting `AI_PROVIDER=demo` makes those calls use
pre-canned responses instead of hitting the Ollama endpoint. This keeps tests:

- **Fast** — no ~120 s LLM round-trips
- **Deterministic** — same output every run
- **Offline** — no network dependency

The unit tests (`test_brain`, `test_auditor`, `test_detective`, `test_scribe`)
never call the LLM and don't require the env var, but it's harmless to set it
globally.

### Why `LLM_BUDGET=5`?

Some agent code checks the remaining LLM budget before deciding whether to
call the LLM. Setting it to 5 s exhausts the budget almost immediately,
ensuring the regex/stub fallback paths are exercised rather than waiting for a
real LLM timeout.

## Prerequisites

| Tool | Required for | Install |
|------|-------------|---------|
| `pytest` | all tests | `pip install pytest --break-system-packages` |
| `licensecheck` | `TestAuditorIntegration` | `sudo apt install licensecheck` |
| `apt-file` | `TestDetectiveIntegration` | `sudo apt install apt-file` |
| `quilt`, `patch` | `TestPatchManagerIntegration` (future) | `sudo apt install quilt patch` |

Integration tests **skip automatically** (not fail) when their required tool
is absent, so `pytest` always exits cleanly on a minimal install.

## Test files

| File | Tests | What's covered |
|------|------:|----------------|
| `test_brain.py` | 11 | `llm_budget_seconds()` — env var parsing, defaults; `backup_file()` — creates `.bak`, preserves content, timestamped name, no clobber |
| `test_auditor.py` | 22 | `_is_valid_dep5()`, `_regex_fallback()` (15 license mappings), `parse_licensecheck_output()`, `build_dep5()` |
| `test_detective.py` | 18 | `_is_stdlib()`, `_should_skip_dir()`, `_PLATFORM_SKIP` regex, `scan_build_system()` (autoconf/cmake/meson/quilt), `scan_autoconf_deps()` |
| `test_scribe.py` | 17 | `_get_package_name()`, `_get_last_version()` (Debian revision bump), `_get_git_log()` (`.git` guard), `_extract_bullets()`, `_validate_entry()` (3-tier fallback), `_build_stub()` |
| `test_patch_manager.py` | 21 | `_build_file_index()` (skip dirs/extensions/debian/, sort order), `_read_file_context()` (line numbers, truncation), `_extract_diff()` (strip fences/prose), `patch()` (bad dir, empty tree, dry-run) |
| `test_builder.py` | 21 | `_classify_error()` (all error categories), `_regex_recovery()` (agent mapping), `_parse_llm_response()` (structured + fallback), `build()` (bad dir, missing debian/, no debuild, success/failure result shape) |
| `test_integration.py` | 16 | End-to-end `audit()`, `detect()`, `scribe()` against `lab/sources/hello-package`; write-flag safety checks |

## Running subsets

```bash
# Unit tests only (no external tools needed)
AI_PROVIDER=demo python3 -m pytest tests/ --ignore=tests/test_integration.py -v

# Integration tests only
AI_PROVIDER=demo LLM_BUDGET=5 python3 -m pytest tests/test_integration.py -v

# Single test class
AI_PROVIDER=demo python3 -m pytest tests/test_auditor.py::TestRegexFallback -v

# Stop on first failure
AI_PROVIDER=demo LLM_BUDGET=5 python3 -m pytest tests/ -x
```

## Adding new tests

- **Unit tests**: add a class to the relevant `test_<agent>.py` file.
  No env vars required as long as the function under test doesn't call `brain.ask()`.
- **Integration tests**: add to `test_integration.py`. Always guard with a
  `@requires_*` skip marker if an external tool is needed.
- **New agent**: create `tests/test_<agent>.py` following the existing pattern.
  Import private helpers via `from agents.<agent> import ...` — the `conftest.py`
  already inserts the project root into `sys.path`.
