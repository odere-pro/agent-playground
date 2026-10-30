#!/usr/bin/env bash
# PoC-8 drill stack: the chassis -> a real LiteLLM (Presidio hook, OTel callback) -> the fake
# model; the chassis and LiteLLM -> the OTel Collector -> Jaeger.
# Usage: stack.sh up|wait|down|digests|env|logs
#
# Keys (ADR-001 hard requirement 1, as PoC-5's seed.sh and PoC-7's stack.sh do it):
# - `up` makes the LiteLLM master key and the database password with `openssl rand` into
#   .env.poc08-litellm (mode 600, git-ignored). Only the LiteLLM and Postgres services get them.
# - Once LiteLLM answers, `up` mints one virtual key for the chassis through `/key/generate`. The
#   master key goes to curl as a header on stdin (`-H @-`), never in argv; the new key goes from
#   the response, through python, into .run/chassis.env (mode 600), which only the chassis loads.
# - No value is printed. `logs` masks every value of both files. No `set -x`.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../../.." && pwd)"
env_file="$here/.env.poc08-litellm"
run_dir="$here/.run"
key_file="$run_dir/chassis.env"
compose=(docker compose -p poc08-litellm --env-file "$env_file" -f "$here/docker-compose.yaml")
# Everything but the chassis: it starts once its key exists.
platform=(postgres fake-model-server guardrail-server presidio-analyzer presidio-anonymizer
  litellm jaeger otel-collector)
litellm=http://127.0.0.1:4000
model_route=big-default

make_env() {
  umask 077
  mkdir -p "$run_dir"
  [[ -f "$key_file" ]] || : >"$key_file"
  [[ -f "$env_file" ]] && return 0
  {
    # LiteLLM wants a master key that starts with `sk-`.
    echo "LITELLM_MASTER_KEY=sk-$(openssl rand -hex 24)"
    echo "LITELLM_DB_PASSWORD=$(openssl rand -hex 16)"
  } >"$env_file"
}

# The fake model's script (model_script.py): the PII rules and the red-team triggers.
make_script() {
  (cd "$root" && uv run --no-sync python "$here/model_script.py" "$run_dir/model.yaml")
}

# The /key/generate body. suggested: the PoC-5 shape (seed.sh key_body) without the MCP fields.
# `allowed_routes`: the model routes and `/guardrails/apply_guardrail`, which the chassis's guard
# stages call. LiteLLM refuses that route to a key without it (admin-only by default).
key_body() {
  printf '{"key_alias":"svc-echo","models":["%s"],"max_budget":1.0,"budget_duration":"1d",' \
    "$model_route"
  printf '"allowed_routes":["openai_routes","/guardrails/apply_guardrail"],'
  printf '"rpm_limit":600,"metadata":{"tags":["agent:echo"]}}'
}

# Mints the chassis's virtual key into $key_file. Reads the master key from the env file into a
# variable that only `printf` (a builtin, never in argv) writes into a pipe.
mint_key() {
  local master resp
  master=$(sed -n 's/^LITELLM_MASTER_KEY=//p' "$env_file")
  [[ -n $master ]] || { echo "stack.sh: no master key in the env file" >&2; return 1; }
  resp=$(printf 'Authorization: Bearer %s\n' "$master" |
    curl -sS -m 30 -H @- -H 'Content-Type: application/json' \
      --data-binary "$(key_body)" "$litellm/key/generate")
  unset master
  if ! printf '%s' "$resp" | python3 -I -c '
import json, sys
key = json.load(sys.stdin).get("key")
if not key:
    sys.exit(1)
sys.stdout.write(f"LITELLM_API_KEY={key}\n")
' >"$key_file.new"; then
    rm -f "$key_file.new"
    # A failed call holds no key; show LiteLLM's error message only.
    printf '%s' "$resp" | python3 -I -c '
import json, sys
try:
    data = json.load(sys.stdin)
except ValueError:
    data = {}
err = data.get("error") or {}
print(err.get("message") if isinstance(err, dict) else err or data.get("detail") or "no key")
' >&2 || true
    unset resp
    echo "stack.sh: LiteLLM refused the chassis key" >&2
    return 1
  fi
  unset resp
  mv "$key_file.new" "$key_file"
  echo "minted the chassis's virtual key (svc-echo) into $key_file"
}

