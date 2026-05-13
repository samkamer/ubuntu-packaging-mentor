#!/bin/bash
set -e

PROJECT_ROOT="/home/hackathon/Ubu-dev-mentor"
cd "$PROJECT_ROOT"

mkdir -p agents lab/sources lab/builds lab/outputs tests

touch mentor.py \
      agents/auditor.py \
      agents/detective.py \
      agents/scribe.py \
      agents/quilt_master.py \
      agents/__init__.py

echo "Scaffold complete:"
find . -not -path './.github*' -not -name 'scaffold.sh' -not -name 'copilot-instructions.md' | sort
