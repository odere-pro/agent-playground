#!/usr/bin/env bash
# PostToolUse on Edit|Write: format the edited Python file. Nothing else.
file=$(python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("tool_input",{}).get("file_path",""))' 2>/dev/null)
case "$file" in
  *.py)
    cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
    case "$file" in */docs/planning/*) exit 0 ;; esac
    uv run --no-sync ruff format --quiet "$file" >/dev/null 2>&1 || true
    ;;
esac
exit 0