# Polls URL from the host until it answers 2xx, or fails at the deadline.
poll() {
  local url=$1 deadline=$2
  until curl -fsS -o /dev/null -m 5 "$url" 2>/dev/null; do
    if ((SECONDS >= deadline)); then
      echo "stack.sh wait: $url did not answer in time" >&2
      return 1
    fi
    sleep 3
  done
  echo "ready: $url"
}

# Presidio is on the internal network only, so its health is read from inside LiteLLM.
poll_inside() {
  local url=$1 deadline=$2
  until "${compose[@]}" exec -T litellm python -c \
    "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('$url', timeout=3).status == 200 else 1)" \
    >/dev/null 2>&1; do
    if ((SECONDS >= deadline)); then
      echo "stack.sh wait: $url (from litellm) did not answer in time" >&2
      return 1
    fi
    sleep 3
  done
  echo "ready: $url (from litellm)"
}

# Every endpoint the drill needs, or fails after POC08_LITELLM_WAIT_S (default 600).
wait_ready() {
  local deadline=$((SECONDS + ${POC08_LITELLM_WAIT_S:-600}))
  poll "$litellm/health/liveliness" "$deadline"
  poll http://127.0.0.1:16686/ "$deadline"
  poll http://127.0.0.1:13133/ "$deadline"
  poll_inside http://presidio-analyzer:3000/health "$deadline"
  poll_inside http://presidio-anonymizer:3000/health "$deadline"
  poll http://127.0.0.1:8080/ready "$deadline"
}

# Masks every value of the two key files, and anything shaped like a LiteLLM key, in stdin.
redact() {
  python3 -I -c '
import re, sys
secrets = []
for path in sys.argv[1:]:
    try:
        with open(path) as f:
            for line in f:
                _, sep, value = line.strip().partition("=")
                if sep and len(value) >= 8:
                    secrets.append(value)
    except FileNotFoundError:
        pass
shapes = re.compile(r"sk-[A-Za-z0-9_-]{8,}|Bearer [A-Za-z0-9._-]+")
for line in sys.stdin:
    for value in secrets:
        line = line.replace(value, "***")
    sys.stdout.write(shapes.sub("***", line))
' "$env_file" "$key_file"
}

case "${1:-}" in
  up)
    make_env
    make_script
    "${compose[@]}" up -d --wait --quiet-pull --build "${platform[@]}"
    mint_key
    "${compose[@]}" up -d --wait --build chassis
    ;;
  wait)
    wait_ready
    ;;
  logs)
    # Tails of every service's log, redacted. Never prints a key file.
    "${compose[@]}" logs --no-color --tail "${POC08_LITELLM_LOG_TAIL:-200}" 2>&1 | redact
    ;;
  down)
    make_env
    "${compose[@]}" down -v --remove-orphans
    rm -rf "$env_file" "$run_dir"
    ;;
  digests)
    # image@digest for each third-party image, to pin them in docker-compose.yaml.
    make_env
    "${compose[@]}" config --images | sort -u | grep -v '^agent-platform/' | while read -r image; do
      docker buildx imagetools inspect "${image%@*}" --format '{{.Name}}@{{.Manifest.Digest}}'
    done
    ;;
  env)
    # The path of the chassis's key file, which the drill reads; never a value.
    echo "POC08_LITELLM_KEY_FILE=$key_file"
    ;;
  *)
    echo "usage: $0 up|wait|down|digests|env|logs" >&2
    exit 2
    ;;
esac
