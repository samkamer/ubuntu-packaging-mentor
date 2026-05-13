#!/usr/bin/env bash
# demo/record_personas.sh — Record the multi-persona contrast demo
#
# Outputs:
#   demo/demo_personas.cast   — asciinema cast file
#   demo/demo_personas.svg    — animated SVG for README embedding
#
# Usage:
#   cd /home/hackathon/Ubu-dev-mentor
#   bash demo/record_personas.sh

set -euo pipefail
cd "$(dirname "$0")/.."

CAST=demo/demo_personas.cast
SVG=demo/demo_personas.svg

echo "▶  Recording persona contrast demo (AI_PROVIDER=demo)..."
rm -f "$CAST"

AI_PROVIDER=demo LLM_BUDGET=30 \
  asciinema rec "$CAST" \
    --command "python3 demo/run_demo_personas.py" \
    --title "Ubuntu AI Packaging Mentor — Persona Contrast: Beginner · MOTU · CoreDev" \
    --idle-time-limit 3 \
    --quiet

echo "✓  Cast saved: $CAST"

if command -v termtosvg &>/dev/null; then
    echo "▶  Converting to animated SVG..."
    termtosvg render "$CAST" "$SVG" \
        --template window_frame_js \
        --min-frame-duration 50
    echo "✓  SVG saved: $SVG"
    echo
    echo "   Embed in README.md with:"
    echo "   <img src=\"demo/demo_personas.svg\" alt=\"Persona Demo\" width=\"900\"/>"
fi

if command -v agg &>/dev/null; then
    GIF=demo/demo_personas.gif
    agg "$CAST" "$GIF" --font-size 14 --cols 120 --rows 35
    echo "✓  GIF saved: $GIF"
fi

echo
echo "To replay:   asciinema play $CAST"
