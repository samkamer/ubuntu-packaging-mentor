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

### patch_manager — Quilt Patch Workflow

```
source tree scan → LLM identifies file → LLM generates diff
→ quilt new → quilt add → patch -p1 → quilt refresh
```

```bash
# Dry run — preview the diff without touching files
python3 agents/patch_manager.py <source_dir> <patch-name> "<description>" --dry-run

# Apply for real
python3 agents/patch_manager.py <source_dir> <patch-name> "<description>"

# Example
python3 agents/patch_manager.py lab/sources/hello-package/hello-2.10 \
  fix-null-check "Add null pointer check before dereferencing name argument"
```

Output JSON:
```json
{
  "status": "success",
  "patch": "fix-null-check.patch",
  "file": "src/hello.c",
  "patch_path": "debian/patches/fix-null-check.patch",
  "written_to": "/path/to/source/debian/patches/fix-null-check.patch",
  "agent": "patch_manager"
}
```

Requires `quilt` and `patch`: `sudo apt install quilt patch`

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
