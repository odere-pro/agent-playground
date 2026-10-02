#!/usr/bin/env bash
# PoC-5 seed: every credential in the kind cluster poc05 (plan
# docs/plans/2026-10-02-poc-05-sandboxed.md, section 2.9; bring-up order in section 8).
#
#   seed.sh base          first pass, before the platform starts: litellm-master, litellm-db,
#                         valkey-auth, minio-root, minio-chassis (poc05-platform only)
#   seed.sh keys          second pass, once LiteLLM is Ready: one virtual key per chassis
#                         (chassis-<svc>-litellm) and one token per remote (remote-<name>-token)
#   seed.sh rotate NAME   replace one Secret (and its LiteLLM key); then restart what mounts it
#   seed.sh rekey         new virtual keys for every service (after a Postgres restart)
#   seed.sh status        which Secrets exist (names only)
#
# Rules (checked by pocs/poc-05-sandboxed/tests/test_poc05_seed_static.py):
# - Every value is made here with `openssl rand -hex` and goes through a pipe into
#   `kubectl create secret ... =/dev/stdin --dry-run=client -o yaml | kubectl apply -f -`. A value
#   that goes into two Secrets is held in a shell variable for those pipes only, then unset.
# - A credential is only ever an argument of `printf` (a bash builtin, so never in any process's
#   argv), and that printf always writes into a pipe, never to the terminal. No `set -x`.
# - LiteLLM's admin API is reached through `kubectl port-forward` on a random local port. The
#   master key goes to curl as a header on stdin (`-H @-`); the new key comes out of the response
#   with jq and goes into its Secret.
# - Idempotent: an existing Secret is kept unless `rotate` names it. Applied server-side, so no
#   last-applied annotation copies the value.
# - Every kubectl call goes through `kctl`, pinned to the context kind-poc05.
set -euo pipefail

CONTEXT=kind-poc05
PLATFORM=poc05-platform
AGENTS=poc05-agents
REMOTE=poc05-remote
MODEL="fake-chat"

# One LiteLLM virtual key per chassis instance (T13's pods), with its MCP tool allow-list per
# gateway server (suggested, section 2.9): "<svc>|<fake_tools tools>|<code_runner tools>".
SERVICES=(
  "echo|glossary_lookup,note_write|run_python"
  "echo-remote|glossary_lookup,note_write|run_python"
)
# One token per remote (section 2.2), in poc05-agents (the chassis) and poc05-remote (T14).
# No probe entries: T10 is dropped (2026-10-02), so no pod would mount them (security review, 8).
REMOTES=(echo)

kctl() { kubectl --context "$CONTEXT" "$@"; }
log() { printf '[seed] %s\n' "$*" >&2; }
die() { log "$*"; exit 1; }

new_secret() { openssl rand -hex "${1:-32}"; }
new_id() { printf 'chassis%s' "$(openssl rand -hex 8)"; }

secret_exists() { kctl -n "$1" get secret "$2" >/dev/null 2>&1; }
drop_secret() { kctl -n "$1" delete secret "$2" --ignore-not-found >/dev/null; }

# put_secret NS NAME KEY: one key, its value on stdin.
put_secret() {
  kctl -n "$1" create secret generic "$2" --from-file="$3"=/dev/stdin --dry-run=client -o yaml |
    kctl apply --server-side --force-conflicts --field-manager=poc05-seed -f - >/dev/null
}

# put_env_secret NS NAME: several keys, KEY=value lines on stdin.
put_env_secret() {
  kctl -n "$1" create secret generic "$2" --from-env-file=/dev/stdin --dry-run=client -o yaml |
    kctl apply --server-side --force-conflicts --field-manager=poc05-seed -f - >/dev/null
}

# read_secret NS NAME KEY: the decoded value on stdout, for a command substitution only.
read_secret() { kctl -n "$1" get secret "$2" -o "jsonpath={.data.$3}" | base64 -d; }

preflight() {
  local tool
  for tool in kubectl jq openssl curl base64; do
    command -v "$tool" >/dev/null || die "missing $tool"
  done
  kctl get namespace "$PLATFORM" "$AGENTS" "$REMOTE" >/dev/null ||
    die "context $CONTEXT has no PoC-5 namespaces; run: deploy/kind/poc05/run.sh create"
}

