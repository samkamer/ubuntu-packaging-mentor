#!/usr/bin/env bash
# demo/record.sh — Record and export the Ubuntu AI Packaging Mentor demo
#
# Outputs:
#   demo/demo.cast   — asciinema cast file (portable, ~10 KB)
#   demo/demo.svg    — animated SVG via termtosvg (embeds in GitHub README)
#
# Usage:
#   cd /home/hackathon/Ubu-dev-mentor
#   bash demo/record.sh
#
# To play the cast locally afterwards:
#   asciinema play demo/demo.cast

set -euo pipefail
cd "$(dirname "$0")/.."   # always run from project root

CAST=demo/demo.cast
SVG=demo/demo.svg

echo "▶  Recording demo (AI_PROVIDER=demo — no live LLM needed)..."
echo "   Output: $CAST"
echo

# Remove stale cast so asciinema doesn't prompt to overwrite
rm -f "$CAST"

AI_PROVIDER=demo LLM_BUDGET=30 \
  asciinema rec "$CAST" \
    --command "python3 demo/run_demo.py" \
    --title "Ubuntu AI Packaging Mentor — Full Workflow Demo" \
    --idle-time-limit 3 \
    --quiet

echo
echo "✓  Cast saved: $CAST"

# ── Export to animated SVG ────────────────────────────────────────────────────
if command -v termtosvg &>/dev/null; then
    echo "▶  Converting to animated SVG via termtosvg..."
    termtosvg render "$CAST" "$SVG" \
        --template window_frame_js \
        --min-frame-duration 0.5 \
        --last-frame-duration 5
    echo "✓  SVG saved: $SVG"
    echo
    echo "   Embed in README.md with:"
    echo "   <img src=\"demo/demo.svg\" alt=\"Demo\" width=\"900\"/>"
else
    echo "ℹ  termtosvg not found — install with: pip install termtosvg"
fi

# ── Try agg → GIF if available ────────────────────────────────────────────────
if command -v agg &>/dev/null; then
    GIF=demo/demo.gif
    echo "▶  Converting to GIF via agg..."
    agg "$CAST" "$GIF" --font-size 14 --cols 120 --rows 35
    echo "✓  GIF saved: $GIF"
fi

echo
echo "To upload and share:"
echo "  asciinema upload $CAST"
