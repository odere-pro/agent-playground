#!/usr/bin/env bash
# PoC-3 demo: one agent, every client. For each of the four engines PoC-2 runs in the `sidecar`
# lane (python, pydanticai, langgraph, typescript): start that workload profile, wait for
# `/ready`, then call the agent through the chassis's public port with off-the-shelf clients only
# (pocs/poc-03-one-interface-every-client/demo/clients.py): the OpenAI Python SDK (streamed), the
# Anthropic Python SDK (streamed), and `fastmcp.Client` over streamable HTTP, once directly at
# `/v1/mcp` and once through LiteLLM's MCP gateway (`127.0.0.1:4000/mcp/`, which forwards to
# `http://chassis:8080/v1/mcp` on the Compose network). After each call it prints the router's new
# `token_log` lines and fails if the call left none. It also prints `GET /manifest`, the published
# ports, the key-like variables in the workload, and counts the chassis key in the logs. Then it
# stops the stack.
#
#   deploy/compose/demo-interfaces.sh     # the `fake` variant: no key, no internet
#
# The record goes to pocs/poc-03-one-interface-every-client/demo/<date>-demo-interfaces.md and to
# stdout; DEMO_OUT overrides the path. Exits 1 if any call fails, any call does not reach the
# router, a workload shows a key-like variable, or the chassis key (LITELLM_API_KEY, from the
# shell or the `.env` next to this script) shows up in any service's log. Needs `uv` (the clients
# run in the repo's locked environment) and Docker. Works with the bash 3.2 macOS ships.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"
REPO=$(cd ../.. && pwd)
# The `.env` Compose reads, found next to this script, not from the caller's shell.
ENV_FILE="$HERE/.env"

COMPOSE=(docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml)
OUT=${DEMO_OUT:-$REPO/pocs/poc-03-one-interface-every-client/demo/$(date +%F)-demo-interfaces.md}
PROFILES=(python pydanticai langgraph typescript)
ALL_PROFILES=()
for p in "${PROFILES[@]}"; do ALL_PROFILES+=(--profile "$p"); done
CHASSIS=http://127.0.0.1:8080
GATEWAY=http://127.0.0.1:4000/mcp/
# The agent's name in packages/chassis/configs/sidecar.yaml; every client sends it as `model`.
AGENT="echo"
CLIENTS_PY="$REPO/pocs/poc-03-one-interface-every-client/demo/clients.py"
# The three Python engines get the tool-loop prompt. The TypeScript echo has no tool client
# (PoC-2 scope), so it gets the simplify prompt.
TOOL_TEXT="glossary: what does SLM mean? Say it in plain words."
PLAIN_TEXT="simplify: the quick brown fox jumps over the lazy dog."
# Public, not a credential: the official python image sets GPG_KEY to the id of the key that
# signs CPython releases. Every other key-like name in a workload is a defect.
PUBLIC_NAMES='^GPG_KEY$'

say() { printf '\n## %s\n\n' "$*"; }
run() { printf '$ %s\n' "$*"; "$@"; }
# Like `run`, but keeps only the health lines of a long `up` output.
run_up() { printf '$ %s\n' "$*"; "$@" 2>&1 | grep -E 'Healthy|Error' || true; }

for tool in docker uv; do
  if ! command -v "$tool" >/dev/null; then
    echo "$tool is not on PATH; nothing to demo" >&2
    exit 1
  fi
done
if ! docker info >/dev/null 2>&1; then
  echo "docker info failed: the Docker daemon is not running; nothing to demo" >&2
  exit 1
fi

