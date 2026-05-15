# Ubuntu AI Packaging Mentor

An AI-powered multi-agent CLI that automates Debian/Ubuntu packaging tasks with
adaptive explanations tailored to your experience level.

---

## Installation

Install from the `.deb` package (Ubuntu 24.04 Noble or later):

```bash
sudo dpkg -i ubu-dev-mentor_0.1.0-1_all.deb
ubu-dev-mentor --setup
```

Or run directly from source:

```bash
git clone https://github.com/samkamer/ubuntu-packaging-mentor.git
cd ubuntu-packaging-mentor
python3 mentor.py
```

---

## Quick start

```bash
# First run — detects your environment and writes config
python3 mentor.py

# Re-run setup any time (re-detect tools and LLM)
python3 mentor.py --setup

# Demo mode — no LLM required
AI_PROVIDER=demo python3 mentor.py
```

---

## What it does

`ubu-dev-mentor` walks you through the full Debian packaging workflow via a
menu of skills. Pick a skill, point it at a source directory, and the tool
runs the appropriate agent and explains the result at your experience level.

| Skill | Agent | What it does |
|-------|-------|-------------|
| 1 Audit | **auditor** | Scans source tree with `licensecheck`, builds a DEP-5 `debian/copyright` |
| 2 Detect | **detective** | Scans headers + imports → generates `Build-Depends` |
| 3 Scribe | **scribe** | Reads git log → drafts a `debian/changelog` entry |
| 4 Patch | **patch_manager** | AI-generates a unified diff and applies it as a quilt patch |
| 5 Build | **builder** | Runs `debuild -us -uc -b`; on failure classifies the error and suggests the recovery agent |
| — | **linter** | Runs `lintian`; AI explains `E:` errors. Called automatically after a successful build |

---

## Persona levels

Choose your experience level at startup — the tool adapts its explanations accordingly.

| Persona | Explanation style |
|---------|------------------|
| **Beginner** | Plain-language concept explanations, file backups before writes |
| **MOTU** | Action summary with Debian Policy section references |
| **CoreDev** | Terse one-liners only, zero extra LLM calls |

---

## Setup & Configuration

On first launch (or when `--setup` is passed), the tool detects your environment
and writes a persistent config file.

**What gets detected:**

| Item | How |
|------|-----|
| Packaging tools | `shutil.which` for each required binary |
| Ollama endpoint | HTTP probe: `localhost` → `127.0.0.1` → host gateway IP |
| Active model | Reads `/api/tags`; prefers `gemma3`, else first available model |

Missing tools are flagged with the `apt install` command to fix them.

**Config file location:** `$XDG_CONFIG_HOME/ubu-dev-mentor/config`
(default: `~/.config/ubu-dev-mentor/config`)

```ini
[llm]
provider = ollama
url      = http://192.168.1.1:11434
model    = gemma3:latest
budget   = 180

[tools]
licensecheck = /usr/bin/licensecheck
apt_file     = /usr/bin/apt-file
debuild      = /usr/bin/debuild
quilt        = /usr/bin/quilt
lintian      = /usr/bin/lintian
patch        = /usr/bin/patch
```

Only tools found on the system are written. Edit freely — `--setup` adds new
detections without overwriting your manual changes.

**Supported LLM providers:**

| `provider` | Description |
|------------|-------------|
| `ollama` | Local Ollama instance (default) |
| `copilot` | GitHub Copilot API |
| `demo` | Canned responses, no LLM required |

Environment variables (`AI_PROVIDER`, `LLM_URL`, `LLM_MODEL`, `LLM_BUDGET`)
override config file values when set.

---

## Prerequisites

```bash
sudo apt install licensecheck apt-file devscripts quilt lintian patch
sudo apt-file update
```

---

## Demos

```bash
# Full workflow (Beginner: Audit → Detect → Scribe)
asciinema play demo/demo.cast

# Persona contrast (Beginner · MOTU · CoreDev)
asciinema play demo/demo_personas.cast
```

---

See [CONTRIBUTING.md](CONTRIBUTING.md) for architecture, agent API contracts,
and how to add a new agent.
