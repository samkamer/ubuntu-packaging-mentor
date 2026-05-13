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
|-------|-------|-------------|
| **auditor** | 1 Audit | Scans source tree with `licensecheck`, builds a DEP-5 `debian/copyright` |
| **detective** | 2 Detect | Scans C headers + autoconf/CMake macros → generates `Build-Depends` |
| **scribe** | 3 Scribe | Reads git log → drafts a `debian/changelog` entry |
| **patch_manager** | 4 Patch | AI-identifies the file to change, generates a unified diff, applies it as a quilt patch in `debian/patches/` |
| **builder** | 5 Build | Runs `debuild -us -uc -b`; on failure uses AI to classify the error and recommend the recovery agent |

### patch_manager — Quilt Patch Workflow

```bash
# Dry run — preview the diff without touching files
python3 agents/patch_manager.py <source_dir> <patch-name> "<description>" --dry-run

# Apply for real
python3 agents/patch_manager.py <source_dir> <patch-name> "<description>"
```

Requires `quilt` and `patch`: `sudo apt install quilt patch`

### builder — Debian Build + AI Failure Analysis

```bash
python3 agents/builder.py <source_dir>
```

On failure the builder extracts the last 20 lines of the build log and asks the LLM to classify the error and suggest the recovery command:

| Error type | Suggested agent |
|------------|----------------|
| Missing `-dev` package | `detective` |
| Compilation / syntax error | `patch_manager` |
| Packaging file problem | `auditor` |

Requires `debuild`: `sudo apt install devscripts`

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