# --- first pass -------------------------------------------------------------------------------

seed_base() {
  local value hash access
  if ! secret_exists "$PLATFORM" litellm-master; then
    value=$(new_secret)
    printf '%s' "$value" | put_secret "$PLATFORM" litellm-master LITELLM_MASTER_KEY
    unset value
    log "made $PLATFORM/litellm-master"
  fi
  if ! secret_exists "$PLATFORM" litellm-db; then
    value=$(new_secret)
    printf '%s' "$value" | put_secret "$PLATFORM" litellm-db password
    unset value
    log "made $PLATFORM/litellm-db"
  fi
  # Valkey gets an ACL file with the SHA-256 of the password; the chassis gets the password.
  if ! { secret_exists "$PLATFORM" valkey-auth && secret_exists "$AGENTS" valkey-auth; }; then
    value=$(new_secret)
    hash=$(printf '%s' "$value" | openssl dgst -sha256 -r | cut -d' ' -f1)
    # -@dangerous: no FLUSHALL, KEYS, CONFIG, DEBUG (review F9). The chassis uses GET, SET, DEL.
    # One user for every agent: B12 (per-agent key patterns) stays open until PoC-8.
    printf 'user default off\nuser chassis on #%s ~* &* +@all -@admin -@dangerous\n' "$hash" |
      put_secret "$PLATFORM" valkey-auth auth.conf
    printf '%s' "$value" | put_secret "$AGENTS" valkey-auth VALKEY_PASSWORD
    unset value hash
    log "made valkey-auth in $PLATFORM (ACL file) and $AGENTS (password)"
  fi
  # MinIO secrets are 48 hex characters, as the PoC-4 scale stack uses with the same image.
  if ! secret_exists "$PLATFORM" minio-root; then
    value=$(new_secret 24)
    printf 'MINIO_ROOT_USER=poc05-admin\nMINIO_ROOT_PASSWORD=%s\n' "$value" |
      put_env_secret "$PLATFORM" minio-root
    unset value
    log "made $PLATFORM/minio-root"
  fi
  # Only in $PLATFORM, for minio-init: no chassis reads S3 in PoC-5, so a copy in $AGENTS would
  # be a credential with no consumer (review F10). Copy it there with the first `config: s3` chassis.
  if ! secret_exists "$PLATFORM" minio-chassis; then
    access=$(new_id)
    value=$(new_secret 24)
    printf 'CONFIG_S3_ACCESS_KEY=%s\nCONFIG_S3_SECRET_KEY=%s\n' "$access" "$value" |
      put_env_secret "$PLATFORM" minio-chassis
    unset value access
    log "made minio-chassis in $PLATFORM (for minio-init)"
  fi
}

# --- second pass: LiteLLM virtual keys --------------------------------------------------------

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

# The /key/generate body. Verified on LiteLLM v1.103.0 on kind (T19, notes/2026-10-02-bring-up.md):
# - `object_permission.mcp_servers` and `.mcp_tool_permissions` are the per-key MCP fields; the
#   gateway lists only those tools and refuses any other ("not allowed for your key"). A key with
#   no object_permission lists zero tools. `metadata.tags` is stored as given (top-level `tags`
#   stays null).
# - `allowed_routes` (review F16): the route groups `openai_routes` (chat, models) and
#   `mcp_inference_routes` (/mcp and its tool calls). Management routes, MCP server CRUD
#   included, answer 403 for such a key.
key_body() {
  jq -nc --arg alias "svc-$1" --arg tag "agent:$1" --arg model "$MODEL" \
    --arg fake "$2" --arg code "$3" '
    def tools($s): $s | split(",") | map(select(. != ""));
    ({fake_tools: tools($fake), code_runner: tools($code)} | with_entries(select(.value != [])))
      as $perm
    | {key_alias: $alias, models: [$model], max_budget: 1.0, budget_duration: "1d",
       rpm_limit: 600, metadata: {tags: [$tag]},
       allowed_routes: ["openai_routes", "mcp_inference_routes"],
       object_permission: {mcp_servers: ($perm | keys), mcp_tool_permissions: $perm}}'
}