# The chassis key as Compose sees it: the shell's LITELLM_API_KEY wins over `.env`, as in
# Compose interpolation. Never printed; tracing is off while it is read.
chassis_key_value() {
  { set +x; } 2>/dev/null
  local line value
  if [[ -n "${LITELLM_API_KEY:-}" ]]; then
    value=$LITELLM_API_KEY
  elif [[ -f "$ENV_FILE" ]]; then
    line=$(grep -E '^[[:space:]]*(export[[:space:]]+)?LITELLM_API_KEY=' "$ENV_FILE" | tail -n 1 || true)
    value=${line#*=}
    value=${value%$'\r'}
    case "$value" in
      \"*\") value=${value#\"}; value=${value%\"} ;;
      \'*\') value=${value#\'}; value=${value%\'} ;;
      *) value=${value%%[[:space:]]#*}; value=${value%"${value##*[![:space:]]}"} ;;
    esac
  else
    value=""
  fi
  REPLY=$value
}

token_lines() {
  "${COMPOSE[@]}" logs --no-color litellm 2>/dev/null | grep 'token_log' || true
}

# One client call, then the router lines it left. Returns 1 if the call failed or no model call
# reached the router.
call() {
  local label=$1 before status=0 new
  shift
  before=$(token_lines | wc -l | tr -d ' ')
  say "$WORKLOAD: $label"
  printf '$ uv run --no-sync python %s %s %s %s\n' \
    "${CLIENTS_PY#"$REPO"/}" "$CHASSIS" "$AGENT" "$*"
  (cd "$REPO" && uv run --no-sync python "$CLIENTS_PY" "$CHASSIS" "$AGENT" "$@") || status=1
  sleep 2
  printf '\nrouter (LiteLLM token_log), new lines since this call:\n'
  new=$(token_lines | tail -n +"$((before + 1))" | sed 's/^[^|]*| //')
  if [[ -n "$new" ]]; then printf '%s\n' "$new"; else echo "(no token_log line)"; fi
  if ! printf '%s\n' "$new" | grep -q 'token_log status=ok route=big-default'; then
    echo "the call did not reach the router: FAILED"
    status=1
  fi
  return "$status"
}

main() {
  set -e
  local failed=0 prev="" p svc names TEXT
  # On any failure, take the whole stack down, every profile included.
  trap '"${COMPOSE[@]}" "${ALL_PROFILES[@]}" down >/dev/null 2>&1 || true' EXIT

  printf '# PoC-3 demo: one agent, every client, four engines (sidecar lane, fake variant)\n\n'
  printf 'Recorded %s by deploy/compose/demo-interfaces.sh.\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  for p in "${PROFILES[@]}"; do
    svc="workload-$p"
    WORKLOAD="Workload $p"
    TEXT=$TOOL_TEXT
    [[ "$p" == "typescript" ]] && TEXT=$PLAIN_TEXT
    if [[ -z "$prev" ]]; then
      say "$WORKLOAD: start the stack with the $svc profile (no manual steps)"
      run_up "${COMPOSE[@]}" --profile "$p" up -d --wait --quiet-pull
      say "Published ports (only 127.0.0.1:8080 and 127.0.0.1:4000 expected)"
      run "${COMPOSE[@]}" --profile "$p" ps --format '{{.Service}} {{.Ports}}'
    else
      say "$WORKLOAD: swap workload-$prev for $svc, then recreate the chassis next to it"
      run_up "${COMPOSE[@]}" --profile "$prev" rm -sf "workload-$prev"
      run_up "${COMPOSE[@]}" --profile "$p" up -d --wait --no-deps --force-recreate chassis "$svc"
    fi
    run curl -s "$CHASSIS/ready"; echo
    run "${COMPOSE[@]}" exec -T chassis python -c \
      "import json,urllib.request; c=json.load(urllib.request.urlopen('http://127.0.0.1:9000/.well-known/agent-card.json', timeout=2)); print('engine:', c['name'], c.get('version'))"

    call "OpenAI Python SDK, streamed" --client openai --text "$TEXT" || failed=1
    call "Anthropic Python SDK, streamed" --client anthropic --text "$TEXT" || failed=1
    call "MCP client, direct at /v1/mcp" --client mcp --text "$TEXT" || failed=1
    call "MCP client through LiteLLM's MCP gateway" --client mcp --mcp-url "$GATEWAY" \
      --text "$TEXT" || failed=1

    say "$WORKLOAD: GET /manifest"
    (cd "$REPO" && uv run --no-sync python "$CLIENTS_PY" "$CHASSIS" "$AGENT" --client manifest) \
      || failed=1

    say "$WORKLOAD: key-like variables in the workload container (names only; none expected)"
    names=$("${COMPOSE[@]}" --profile "$p" exec -T "$svc" env | cut -d= -f1 \
      | grep -i -E 'key|token|secret' | grep -v -E "$PUBLIC_NAMES" || true)
    if [[ -z "$names" ]]; then
      echo "key-like variables in the workload: none"
    else
      printf '%s\n' "$names"
      echo "key-like variables in the workload: FOUND (defect)"
      failed=1
    fi
    prev="$p"
  done

  say "No key in any log"
  { set +x; } 2>/dev/null
  chassis_key_value
  local chassis_key=$REPLY key_count
  if [[ -z "$chassis_key" ]]; then
    echo "LITELLM_API_KEY is empty (shell and $ENV_FILE): no chassis key to look for"
  else
    key_count=$("${COMPOSE[@]}" "${ALL_PROFILES[@]}" logs --no-color 2>&1 \
      | grep -c -F -e "$chassis_key" || true)
    key_count=${key_count:-0}
    printf 'lines with the chassis key (LITELLM_API_KEY) in the logs: %s\n' "$key_count"
    if [[ "$key_count" -gt 0 ]]; then
      echo "the chassis key is in a log: FOUND (defect)"
      failed=1
    fi
  fi
  chassis_key=""

  say "Stop"
  run "${COMPOSE[@]}" "${ALL_PROFILES[@]}" down 2>&1 | tail -n 1
  trap - EXIT
  printf '\nresult: %s\n' "$([[ "$failed" -eq 0 ]] && echo "every call ok" || echo "FAILED")"
  return "$failed"
}

mkdir -p "$(dirname "$OUT")"
set +e
main 2>&1 | tee "$OUT"
status=${PIPESTATUS[0]}
set -e
echo "record: $OUT" >&2
exit "$status"
