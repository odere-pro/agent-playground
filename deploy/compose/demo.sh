#!/usr/bin/env bash
# PoC-1 demo: one request through the chassis and the router, streaming and complete, on both
# routes, then the router's token log. Runs the `fake` variant by default; pass
# `--local` to add docker-compose.local.yaml. Output goes to stdout; save it to
# pocs/poc-01-walking-skeleton/demo/.
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE=(docker compose -f docker-compose.yaml)
if [[ "${1:-}" == "--local" ]]; then
  COMPOSE+=(-f docker-compose.local.yaml)
fi
CHASSIS=http://127.0.0.1:8080
CONFIG=../../packages/chassis/configs/local.yaml
BODY='{"input": {"text": "simplify: the quick brown fox jumps over the lazy dog"}}'

say() { printf '\n## %s\n\n' "$*"; }
run() { printf '$ %s\n' "$*"; "$@"; }

if ! command -v docker >/dev/null; then
  echo "docker is not on PATH; nothing to demo" >&2
  exit 1
fi

say "Start the stack (no manual steps)"
run "${COMPOSE[@]}" up -d --wait --quiet-pull
run curl -s "$CHASSIS/health"; echo
run curl -s "$CHASSIS/ready"; echo

for route in big-default local-small; do
  if [[ "$route" == "local-small" ]]; then
    say "Switch model.route to local-small: a config change, then a restart"
    # A temporary copy of the config with the route swapped, mounted in place of the original.
    tmp=$(mktemp -d)
    sed 's/route: big-default/route: local-small/' "$CONFIG" > "$tmp/config.yaml"
    cat > "$tmp/override.yaml" <<EOF
services:
  chassis:
    volumes:
      - $tmp/config.yaml:/etc/chassis/config.yaml:ro
EOF
    COMPOSE+=(-f "$tmp/override.yaml")
    run "${COMPOSE[@]}" up -d --wait chassis
  fi
  say "Route $route: complete response"
  run curl -s -X POST "$CHASSIS/v1/run" -H 'content-type: application/json' -d "$BODY"; echo
  say "Route $route: streaming response (server-sent events)"
  run curl -s -N -X POST "$CHASSIS/v1/run" -H 'content-type: application/json' \
    -d "${BODY%\}}, \"stream\": true}"
done

sleep 2

say "Token counts in the router (LiteLLM), one line per call, tagged agent:<name>"
# `/spend/logs` needs a database behind LiteLLM, which PoC-1 does not run; litellm/token_log.py
# prints one `token_log` line per call instead (route, tokens, cost, tags; never the text).
run "${COMPOSE[@]}" logs --no-color litellm | { grep 'token_log' || echo "(no token_log line)"; } | tail -n 20

say "No key in any log"
if [[ -n "${LITELLM_API_KEY:-}" ]]; then
  printf 'occurrences of LITELLM_API_KEY in logs: '
  "${COMPOSE[@]}" logs --no-color | grep -c -- "$LITELLM_API_KEY" || true
fi

say "Stop"
run "${COMPOSE[@]}" down
