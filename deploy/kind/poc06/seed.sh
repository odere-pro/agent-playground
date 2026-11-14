#!/usr/bin/env bash
# PoC-6 seed: the credentials the PoC-6 services need in the kind cluster poc05, made after the
# PoC-5 bring-up (`deploy/kind/poc05/run.sh up`) and its `seed.sh base` / `seed.sh keys`. It does
# for the PoC-6 services what deploy/kind/poc05/platform/seed.sh does for the PoC-5 ones, without
# editing that file (PoC-5 files are read-only for PoC-6; the arrays there are not extendable).
#
#   seed.sh keys      one LiteLLM virtual key per new chassis (chassis-<svc>-litellm), each with an
#                     MCP allow-list of glossary_lookup and acronym_expand on the gateway server
#                     `fake_tools`; one token Secret per new remote (remote-<name>-token, two keys)
#   seed.sh rotate remote-<name>-token   drop and remake it (also the fix for an old one-key one)
#   seed.sh status    which Secrets exist (names only)
#
# Rules (checked by pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_kind_static.py):
# - Every value is made with `openssl rand -hex` and goes through a pipe into
#   `kubectl create secret ... =/dev/stdin --dry-run=client -o yaml | kubectl apply -f -`. A value
#   that goes into two Secrets is held in a shell variable for those pipes only, then unset.
# - A credential is only ever an argument of `printf` (a bash builtin, so never in any process's
#   argv), and that printf always writes into a pipe. No `set -x`.
# - LiteLLM's admin API is reached through `kubectl port-forward` on a random local port. The
#   master key goes to curl as a header on stdin (`-H @-`); the new key comes out of the response
#   with jq and goes into its Secret.
# - Idempotent: an existing Secret is kept. Applied server-side, so no last-applied annotation
#   copies the value.
# - Every kubectl call goes through `kctl`, pinned to the context kind-poc05.
set -euo pipefail

CONTEXT=kind-poc05
LOG_PREFIX=seed-poc06
PLATFORM=poc05-platform
AGENTS=poc05-agents
REMOTE=poc05-remote
HERE=$(cd "$(dirname "$0")" && pwd)
MODEL="fake-chat"
TOOLS="glossary_lookup,acronym_expand"

# One virtual key per chassis instance: "<svc>". Each gets `fake_tools` with $TOOLS and no other
# gateway server (no code_runner). The Secret is chassis-<svc>-litellm in poc05-agents.
SERVICES=(
  openai-agents
  typescript
  smolagents-remote
  claude-agent-remote
  typescript-remote
  kagent-adk-remote
)
# One token per remote, in poc05-agents (its chassis) and poc05-remote (the remote pod). Rule 7c
# (admission) ties remote-<name>-token to the pod label app.kubernetes.io/name: remote-<name>.
REMOTES=(smolagents claude-agent typescript)
# kagent-adk uses its inbound bearer as its model key (api_key_passthrough) and holds no Secret, so
# its token is made in poc05-agents only.
CHASSIS_ONLY_REMOTES=(kagent-adk)

# shellcheck source=deploy/kind/lib.sh
source "$HERE/../lib.sh"

new_secret() { openssl rand -hex "${1:-32}"; }

secret_exists() { kctl -n "$1" get secret "$2" >/dev/null 2>&1; }
drop_secret() { kctl -n "$1" delete secret "$2" --ignore-not-found >/dev/null; }

# token_keys_ok NS NAME: the remote token Secret holds both keys, one per direction (055 CH-6).
# The template prints key names only, never a value.
token_keys_ok() {
  local keys
  keys=" $(kctl -n "$1" get secret "$2" -o 'go-template={{range $k, $_ := .data}}{{$k}} {{end}}') "
  [[ $keys == *" chassis-to-workload "* && $keys == *" workload-to-chassis "* ]]
}

# put_secret NS NAME KEY: one key, its value on stdin.
put_secret() {
  kctl -n "$1" create secret generic "$2" --from-file="$3"=/dev/stdin --dry-run=client -o yaml |
    kctl apply --server-side --force-conflicts --field-manager=poc06-seed -f - >/dev/null
}

# put_env_secret NS NAME: several keys, KEY=value lines on stdin.
put_env_secret() {
  kctl -n "$1" create secret generic "$2" --from-env-file=/dev/stdin --dry-run=client -o yaml |
    kctl apply --server-side --force-conflicts --field-manager=poc06-seed -f - >/dev/null
}

