#!/usr/bin/env bash
# SessionStart: show where the work is. Prints to the session context.
cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}" || exit 0
current=$(cat pocs/CURRENT 2>/dev/null || echo "unknown")
echo "Current PoC: ${current} (pocs/${current}/README.md holds its checklist)"
changes=$(git status --short 2>/dev/null | head -8)
if [ -n "$changes" ]; then
  echo "Uncommitted changes:"
  echo "$changes"
fi
echo "Commands: make help. Gate before commit: make quick. Boundary gate: make check."
exit 0
