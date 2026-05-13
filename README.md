# Ubuntu AI Packaging Mentor

An AI-powered multi-agent CLI that automates Debian/Ubuntu packaging tasks with
adaptive explanations tailored to your experience level.

---

## Demo — Full Workflow (Beginner)

Audit → Detect → Scribe in a single session:

<img src="demo/demo.svg" alt="Full workflow demo" width="900"/>

---

## Demo — Persona Contrast

Same tools, different depth: Beginner · MOTU · CoreDev

<img src="demo/demo_personas.svg" alt="Persona contrast demo" width="900"/>

---

## Agents

| Agent | What it does |
|-------|-------------|
| **auditor** | Scans source tree with `licensecheck`, builds a DEP-5 `debian/copyright` |
| **detective** | Scans C headers + autoconf/CMake macros → generates `Build-Depends` |
| **scribe** | Reads git log → drafts a `debian/changelog` entry |

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