# read_secret NS NAME KEY: the decoded value on stdout, for a command substitution only.
read_secret() { kctl -n "$1" get secret "$2" -o "jsonpath={.data.$3}" | base64 -d; }

preflight() {
  local tool
  for tool in kubectl jq openssl curl base64; do
    command -v "$tool" >/dev/null || die "missing $tool"
  done
  kctl get namespace "$PLATFORM" "$AGENTS" "$REMOTE" >/dev/null ||
    die "context $CONTEXT has no PoC-5 namespaces; run: deploy/kind/poc05/run.sh up"
  secret_exists "$PLATFORM" litellm-master ||
    die "no $PLATFORM/litellm-master; run: deploy/kind/poc05/run.sh up"
}

PF_PID=""
PF_LOG=""
LITELLM=""

stop_forward() {
  if [[ -n $PF_PID ]]; then kill "$PF_PID" 2>/dev/null || true; fi
  if [[ -n $PF_LOG ]]; then rm -f "$PF_LOG"; fi
  PF_PID=""
  PF_LOG=""
}
trap stop_forward EXIT

start_forward() {
  local port=""
  PF_LOG=$(mktemp)
  kctl -n "$PLATFORM" port-forward --address 127.0.0.1 svc/litellm :4000 >"$PF_LOG" 2>&1 &
  PF_PID=$!
  for _ in $(seq 1 50); do
    port=$(sed -n 's/^Forwarding from 127\.0\.0\.1:\([0-9][0-9]*\) .*/\1/p' "$PF_LOG" | head -n 1)
    [[ -z $port ]] || break
    kill -0 "$PF_PID" 2>/dev/null || die "port-forward to litellm failed: $(cat "$PF_LOG")"
    sleep 0.2
  done
  [[ -n $port ]] || die "port-forward to litellm did not start"
  LITELLM=http://127.0.0.1:$port
  for _ in $(seq 1 60); do
    curl -fsS -o /dev/null "$LITELLM/health/liveliness" 2>/dev/null && return 0
    sleep 1
  done
  die "LiteLLM did not answer /health/liveliness in 60 s"
}

# The /key/generate body, the shape of deploy/kind/poc05/platform/seed.sh key_body: the model
# route, the route groups openai_routes and mcp_inference_routes only, and the per-key MCP
# permission for the gateway server fake_tools.
key_body() {
  jq -nc --arg alias "svc-$1" --arg tag "agent:$1" --arg model "$MODEL" --arg tools "$TOOLS" '
    ($tools | split(",") | map(select(. != ""))) as $t
    | {key_alias: $alias, models: [$model], max_budget: 1.0, budget_duration: "1d",
       rpm_limit: 600, metadata: {tags: [$tag]},
       allowed_routes: ["openai_routes", "mcp_inference_routes"],
       object_permission: {mcp_servers: ["fake_tools"], mcp_tool_permissions: {fake_tools: $t}}}'
}

# drop_old_key SVC: delete every key with the alias svc-SVC, and fail closed, as
# deploy/kind/poc05/platform/seed.sh does (code review R5a-09). Reads `master` from make_keys (bash
# dynamic scope), so the master key is never a function argument. A 2xx, 400, or 404 counts only
# once /key/list shows no key left under the alias; any other status (401, 403, 5xx, no answer)
# stops the run before a new key is minted.
drop_old_key() {
  local gone status left
  gone=$(jq -nc --arg alias "svc-$1" '{key_aliases: [$alias]}')
  status=$(printf 'Authorization: Bearer %s\n' "$master" | curl -sS -o /dev/null -w '%{http_code}' -H @- -H 'Content-Type: application/json' --data-binary "$gone" "$LITELLM/key/delete" 2>/dev/null) || status=000
  [[ $status =~ ^(2[0-9][0-9]|400|404)$ ]] || die "LiteLLM /key/delete failed for svc-$1; no key minted"
  left=$(printf 'Authorization: Bearer %s\n' "$master" | curl -fsS -H @- --get --data-urlencode "key_alias=svc-$1" "$LITELLM/key/list" 2>/dev/null) ||
    die "LiteLLM /key/list failed for svc-$1; no key minted"
  printf '%s' "$left" | jq -e '(.keys | type) == "array" and (.keys | length) == 0' >/dev/null 2>&1 ||
    die "a key with alias svc-$1 is still live after /key/delete; no key minted"
  unset status left
}

