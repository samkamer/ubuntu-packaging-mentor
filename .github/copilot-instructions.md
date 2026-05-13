# Copilot Instructions: Ubuntu AI Packaging Mentor

## Project Overview

This is a **multi-agent Python system** that automates Debian/Ubuntu packaging tasks and provides adaptive mentorship. It runs inside an Ubuntu 26.04 LXD container with the project mounted at `/home/hackathon/Ubu-dev-mentor`.

## Architecture: Manager-Worker Pattern

```
mentor.py          ← Orchestrator: UI, environment checks, agent coordination
agents/
  auditor.py       ← Legal/copyright analysis (produces DEP-5 files)
  detective.py     ← Dependency discovery (resolves dev-package names)
  scribe.py        ← Changelog and documentation generation
  quilt_master.py  ← Source patching via the `quilt` tool
lab/builds/<pkg>/  ← Build workspace (check for existence before writing)
```

**`mentor.py`** is the single entry point. It coordinates agents and presents results to the user.

## Agent Contract (Non-Negotiable)

Every agent **must** write its final result as a structured JSON object to `stdout`:

```python
import json, sys

result = {"status": "ok", "data": {...}}
# On error:
result = {"status": "error", "error": stderr_output, "data": None}

print(json.dumps(result))
```

Errors from `subprocess` calls go into the `"error"` field — this enables the self-healing loop in `mentor.py`.

## Python Conventions (3.12+)

- All system tool invocations use `subprocess.run(capture_output=True, text=True)`
- Use relative paths from the project root (not absolute)
- Each agent in `agents/` must be runnable standalone for independent testing
- Always check `if os.path.exists("lab/builds/<pkg>")` before destructive operations (idempotency rule)

## Key Packaging Tools

The system shells out to these Ubuntu/Debian tools:

| Tool | Purpose |
|------|---------|
| `licensecheck` | Scan source files for licenses (used by `auditor.py`) |
| `apt-file` | Map headers to `-dev` packages (used by `detective.py`) |
| `lintian` | Lint built `.deb` packages |
| `quilt` | Manage patch series on upstream source |
| `devscripts` | `debchange`, `debuild`, `uscan` wrappers |

## LLM Integration

Local Ollama endpoint: `http://10.116.163.1:11434` (host bridge IP, Gemma:4b model). Agents call this for tasks like generating DEP-5 copyright summaries and interpreting build failures.

## Adaptive Mentorship Levels

When generating explanations or documentation, calibrate output to the user's level:

- **Beginner** — explain *why* (e.g., "What is a build-dep and why does it matter?")
- **MOTU** — focus on Ubuntu Policy compliance, cite specific policy manual sections
- **CoreDev** — provide raw: logs, diffs, system state; skip preamble

## Self-Healing Pattern

When a build fails:
1. Capture the tail of the build log
2. Pass it to `detective.py` to identify missing deps or policy violations
3. Surface the suggested fix to the user via `mentor.py`

Prioritize CVE/security findings during the Auditor phase.

## Golden Prompts (Agent Bootstrap)

When asked to implement an agent, follow these specs:

1. **Auditor** — Run `licensecheck -r .` on source tree, pass output to LLM, produce a valid DEP-5 `debian/copyright` file
2. **Detective** — Scan `#include` headers, use `apt-file search` to resolve `-dev` packages, output `Build-Depends` list
3. **Scribe** — Summarize `git log` entries into `debian/changelog` format using `debchange`
4. **Quilt Master** — Wrap `quilt push/pop/refresh` with LLM-driven patch generation for upstream source modifications
