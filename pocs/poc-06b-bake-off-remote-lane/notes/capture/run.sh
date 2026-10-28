#!/usr/bin/env bash
# Run one capture scenario: plain | tool | tool-nohdr | shell | control.
# Usage: run.sh <scenario> <prompt> <out-dir>   (venv at /tmp/poc06-capture/venv)
# The child environment is an allow-list built here with env -i. Nothing else is inherited.
set -euo pipefail
scenario=$1 prompt=$2 out=$3
here=$(cd "$(dirname "$0")" && pwd)
py=/tmp/poc06-capture/venv/bin/python
mkdir -p "$out/home" "$out/work"
free_port() { "$py" -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])'; }
port=$(free_port) mcp_port=$(free_port)
base="http://127.0.0.1:$port"
[ "$scenario" = control ] && base="http://example.test:8080"
: >"$out/capture.jsonl"
"$py" "$here/capture_server.py" tripwire --port 9 --log "$out/capture.jsonl" &
trip=$!
srv_scenario=$scenario
[ "$scenario" = control ] && srv_scenario=plain
srv_scenario=${srv_scenario%-nohdr}
"$py" "$here/capture_server.py" messages --port "$port" --log "$out/capture.jsonl" \
  --scenario "$srv_scenario" --workdir "$out/work" &
srv=$!
"$py" "$here/capture_server.py" mcp --port "$mcp_port" --log "$out/mcp.jsonl" &
mcp=$!
trap 'kill $trip $srv $mcp 2>/dev/null || true' EXIT
sleep 2
rc=0
timeout "${RUN_TIMEOUT:-90}" env -i \
  PATH=/usr/bin:/bin HOME="$out/home" \
  ANTHROPIC_BASE_URL="$base" ANTHROPIC_AUTH_TOKEN=dummy-capture-token \
  ANTHROPIC_MODEL=big-default ANTHROPIC_SMALL_FAST_MODEL=big-default \
  ANTHROPIC_CUSTOM_HEADERS="traceparent: 00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01" \
  DISABLE_TELEMETRY=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 DISABLE_AUTOUPDATER=1 \
  DISABLE_ERROR_REPORTING=1 HTTPS_PROXY=http://127.0.0.1:9 HTTP_PROXY=http://127.0.0.1:9 \
  NO_PROXY=127.0.0.1,localhost \
  "$py" "$here/driver.py" "$scenario" "$prompt" "$out/work" "$mcp_port" "$out/report.json" || rc=$?
echo "driver exit code: $rc"
