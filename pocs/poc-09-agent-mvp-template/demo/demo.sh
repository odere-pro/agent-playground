#!/usr/bin/env bash
# PoC-9 demo: a new agent, from nothing to a green and then a red eval. Offline, no key.
# `--live` then starts the Compose `fake` profile and calls it (needs Docker and the chassis image;
# see the rendered deploy/compose/README.md).
set -euo pipefail
root="$(cd "$(dirname "$0")/../../.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
export UV_OFFLINE="${UV_OFFLINE:-1}"

(cd "$root" && uv run agentctl new demo-agent --kind transformer --dest "$work" --platform-path "$root")
cd "$work/demo-agent"
unset UV_NO_SYNC  # the new agent syncs itself
git log --oneline
make test ARGS=-q | tail -1
make lint | tail -1
make load-smoke ARGS=-q | tail -1
make hostile ARGS=-q | tail -1
make redteam ARGS=-q | tail -1
make eval ARGS=-q | tail -1

echo "--- break the logic: the eval must fail"
cat >> src/demo_agent/handle.py <<'PY'


async def handle(input, ctx):  # noqa: F811
    raise RuntimeError("broken on purpose")
    yield {}
PY
if make eval ARGS=-q > "$work/broken.log" 2>&1; then
  echo "FAIL: the eval passed on broken logic"
  exit 1
fi
tail -1 "$work/broken.log"
echo "OK: the eval caught it"
git checkout -q src/demo_agent/handle.py

if [[ "${1:-}" == "--live" ]]; then
  compose=(docker compose -f deploy/compose/docker-compose.yaml --profile fake)
  "${compose[@]}" up -d --wait --build
  trap '"${compose[@]}" down -v; rm -rf "$work"' EXIT
  curl -fsS -X POST http://127.0.0.1:8080/v1/run -H 'content-type: application/json' \
    -d '{"input": {"text": "hello"}}'
  echo
  curl -fsS -X POST http://127.0.0.1:8080/v1/chat/completions -H 'content-type: application/json' \
    -d '{"model": "demo-agent", "messages": [{"role": "user", "content": "hello"}]}'
  echo
  # MCP: the two calls any client starts with. The kit's `test_an_mcp_client_finds_the_agent` runs
  # the same pair offline.
  mcp=(-fsS -X POST http://127.0.0.1:8080/v1/mcp -H 'content-type: application/json'
    -H 'accept: application/json, text/event-stream')
  init='{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "demo", "version": "0"}}}'
  session="$(curl "${mcp[@]}" -D - -o /dev/null -d "$init" | tr -d '\r' | awk -F': ' 'tolower($1) == "mcp-session-id" {print $2}')"
  curl "${mcp[@]}" -H "mcp-session-id: $session" -o /dev/null \
    -d '{"jsonrpc": "2.0", "method": "notifications/initialized"}'
  curl "${mcp[@]}" -H "mcp-session-id: $session" \
    -d '{"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}'
  echo
fi