# make_keys ENTRY...: needs LITELLM up. Each old key with the same alias is deleted first, so a
# rotate, or a Secret lost while Postgres kept the key, never leaves two live keys.
make_keys() {
  local entry svc fake code master body resp key gone
  start_forward
  master=$(read_secret "$PLATFORM" litellm-master LITELLM_MASTER_KEY)
  [[ -n $master ]] || die "$PLATFORM/litellm-master is empty; run: seed.sh base"
  for entry in "$@"; do
    IFS='|' read -r svc fake code <<<"$entry"
    gone=$(jq -nc --arg alias "svc-$svc" '{key_aliases: [$alias]}')
    # /key/delete by `key_aliases` (verified on v1.103.0). A miss (no such key) is fine.
    printf 'Authorization: Bearer %s\n' "$master" | curl -sS -o /dev/null -H @- -H 'Content-Type: application/json' --data-binary "$gone" "$LITELLM/key/delete" 2>/dev/null || true
    body=$(key_body "$svc" "$fake" "$code")
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
  local entry svc fake code name value pending=()
  for entry in "${SERVICES[@]}"; do
    IFS='|' read -r svc fake code <<<"$entry"
    secret_exists "$AGENTS" "chassis-$svc-litellm" || pending+=("$entry")
  done
  if ((${#pending[@]} > 0)); then
    make_keys "${pending[@]}"
  fi
  for name in "${REMOTES[@]}"; do
    if secret_exists "$AGENTS" "remote-$name-token" && secret_exists "$REMOTE" "remote-$name-token"; then
      continue
    fi
    value=$(new_secret)
    printf '%s' "$value" | put_secret "$AGENTS" "remote-$name-token" token
    printf '%s' "$value" | put_secret "$REMOTE" "remote-$name-token" token
    unset value
    log "made remote-$name-token in $AGENTS and $REMOTE"
  done
}

# --- rotate, rekey, status ---------------------------------------------------------------------

service_known() {
  local entry
  for entry in "${SERVICES[@]}"; do
    [[ ${entry%%|*} != "$1" ]] || return 0
  done
  return 1
}

rotate() {
  local name=${1:-}
  case $name in
    litellm-master | litellm-db | minio-root)
      drop_secret "$PLATFORM" "$name"
      seed_base
      ;;
    valkey-auth)
      drop_secret "$PLATFORM" "$name"
      drop_secret "$AGENTS" "$name"
      seed_base
      ;;
    minio-chassis)
      drop_secret "$PLATFORM" "$name"
      seed_base
      ;;
    chassis-*-litellm)
      local svc=${name#chassis-}
      service_known "${svc%-litellm}" || die "no service ${svc%-litellm} in SERVICES"
      drop_secret "$AGENTS" "$name"
      seed_keys
      ;;
    remote-*-token)
      drop_secret "$AGENTS" "$name"
      drop_secret "$REMOTE" "$name"
      seed_keys
      ;;
    *) die "rotate: unknown Secret '$name'" ;;
  esac
  log "rotated $name; restart the pods that mount it (litellm-db: Postgres too, then rekey)"
}

rekey() {
  local entry
  for entry in "${SERVICES[@]}"; do
    drop_secret "$AGENTS" "chassis-${entry%%|*}-litellm"
  done
  seed_keys
}

status() {
  local ns
  for ns in "$PLATFORM" "$AGENTS" "$REMOTE"; do
    printf '%s:\n' "$ns"
    kctl -n "$ns" get secrets -o name | sed 's#^secret/#  #'
  done
}

usage() { sed -n '2,13p' "$0"; }

(($# > 0)) || { usage; exit 0; }
case $1 in
  base) preflight; seed_base ;;
  keys) preflight; seed_keys ;;
  rotate) preflight; rotate "${2:-}" ;;
  rekey) preflight; rekey ;;
  status) status ;;
  *) usage; exit 2 ;;
esac