# make_keys SVC...: needs LITELLM up. Each old key with the same alias is deleted first, and checked
# gone, so a Secret lost while Postgres kept the key never leaves two live keys.
make_keys() {
  local svc master body resp key
  start_forward
  master=$(read_secret "$PLATFORM" litellm-master LITELLM_MASTER_KEY)
  [[ -n $master ]] || die "$PLATFORM/litellm-master is empty"
  for svc in "$@"; do
    drop_old_key "$svc"
    body=$(key_body "$svc")
    resp=$(printf 'Authorization: Bearer %s\n' "$master" | curl -sS -H @- -H 'Content-Type: application/json' --data-binary "$body" "$LITELLM/key/generate")
    if ! key=$(printf '%s' "$resp" | jq -er .key); then
      # A failed call has no key in it; show LiteLLM's error message only.
      printf '%s' "$resp" | jq -r '.error.message // .detail // "no key in the response"' >&2 || true
      unset master resp
      die "LiteLLM refused the key for $svc"
    fi
    printf '%s' "$key" | put_secret "$AGENTS" "chassis-$svc-litellm" LITELLM_API_KEY
    unset key resp
    log "made $AGENTS/chassis-$svc-litellm (alias svc-$svc)"
  done
  unset master
  stop_forward
}

seed_keys() {
  local svc name to_workload to_chassis pending=()
  for svc in "${SERVICES[@]}"; do
    secret_exists "$AGENTS" "chassis-$svc-litellm" || pending+=("$svc")
  done
  if ((${#pending[@]} > 0)); then
    make_keys "${pending[@]}"
  fi
  for name in "${REMOTES[@]}"; do
    if secret_exists "$AGENTS" "remote-$name-token" && secret_exists "$REMOTE" "remote-$name-token"; then
      if token_keys_ok "$AGENTS" "remote-$name-token" && token_keys_ok "$REMOTE" "remote-$name-token"; then
        continue
      fi
      die "remote-$name-token is the old one-key Secret; run: $0 rotate remote-$name-token"
    fi
    # Two keys, one per direction (055 CH-6): two random values, never equal.
    to_workload=$(new_secret)
    to_chassis=$(new_secret)
    [[ $to_workload != "$to_chassis" ]] || die "remote-$name-token: the two keys came out equal"
    printf 'chassis-to-workload=%s\nworkload-to-chassis=%s\n' "$to_workload" "$to_chassis" |
      put_env_secret "$AGENTS" "remote-$name-token"
    printf 'chassis-to-workload=%s\nworkload-to-chassis=%s\n' "$to_workload" "$to_chassis" |
      put_env_secret "$REMOTE" "remote-$name-token"
    unset to_workload to_chassis
    log "made remote-$name-token (two keys) in $AGENTS and $REMOTE"
  done
  for name in "${CHASSIS_ONLY_REMOTES[@]}"; do
    if secret_exists "$AGENTS" "remote-$name-token"; then
      token_keys_ok "$AGENTS" "remote-$name-token" && continue
      die "remote-$name-token is the old one-key Secret; run: $0 rotate remote-$name-token"
    fi
    to_workload=$(new_secret)
    to_chassis=$(new_secret)
    [[ $to_workload != "$to_chassis" ]] || die "remote-$name-token: the two keys came out equal"
    printf 'chassis-to-workload=%s\nworkload-to-chassis=%s\n' "$to_workload" "$to_chassis" |
      put_env_secret "$AGENTS" "remote-$name-token"
    unset to_workload to_chassis
    log "made remote-$name-token (two keys) in $AGENTS"
  done
}

status() {
  local ns
  for ns in "$AGENTS" "$REMOTE"; do
    printf '%s:\n' "$ns"
    kctl -n "$ns" get secrets -o name | sed 's#^secret/#  #'
  done
}

rotate() {
  local name=${1:-}
  case $name in
    remote-*-token)
      drop_secret "$AGENTS" "$name"
      drop_secret "$REMOTE" "$name"
      seed_keys
      ;;
    *) die "rotate: unknown Secret '$name'" ;;
  esac
  log "rotated $name; restart its chassis pod and its remote pods"
}

usage() { sed -n '2,11p' "$0"; }

(($# > 0)) || { usage; exit 0; }
case $1 in
  keys) preflight; seed_keys ;;
  rotate) preflight; rotate "${2:-}" ;;
  status) status ;;
  *) usage; exit 2 ;;
esac
