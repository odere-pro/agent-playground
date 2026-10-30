#!/usr/bin/env bash
# SessionStart: show where the work is. Prints to the session context.
cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}" || exit 0
# The file reaches the session context, so only a PoC folder name gets through. Read a bounded
# prefix: a longer file cannot match the pattern anyway.
current=$(head -c 64 pocs/CURRENT 2>/dev/null || true)
if ! [[ "$current" =~ ^poc-[0-9a-z-]{1,40}$ ]]; then
  current="unknown"
fi
echo "Current PoC: ${current} (pocs/${current}/README.md holds its checklist)"
changes=$(git status --short 2>/dev/null | head -8)
if [ -n "$changes" ]; then
  echo "Uncommitted changes:"
  echo "$changes"
fi
echo "Commands: make help. Gate before commit: make quick. Boundary gate: make check."
exit 0
