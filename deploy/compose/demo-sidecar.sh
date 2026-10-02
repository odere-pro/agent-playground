#!/usr/bin/env bash
# PoC-2 demo, `sidecar` lane: the same request goes to four engines by swapping the workload
# container next to the chassis. For each of python, pydanticai, langgraph, and typescript:
# start that workload profile, wait for `/ready`, show which engine answers, send one `glossary`
# request complete and streamed (the fake model asks for `glossary_lookup`, so the tool loop runs
# through the chassis's MCP endpoint), print the events and the `versions`, the router's
# `token_log` lines for that engine, and the key-like variables in the workload container (none
# expected). Then stop the stack.
#
#   deploy/compose/demo-sidecar.sh            # the `fake` variant: no key, no internet
#   deploy/compose/demo-sidecar.sh --local    # adds docker-compose.local.yaml (real models)
#
# The record goes to pocs/poc-02-two-engines-one-contract/demo/<date>-demo-sidecar[-local].md
# and to stdout; DEMO_OUT overrides the path. Exits 1 if any workload shows a key-like variable
# or the chassis key (LITELLM_API_KEY, from the shell or the `.env` next to this script) shows up
# in any service's log.
# PoC-1's demo.sh is unchanged. Works with the bash 3.2 macOS ships.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"
REPO=$(cd ../.. && pwd)
# The `.env` Compose reads, found next to this script, not from the caller's shell.
ENV_FILE="$HERE/.env"

COMPOSE=(docker compose -f docker-compose.yaml -f docker-compose.sidecar.yaml)
VARIANT=fake
if [[ "${1:-}" == "--local" ]]; then
  COMPOSE+=(-f docker-compose.local.yaml)
  VARIANT=local
fi
SUFFIX=""
[[ "$VARIANT" == "local" ]] && SUFFIX="-local"
OUT=${DEMO_OUT:-$REPO/pocs/poc-02-two-engines-one-contract/demo/$(date +%F)-demo-sidecar$SUFFIX.md}
PROFILES=(python pydanticai langgraph typescript)
ALL_PROFILES=()
for p in "${PROFILES[@]}"; do ALL_PROFILES+=(--profile "$p"); done
CHASSIS=http://127.0.0.1:8080
# The three Python engines get the tool-loop prompt. The TypeScript echo is the plain simplifier:
# it has no tool client (PoC-2 scope), so it gets the simplify prompt instead.
TOOL_TEXT="glossary: what does SLM mean? Say it in plain words."
PLAIN_TEXT="simplify: the quick brown fox jumps over the lazy dog."
# Public, not a credential: the official python image sets GPG_KEY to the id of the key that
# signs CPython releases. Every other key-like name in a workload is a defect.
PUBLIC_NAMES='^GPG_KEY$'
CARD=http://127.0.0.1:9000/.well-known/agent-card.json

say() { printf '\n## %s\n\n' "$*"; }
run() { printf '$ %s\n' "$*"; "$@"; }

if ! command -v docker >/dev/null; then
  echo "docker is not on PATH; nothing to demo" >&2
  exit 1
fi
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

main() {
  set -e
  local found_key=0 prev="" p svc before resp names TEXT BODY STREAM_BODY
  # On any failure, take the whole stack down, every profile included.
  trap '"${COMPOSE[@]}" "${ALL_PROFILES[@]}" down >/dev/null 2>&1 || true' EXIT

  printf '# PoC-2 demo: four engines in the sidecar lane (%s variant)\n\n' "$VARIANT"
  printf 'Recorded %s by deploy/compose/demo-sidecar.sh.\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  for p in "${PROFILES[@]}"; do
    svc="workload-$p"
    TEXT=$TOOL_TEXT
    [[ "$p" == "typescript" ]] && TEXT=$PLAIN_TEXT
    BODY="{\"input\": {\"text\": \"$TEXT\"}}"
    STREAM_BODY="{\"input\": {\"text\": \"$TEXT\"}, \"stream\": true}"
    before=$(token_lines | wc -l | tr -d ' ')
    if [[ -z "$prev" ]]; then
      say "Workload $p: start the stack with the $svc profile (no manual steps)"
      run "${COMPOSE[@]}" --profile "$p" up -d --wait --quiet-pull
    else
      say "Workload $p: swap workload-$prev for $svc, then recreate the chassis next to it"
      # The new workload binds the same 127.0.0.1:9000 in the chassis's namespace, so the old
      # one goes first. The chassis is recreated so it reads the new agent card in `setup`.
      run "${COMPOSE[@]}" --profile "$prev" rm -sf "workload-$prev"
      run "${COMPOSE[@]}" --profile "$p" up -d --wait --no-deps --force-recreate chassis "$svc"
    fi
    run curl -s "$CHASSIS/ready"; echo

    say "Workload $p: the engine behind the chassis (its agent card, read inside the chassis)"
    run "${COMPOSE[@]}" exec -T chassis python -c \
      "import json,urllib.request; c=json.load(urllib.request.urlopen('$CARD', timeout=2)); print(c['name'], c.get('version'))"
    printf '$ curl -s -m 2 %s   # from the host\n' "$CARD"
    curl -s -m 2 -o /dev/null "$CARD" && echo "reachable (defect: the workload port is published)" \
      || echo "not reachable from the host: the workload publishes no port"

    say "Workload $p: complete response"
    printf '$ curl -s -X POST %s/v1/run -H content-type: application/json -d %s\n' "$CHASSIS" "$BODY"
    resp=$(curl -s -X POST "$CHASSIS/v1/run" -H 'content-type: application/json' -d "$BODY")
    printf '%s\n' "$resp"
    printf 'versions: '
    printf '%s' "$resp" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin).get("versions")))' \
      || echo "(no versions: the response is not JSON)"

    say "Workload $p: streaming response (server-sent events)"
    run curl -s -N -X POST "$CHASSIS/v1/run" -H 'content-type: application/json' -d "$STREAM_BODY"

    sleep 2
    say "Workload $p: token counts in the router (LiteLLM), this engine's calls only"
    token_lines | tail -n +"$((before + 1))" | grep . || echo "(no token_log line)"

    say "Workload $p: key-like variables in the workload container (names only; none expected)"
    # Names only, so a defect never writes a key's value into the record.
    printf "\$ docker compose exec %s env | cut -d= -f1 | grep -i -E 'key|token|secret' | grep -v -E '%s'\n" "$svc" "$PUBLIC_NAMES"
    names=$("${COMPOSE[@]}" --profile "$p" exec -T "$svc" env | cut -d= -f1 \
      | grep -i -E 'key|token|secret' | grep -v -E "$PUBLIC_NAMES" || true)
    if [[ -z "$names" ]]; then
      echo "key-like variables in the workload: none"
    else
      printf '%s\n' "$names"
      echo "key-like variables in the workload: FOUND (defect)"
      found_key=1
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
      found_key=1
    fi
  fi
  chassis_key=""

  say "Stop"
  run "${COMPOSE[@]}" "${ALL_PROFILES[@]}" down
  trap - EXIT
  return "$found_key"
}

mkdir -p "$(dirname "$OUT")"
set +e
main 2>&1 | tee "$OUT"
status=${PIPESTATUS[0]}
set -e
echo "record: $OUT" >&2
exit "$status"
