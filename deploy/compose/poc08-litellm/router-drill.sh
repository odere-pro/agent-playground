#!/usr/bin/env bash
# The router drill (router-drill.yaml over docker-compose.yaml), Compose project `router1113`.
# Usage: router-drill.sh up|down|logs|env|swap <deployment>|mint <alias> <agent> <file>|sql
#
# Keys (ADR-001 hard requirement 1, as stack.sh does it): `up` makes the master key, the database
# password, and the (unused here, but the base file needs it) guardrail token with `openssl rand`
# into .run/router.env (mode 600, git-ignored). `mint` gets a virtual key through /key/generate,
# the master key sent as a header on stdin, and writes it to a mode-600 file. No value is printed.
#
# `swap <deployment>` is the G-1b swap: it sets the model behind `big-default` in the copy of the
# router config (.run/router-litellm.yaml), bumps its `route_version`, and restarts LiteLLM.
# `sql` runs the SQL on stdin in the Postgres container (CSV out), for the drill's usage reads.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../../.." && pwd)"
run_dir="$here/.run"
env_file="$run_dir/router.env"
key_file="$run_dir/chassis.env"
config="$run_dir/router-litellm.yaml"
project=${ROUTER_DRILL_PROJECT:-router1113}
compose=(docker compose -p "$project" --env-file "$env_file"
  -f "$here/docker-compose.yaml" -f "$here/router-drill.yaml")
platform=(postgres fake-model-server litellm)
litellm=http://127.0.0.1:4013

make_env() {
  umask 077
  mkdir -p "$run_dir"
  [[ -f "$key_file" ]] || : >"$key_file"
  [[ -f "$env_file" ]] && return 0
  {
    echo "LITELLM_MASTER_KEY=sk-$(openssl rand -hex 24)"
    echo "LITELLM_DB_PASSWORD=$(openssl rand -hex 16)"
    echo "GUARDRAIL_TOKEN=$(openssl rand -hex 24)"
  } >"$env_file"
}

# mint ALIAS AGENT FILE: a virtual key for big-default and local-small, tagged `agent:AGENT`.
mint() {
  local alias=$1 agent=$2 out=$3 master resp body
  body=$(printf '{"key_alias":"%s","models":["big-default","local-small"],"metadata":{"tags":["agent:%s"]}}' \
    "$alias" "$agent")
  master=$(sed -n 's/^LITELLM_MASTER_KEY=//p' "$env_file")
  resp=$(printf 'Authorization: Bearer %s\n' "$master" |
    curl -sS -m 30 -H @- -H 'Content-Type: application/json' --data-binary "$body" \
      "$litellm/key/generate")
  unset master
  umask 077
  printf '%s' "$resp" | python3 -I -c '
import json, sys
key = json.load(sys.stdin).get("key")
if not key:
    sys.exit("router-drill.sh: LiteLLM refused the key")
sys.stdout.write(f"LITELLM_API_KEY={key}\n")
' >"$out"
  unset resp
  echo "minted $alias (agent:$agent) into $out"
}

swap() {
  (cd "$root" && uv run --no-sync python -I -c '
import sys, yaml
path, target = sys.argv[1], sys.argv[2]
data = yaml.safe_load(open(path))
for route in data["model_list"]:
    if route["model_name"] == "big-default":
        route["litellm_params"]["model"] = f"openai/{target}"
        route["model_info"]["route_version"] += 1
open(path, "w").write(yaml.safe_dump(data, sort_keys=False))
' "$config" "$1")
  "${compose[@]}" restart litellm
  "${compose[@]}" up -d --wait litellm
  echo "big-default -> openai/$1"
}

redact() {
  python3 -I -c '
import re, sys, glob
secrets = []
for path in glob.glob(sys.argv[1] + "/*.env"):
    for line in open(path):
        _, sep, value = line.strip().partition("=")
        if sep and len(value) >= 8:
            secrets.append(value)
shapes = re.compile(r"sk-[A-Za-z0-9_-]{8,}|Bearer [A-Za-z0-9._-]+")
for line in sys.stdin:
    for value in secrets:
        line = line.replace(value, "***")
    sys.stdout.write(shapes.sub("***", line))
' "$run_dir"
}

case "${1:-}" in
  up)
    make_env
    (cd "$root" && uv run --no-sync python "$here/model_script.py" "$run_dir/model.yaml")
    cp "$here/router-drill.litellm.yaml" "$config"
    chmod 644 "$config"
    "${compose[@]}" up -d --wait --quiet-pull --build "${platform[@]}"
    mint svc-echo echo "$key_file"
    "${compose[@]}" up -d --wait --build chassis prometheus
    ;;
  down)
    make_env
    "${compose[@]}" down -v --remove-orphans
    rm -rf "$run_dir"
    ;;
  logs)
    "${compose[@]}" logs --no-color --tail "${ROUTER_DRILL_LOG_TAIL:-400}" 2>&1 | redact
    ;;
  key-hits)
    # G-1: how many log lines of every service hold a key value from .run/*.env, or anything
    # shaped like a LiteLLM key or a Bearer header. Prints the count only, never a line.
    "${compose[@]}" logs --no-color 2>&1 | python3 -I -c '
import re, sys, glob
secrets = []
for path in glob.glob(sys.argv[1] + "/*.env"):
    for line in open(path):
        _, sep, value = line.strip().partition("=")
        if sep and len(value) >= 8:
            secrets.append(value)
shapes = re.compile(r"sk-[A-Za-z0-9_-]{8,}|Bearer [A-Za-z0-9._-]{8,}")
lines = hits = 0
for line in sys.stdin:
    lines += 1
    if any(v in line for v in secrets) or shapes.search(line):
        hits += 1
print(f"lines={lines} hits={hits} values={len(secrets)}")
' "$run_dir"
    ;;
  env)
    echo "ROUTER_DRILL_RUN_DIR=$run_dir"
    ;;
  swap)
    swap "${2:?deployment}"
    ;;
  mint)
    mint "${2:?alias}" "${3:?agent}" "${4:?file}"
    ;;
  sql)
    "${compose[@]}" exec -T postgres psql -U litellm -d litellm --csv -v ON_ERROR_STOP=1 -f - "${@:2}"
    ;;
  *)
    echo "usage: $0 up|down|logs|env|swap <deployment>|mint <alias> <agent> <file>|sql" >&2
    exit 2
    ;;
esac
