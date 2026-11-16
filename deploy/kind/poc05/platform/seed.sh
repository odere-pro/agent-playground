#!/usr/bin/env bash
# PoC-5 seed: every credential in the kind cluster poc05 (plan
# docs/plans/2026-10-02-poc-05-sandboxed.md, section 2.9; bring-up order in section 8).
#
#   seed.sh base          first pass, before the platform starts: litellm-master, litellm-db,
#                         valkey-auth (one Valkey user per chassis), minio-root,
#                         chassis-echo-minio (both namespaces), code-runner-dispatch-token
#                         (poc05-platform only), chart-drill-token (poc05-chart)
#   seed.sh keys          second pass, once LiteLLM is Ready: one virtual key per chassis
#                         (chassis-<svc>-litellm) and one token per remote (remote-<name>-token)
#   seed.sh rotate NAME   replace one Secret (and its LiteLLM key); then restart what mounts it
#   seed.sh rekey         a new virtual key for every PoC-5 service while the old one answers:
#                         mint, restart what mounts it, then revoke the old key (026 CH-4)
#   seed.sh revoke-grants SVC  take every MCP tool grant off SVC's key (026 CH-4)
#   seed.sh revoke SVC    delete SVC's key and its Secret (026 CH-4)
#   seed.sh key-info      per key alias: how many keys, created, expires (no key, no hash)
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
LOG_PREFIX=seed
PLATFORM=poc05-platform
AGENTS=poc05-agents
REMOTE=poc05-remote
# The library chart's drill (024 CH-3): two example-agent releases a kind test installs.
CHART=poc05-chart
HERE=$(cd "$(dirname "$0")" && pwd)
MODEL="fake-chat"
# suggested: where each minted key's manifest goes (svc-<svc>.json, its routes only, never the
# key), for `agentctl new --key-manifest` (059 H-15). Git-ignored. KEY_MANIFEST_DIR overrides it.
KEY_MANIFESTS=${KEY_MANIFEST_DIR:-$HERE/../../.key-manifests}

# One LiteLLM virtual key per chassis instance (T13's pods), with its MCP tool allow-list per
# gateway server (suggested, section 2.9): "<svc>|<fake_tools tools>|<code_runner tools>".
SERVICES=(
  "echo|glossary_lookup,note_write|run_python"
  "echo-remote|glossary_lookup,note_write|run_python"
)
# One token per remote (section 2.2), in poc05-agents (the chassis) and poc05-remote (T14).
# No probe entries: T10 is dropped (2026-10-02), so no pod would mount them (security review, 8).
REMOTES=(echo)
# The credential drill's two services (026 CH-4; pocs/poc-09-agent-mvp-template/tests/
# test_poc09_kind_ch4.py): two releases of the library chart in $CHART, each with its own key
# (Secret chassis-<svc>-litellm) and its own Valkey user limited to `<svc>:*` (Secret
# chassis-<svc>-valkey). "<svc>|<model>|<fake_tools tools>|<code_runner tools>": a tool and a key
# prefix the other service does not have. Open: a model of its own for ch4-b needs a second route
# in platform/litellm/config.yaml and its pinned copy in deploy/kind/poc06/hosted/.
CHART_SERVICES=(
  "ch4-a|fake-chat|glossary_lookup|"
  "ch4-b|fake-chat|note_write|"
)
# suggested: every virtual key expires after 30 days (026 CH-4, gap review 2026-11-07); `rekey`
# replaces it before then.
KEY_DURATION=30d
# What a per-service Valkey user may do, on its own key prefix only: an allow-list, never +@all.
# What the chassis's client sends (packages/chassis/src/chassis/adapters/valkey/state.py): GET,
# SET, DEL; EVALSHA and, on NOSCRIPT, SCRIPT LOAD for its compare-and-set script; PING for its
# idle health check. AUTH needs no grant. No FUNCTION, no other SCRIPT subcommand, no DBSIZE, no
# key listing, no channels.
SERVICE_ACL="resetchannels -@all +get +set +del +evalsha +script|load +ping"
# suggested: how long a key renamed to svc-<svc>-retiring stays valid if a rekey dies before it
# revokes it (platform-security, 2026-11-16).
RETIRE_DURATION=1h
# The MinIO secret key of the one chassis that reads its config from the store (agent-echo,
# `config: minio`). Its user name, `chassis-echo`, is plain text in agent-echo.yaml and minio.yaml.
STORE_READER_SECRET=chassis-echo-minio

# shellcheck source=deploy/kind/lib.sh
source "$HERE/../../lib.sh"

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
    kctl apply --server-side --force-conflicts --field-manager=poc05-seed -f - >/dev/null
}

# put_env_secret NS NAME: several keys, KEY=value lines on stdin.
put_env_secret() {
  kctl -n "$1" create secret generic "$2" --from-env-file=/dev/stdin --dry-run=client -o yaml |
    kctl apply --server-side --force-conflicts --field-manager=poc05-seed -f - >/dev/null
}

# read_secret NS NAME KEY: the decoded value on stdout, for a command substitution only.
read_secret() { kctl -n "$1" get secret "$2" -o "jsonpath={.data.$3}" | base64 -d; }

valkey_secrets_exist() {
  local svc
  secret_exists "$PLATFORM" valkey-auth && secret_exists "$AGENTS" valkey-auth || return 1
  for svc in "${CHART_SERVICES[@]%%|*}"; do
    secret_exists "$CHART" "chassis-$svc-valkey" || return 1
  done
}

# valkey_acl: the Valkey ACL file on stdout, for a pipe into its Secret. Additive: a password
# whose Secret exists is read back and kept, so adding the drill users never changes the PoC-5
# chassis password; a missing one is made here. Only each password's SHA-256 goes into the file
# (acl_user). -@dangerous: no FLUSHALL, KEYS, CONFIG, DEBUG (review F9). `chassis`: the PoC-5
# agents' one user, every key (unchanged). Each drill service: its own user, its own prefix, an
# allow-list of commands (B12, 026 CH-4).
valkey_acl() {
  local svc
  printf 'user default off\n'
  valkey_password "$AGENTS" valkey-auth | acl_user chassis '~* &* +@all -@admin -@dangerous'
  for svc in "${CHART_SERVICES[@]%%|*}"; do
    valkey_password "$CHART" "chassis-$svc-valkey" | acl_user "$svc" "~$svc:* $SERVICE_ACL"
  done
}

# valkey_password NS NAME: the password in NS/NAME on stdout, for a pipe into acl_user only. A
# missing Secret is made first.
valkey_password() {
  local value
  if ! secret_exists "$1" "$2"; then
    value=$(new_secret)
    printf '%s' "$value" | put_secret "$1" "$2" VALKEY_PASSWORD
    unset value
  fi
  read_secret "$1" "$2" VALKEY_PASSWORD
}

# acl_user NAME RULES: one ACL line for the password on stdin; only its SHA-256 is written.
acl_user() {
  openssl dgst -sha256 -r | awk -v user="$1" -v rules="$2" '{ print "user " user " on #" $1 " " rules }'
}

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
  local value
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
  # Valkey gets an ACL file with the SHA-256 of each password; each chassis gets its password.
  if ! valkey_secrets_exist; then
    valkey_acl | put_secret "$PLATFORM" valkey-auth auth.conf
    log "made valkey-auth in $PLATFORM (ACL file); kept or made each password in $AGENTS and" \
      "$CHART (${CHART_SERVICES[*]%%|*}); a running Valkey loads the file only on restart"
  fi
  # MinIO secrets are 48 hex characters, as the PoC-4 scale stack uses with the same image.
  if ! secret_exists "$PLATFORM" minio-root; then
    value=$(new_secret 24)
    printf 'MINIO_ROOT_USER=poc05-admin\nMINIO_ROOT_PASSWORD=%s\n' "$value" |
      put_env_secret "$PLATFORM" minio-root
    unset value
    log "made $PLATFORM/minio-root"
  fi
  # The dispatcher's inbound bearer (code review R5a-23): one Secret, read as a file by the
  # dispatcher (--inbound-token-file) and as env by LiteLLM, which sends it to code_runner only.
  if ! secret_exists "$PLATFORM" code-runner-dispatch-token; then
    value=$(new_secret)
    printf '%s' "$value" | put_secret "$PLATFORM" code-runner-dispatch-token token
    unset value
    log "made $PLATFORM/code-runner-dispatch-token"
  fi
  # The bucket-wide reader of older runs had no consumer (review F10): drop a copy left behind.
  drop_secret "$PLATFORM" minio-chassis
  # agent-echo's chassis reads its config document from MinIO (`config: minio`) as the user
  # `chassis-echo`, a plain name in agent-echo.yaml and minio.yaml, never in a Secret. Its secret
  # key: the chassis container in $AGENTS, minio-init in $PLATFORM (it makes the user).
  if ! { secret_exists "$PLATFORM" "$STORE_READER_SECRET" &&
    secret_exists "$AGENTS" "$STORE_READER_SECRET"; }; then
    value=$(new_secret 24)
    printf '%s' "$value" | put_secret "$AGENTS" "$STORE_READER_SECRET" CONFIG_S3_SECRET_KEY
    printf '%s' "$value" | put_secret "$PLATFORM" "$STORE_READER_SECRET" CONFIG_S3_SECRET_KEY
    unset value
    log "made $STORE_READER_SECRET in $AGENTS (the chassis) and $PLATFORM (for minio-init)"
  fi
  # The library chart's drill (024 CH-3): a stand-in credential the chart gives the chassis
  # container only, as env and as a file. No service reads its value; the kind test checks names
  # and paths from inside the workload (test_poc09_kind_chart.py).
  if ! secret_exists "$CHART" chart-drill-token; then
    value=$(new_secret)
    printf '%s' "$value" | put_secret "$CHART" chart-drill-token token
    unset value
    log "made $CHART/chart-drill-token"
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
# - `duration` (026 CH-4): the key expires after $KEY_DURATION (LiteLLM sets `expires`).
# key_body SVC MODEL FAKE_TOOLS CODE_TOOLS
key_body() {
  jq -nc --arg alias "svc-$1" --arg tag "agent:$1" --arg model "$2" \
    --arg fake "$3" --arg code "$4" --arg duration "$KEY_DURATION" '
    def tools($s): $s | split(",") | map(select(. != ""));
    ({fake_tools: tools($fake), code_runner: tools($code)} | with_entries(select(.value != [])))
      as $perm
    | {key_alias: $alias, models: [$model], max_budget: 1.0, budget_duration: "1d",
       duration: $duration, rpm_limit: 600, metadata: {tags: [$tag]},
       allowed_routes: ["openai_routes", "mcp_inference_routes"],
       object_permission: {mcp_servers: ($perm | keys), mcp_tool_permissions: $perm}}'
}

# drop_old_key SVC: delete every key with the alias svc-SVC, and fail closed. Reads `master` from
# make_keys (bash dynamic scope), so the master key is never a function argument. /key/delete by
# `key_aliases` (verified on v1.103.0). How it answers a miss is not recorded, so a 2xx, 400, or
# 404 counts only once /key/list shows no key left under the alias; any other status (401, 403,
# 5xx, no answer) stops the run before a new key is minted (code review R5a-09).
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

# all_services: every service as "NS|SVC|MODEL|FAKE_TOOLS|CODE_TOOLS", one per line: the PoC-5
# chassis pods (SERVICES, in $AGENTS, all on $MODEL), then the drill's (CHART_SERVICES, in $CHART).
all_services() {
  local entry svc model fake code
  for entry in "${SERVICES[@]}"; do
    IFS='|' read -r svc fake code <<<"$entry"
    printf '%s|%s|%s|%s|%s\n' "$AGENTS" "$svc" "$MODEL" "$fake" "$code"
  done
  for entry in "${CHART_SERVICES[@]}"; do
    IFS='|' read -r svc model fake code <<<"$entry"
    printf '%s|%s|%s|%s|%s\n' "$CHART" "$svc" "$model" "$fake" "$code"
  done
}

# service_entry SVC: SVC's line from all_services; fails for an unknown service.
service_entry() {
  local line
  while IFS= read -r line; do
    [[ $(cut -d'|' -f2 <<<"$line") != "$1" ]] || { printf '%s\n' "$line"; return 0; }
  done < <(all_services)
  return 1
}

# make_keys ENTRY...: entries as all_services prints them; needs LITELLM up. Each old key with the
# same alias is deleted first, and checked gone, so a rotate, or a Secret lost while Postgres kept
# the key, never leaves two live keys.
make_keys() {
  local entry ns svc model fake code master body resp key
  start_forward
  master=$(read_secret "$PLATFORM" litellm-master LITELLM_MASTER_KEY)
  [[ -n $master ]] || die "$PLATFORM/litellm-master is empty; run: seed.sh base"
  for entry in "$@"; do
    IFS='|' read -r ns svc model fake code <<<"$entry"
    drop_old_key "$svc"
    body=$(key_body "$svc" "$model" "$fake" "$code")
    resp=$(printf 'Authorization: Bearer %s\n' "$master" | curl -sS -H @- -H 'Content-Type: application/json' --data-binary "$body" "$LITELLM/key/generate")
    if ! key=$(printf '%s' "$resp" | jq -er .key); then
      # A failed call has no key in it; show LiteLLM's error message only.
      printf '%s' "$resp" | jq -r '.error.message // .detail // "no key in the response"' >&2 || true
      unset master resp
      die "LiteLLM refused the key for $svc"
    fi
    printf '%s' "$key" | put_secret "$ns" "chassis-$svc-litellm" LITELLM_API_KEY
    unset key resp
    # Key manifests are for agentctl (059 H-15): the PoC-5 services only, not the drill's.
    if [[ $ns == "$AGENTS" ]]; then
      write_key_manifest "$KEY_MANIFESTS" "svc-$svc" "$model"
    fi
    log "made $ns/chassis-$svc-litellm (alias svc-$svc, model $model, expires in $KEY_DURATION)"
  done
  unset master
  stop_forward
}

seed_keys() {
  local entry ns svc model name to_workload to_chassis pending=()
  while IFS= read -r entry; do
    IFS='|' read -r ns svc model _ <<<"$entry"
    if ! secret_exists "$ns" "chassis-$svc-litellm"; then
      pending+=("$entry")
    elif [[ $ns == "$AGENTS" && ! -e $KEY_MANIFESTS/svc-$svc.json ]]; then
      # The key was minted here with $model: its manifest comes from that, never from the Secret.
      # Only when none is there yet; `rekey` writes a fresh one.
      write_key_manifest "$KEY_MANIFESTS" "svc-$svc" "$model"
    fi
  done < <(all_services)
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
    # Two keys, one per direction (055 CH-6): the chassis sends chassis-to-workload and its
    # remote listener accepts only workload-to-chassis. Two random values, never equal.
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
}

# --- rotate, rekey, status ---------------------------------------------------------------------

rotate() {
  local name=${1:-} svc entry
  case $name in
    litellm-master | litellm-db | minio-root | code-runner-dispatch-token)
      drop_secret "$PLATFORM" "$name"
      seed_base
      ;;
    valkey-auth)
      drop_secret "$PLATFORM" "$name"
      drop_secret "$AGENTS" "$name"
      for svc in "${CHART_SERVICES[@]%%|*}"; do
        drop_secret "$CHART" "chassis-$svc-valkey"
      done
      seed_base
      ;;
    chassis-echo-minio)
      drop_secret "$PLATFORM" "$name"
      drop_secret "$AGENTS" "$name"
      seed_base
      ;;
    chassis-*-litellm)
      svc=${name#chassis-}
      entry=$(service_entry "${svc%-litellm}") ||
        die "no service ${svc%-litellm} in SERVICES or CHART_SERVICES"
      drop_secret "${entry%%|*}" "$name"
      seed_keys
      ;;
    remote-*-token)
      drop_secret "$AGENTS" "$name"
      drop_secret "$REMOTE" "$name"
      seed_keys
      ;;
    *) die "rotate: unknown Secret '$name'" ;;
  esac
  log "rotated $name; restart the pods that mount it (litellm-db: Postgres too, then rekey;" \
    "code-runner-dispatch-token: litellm and code-runner-dispatch;" \
    "chassis-echo-minio: first delete job/minio-init and run run.sh apply)"
}

# --- rekey and revoke (026 CH-4) ----------------------------------------------------------------
#
# A call that carries a key in its body (/key/update) sends the master key and the body through
# one curl config on stdin (`-K -`): neither is in any argv. The JSON body holds no backslash, so
# escaping its quotes is all the config syntax needs.

# admin_update BODY_JSON_ON_STDIN: POST /key/update with the body on stdin; prints the HTTP status.
# Reads `master` from the caller (bash dynamic scope), never as an argument.
admin_update() {
  local body
  body=$(cat)
  printf 'header = "Authorization: Bearer %s"\nheader = "Content-Type: application/json"\ndata-binary = "%s"\n' "$master" "${body//\"/\\\"}" | curl -sS -o /dev/null -w '%{http_code}' -K - "$LITELLM/key/update" 2>/dev/null || printf '000'
  unset body
}

# update_key NS SVC JQ_ARGS... FILTER: /key/update for the key in NS/chassis-SVC-litellm, the body
# made by FILTER with the key as `.`; prints the HTTP status. Reads `master` from the caller.
update_key() {
  local ns=$1 svc=$2 key
  shift 2
  key=$(read_secret "$ns" "chassis-$svc-litellm" LITELLM_API_KEY)
  printf '%s' "$key" | jq -Rc "$@" | admin_update
  unset key
}

# key_count ALIAS: how many keys LiteLLM holds under ALIAS; fails closed on any error.
key_count() {
  local left
  left=$(printf 'Authorization: Bearer %s\n' "$master" | curl -fsS -H @- --get --data-urlencode "key_alias=$1" "$LITELLM/key/list" 2>/dev/null) ||
    die "LiteLLM /key/list failed for $1"
  printf '%s' "$left" | jq -er 'if (.keys | type) == "array" then .keys | length else error end' 2>/dev/null ||
    die "LiteLLM /key/list for $1 has no key list"
  unset left
}

# delete_alias ALIAS: delete every key under ALIAS, and check none is left (as drop_old_key).
delete_alias() {
  local gone status
  gone=$(jq -nc --arg alias "$1" '{key_aliases: [$alias]}')
  status=$(printf 'Authorization: Bearer %s\n' "$master" | curl -sS -o /dev/null -w '%{http_code}' -H @- -H 'Content-Type: application/json' --data-binary "$gone" "$LITELLM/key/delete" 2>/dev/null) || status=000
  [[ $status =~ ^(2[0-9][0-9]|400|404)$ ]] || die "LiteLLM /key/delete failed for $1"
  [[ $(key_count "$1") == 0 ]] || die "a key with alias $1 is still live after /key/delete"
}

# require_status WANT WHAT: GET /v1/models with the key on stdin; dies unless LiteLLM answers
# HTTP WANT, and logs "WHAT gets HTTP <code>". Refuses an empty key, which proves nothing.
require_status() {
  local key
  key=$(cat)
  [[ -n $key ]] || die "$2: no key to check with"
  printf 'Authorization: Bearer %s\n' "$key" | curl -sS -o /dev/null -w '%{http_code}' -H @- "$LITELLM/v1/models" 2>/dev/null | expect_code "$1" "$2"
  unset key
}

# expect_code WANT WHAT: the HTTP status on stdin must be WANT.
expect_code() {
  local code
  code=$(cat)
  [[ $code == "$1" ]] || die "$2 gets HTTP ${code:-000}, want $1"
  log "$2 gets HTTP $code"
}

# restart_consumers NS SECRET: restart every Deployment in NS whose pods read SECRET, and wait
# until each has rolled. Rolling with maxUnavailable 0, so an old pod answers until its new one is
# Ready.
restart_consumers() {
  local name
  for name in $(kctl -n "$1" get deployments -o json | jq -r --arg s "$2" '.items[]
      | select([.spec.template.spec | ((.containers // []) + (.initContainers // []))[]
          | ((.env // [])[] | .valueFrom.secretKeyRef.name?), ((.envFrom // [])[] | .secretRef.name?)]
        | index($s))
      | .metadata.name'); do
    kctl -n "$1" rollout restart "deployment/$name" >/dev/null
    kctl -n "$1" rollout status "deployment/$name" --timeout=300s >/dev/null ||
      die "rekey: $1/$name did not roll out with the new key; the old key is still live"
    log "rekey: $1/$name rolled out with the new key"
  done
}

# rekey_one ENTRY: one service, the old key kept live until every pod holds the new one.
# 1. The old key is read from its Secret and held (never printed).
# 2. The live key moves to the alias svc-SVC-retiring (aliases are unique in LiteLLM) and gets
#    `duration: $RETIRE_DURATION`, so it dies on its own if this run stops before step 5.
# 3. A new key is minted under svc-SVC and overwrites the Secret (a server-side apply: the Secret
#    is never missing, so no pod start fails on it).
# 4. Every Deployment that reads the Secret rolls; the old pods answer with the old key meanwhile.
# 5. The retiring key is deleted and checked gone; the held old key must get 401 on /v1/models
#    (LiteLLM caches keys, so the list alone is no proof) and the new key 200.
# No live old key (a Postgres reset): steps 2 and 5 are skipped, and the service has no working
# key until step 4. After a Postgres reset, `seed.sh keys` is the documented step (runbook).
rekey_one() {
  local ns svc master old updated retiring had_old=0
  IFS='|' read -r ns svc _ <<<"$1"
  retiring=svc-$svc-retiring
  start_forward
  master=$(read_secret "$PLATFORM" litellm-master LITELLM_MASTER_KEY)
  [[ -n $master ]] || die "$PLATFORM/litellm-master is empty; run: seed.sh base"
  [[ $(key_count "$retiring") == 0 ]] ||
    die "a key under $retiring is left from an unfinished rekey; check which pods use it first"
  if secret_exists "$ns" "chassis-$svc-litellm"; then
    old=$(read_secret "$ns" "chassis-$svc-litellm" LITELLM_API_KEY)
    updated=$(update_key "$ns" "$svc" --arg alias "$retiring" --arg duration "$RETIRE_DURATION" '{key: ., key_alias: $alias, duration: $duration}')
    if [[ $updated =~ ^2[0-9][0-9]$ && $(key_count "$retiring") == 1 ]]; then
      had_old=1
      log "rekey: the live key of svc-$svc is now $retiring (expires in $RETIRE_DURATION) until the pods roll"
    else
      # Fail closed: a live key that could not be moved would be deleted by make_keys.
      [[ $(key_count "svc-$svc") == 0 ]] ||
        die "rekey: the live key of svc-$svc could not be moved (HTTP $updated); nothing changed"
      log "rekey: svc-$svc has no live key to keep (HTTP $updated); minting a new one"
    fi
  fi
  unset master
  stop_forward
  make_keys "$1"
  restart_consumers "$ns" "chassis-$svc-litellm"
  if ((had_old == 0)); then
    unset old
    return 0
  fi
  start_forward
  master=$(read_secret "$PLATFORM" litellm-master LITELLM_MASTER_KEY)
  delete_alias "$retiring"
  unset master
  printf '%s' "$old" | require_status 401 "rekey: svc-$svc old key"
  unset old
  read_secret "$ns" "chassis-$svc-litellm" LITELLM_API_KEY | require_status 200 "rekey: svc-$svc new key"
  log "rekey: svc-$svc old key revoked ($retiring deleted), new key answers (HTTP 200)"
  stop_forward
}

rekey() {
  local entry
  while IFS= read -r entry; do
    [[ ${entry%%|*} == "$AGENTS" ]] || continue
    rekey_one "$entry"
  done < <(all_services)
}

# revoke_grants SVC: SVC's key keeps its models but loses every MCP tool grant. An empty grant
# refuses tools/list (it does not fall open), but LiteLLM serves the cached grant for about 60 s
# after the update (measured on poc05, 2026-11-16); `revoke` (a delete) takes effect at once.
revoke_grants() {
  local entry ns svc master updated
  entry=$(service_entry "${1:-}") || die "revoke-grants: no service '${1:-}'"
  IFS='|' read -r ns svc _ <<<"$entry"
  start_forward
  master=$(read_secret "$PLATFORM" litellm-master LITELLM_MASTER_KEY)
  updated=$(update_key "$ns" "$svc" '{key: ., object_permission: {mcp_servers: [], mcp_tool_permissions: {}}}')
  unset master
  [[ $updated =~ ^2[0-9][0-9]$ ]] || die "revoke-grants: LiteLLM /key/update for svc-$svc: HTTP $updated"
  stop_forward
  log "revoked every tool grant of svc-$svc"
}

# revoke SVC: delete SVC's key (and with it its grants) and its Secret; the held key must then get
# 401. `seed.sh keys` mints a new one; the pods keep the dead key until they restart. A service
# with no Secret and no key left is already revoked: exit 0.
revoke() {
  local entry ns svc master old
  entry=$(service_entry "${1:-}") || die "revoke: no service '${1:-}'"
  IFS='|' read -r ns svc _ <<<"$entry"
  start_forward
  master=$(read_secret "$PLATFORM" litellm-master LITELLM_MASTER_KEY)
  if ! secret_exists "$ns" "chassis-$svc-litellm"; then
    delete_alias "svc-$svc"
    unset master
    stop_forward
    log "svc-$svc is already revoked: no Secret, no key under svc-$svc"
    return 0
  fi
  old=$(read_secret "$ns" "chassis-$svc-litellm" LITELLM_API_KEY)
  delete_alias "svc-$svc"
  unset master
  printf '%s' "$old" | require_status 401 "revoke: svc-$svc deleted key"
  unset old
  stop_forward
  drop_secret "$ns" "chassis-$svc-litellm"
  log "revoked svc-$svc: key deleted (HTTP 401 on /v1/models), Secret $ns/chassis-$svc-litellm dropped"
}

# key_info: one JSON line per alias (each service's and its -retiring one): how many keys, and
# each key's created_at and expires. No key, no hash, no other field.
key_info() {
  local entry svc alias master
  start_forward
  master=$(read_secret "$PLATFORM" litellm-master LITELLM_MASTER_KEY)
  while IFS= read -r entry; do
    svc=$(cut -d'|' -f2 <<<"$entry")
    for alias in "svc-$svc" "svc-$svc-retiring"; do
      printf 'Authorization: Bearer %s\n' "$master" | curl -fsS -H @- --get --data-urlencode "key_alias=$alias" --data-urlencode return_full_object=true "$LITELLM/key/list" 2>/dev/null |
        jq -c --arg alias "$alias" '{alias: $alias, keys: [.keys[] | {created_at, expires}]}' ||
        die "key-info: LiteLLM /key/list failed for $alias"
    done
  done < <(all_services)
  unset master
  stop_forward
}

status() {
  local ns
  for ns in "$PLATFORM" "$AGENTS" "$REMOTE" "$CHART"; do
    printf '%s:\n' "$ns"
    kctl -n "$ns" get secrets -o name | sed 's#^secret/#  #'
  done
}

# --- Kafka (T25, H11; platform/kafka.yaml) ---------------------------------------------------
#
#   seed.sh kafka          kafka-sasl in $PLATFORM (ADMIN_PASSWORD, CHASSIS_PASSWORD: the broker
#                          formats its SCRAM users from them) and in $AGENTS (KAFKA_SASL_PASSWORD:
#                          agent-echo-events's chassis); before platform/kafka.yaml
#   seed.sh kafka-topics   the result topics, once Kafka is Ready (auto-create is off)
#
# The topics are made inside the broker's container with the admin client config its start.sh
# wrote to /tmp, so no credential crosses kubectl. The tool gets its own small heap: the broker's
# JVM already holds most of the container's memory limit.
KAFKA_TOPICS=(agents.task.completed.v1 agents.task.failed.v1)

seed_kafka() {
  local admin value
  if secret_exists "$PLATFORM" kafka-sasl && secret_exists "$AGENTS" kafka-sasl; then
    return 0
  fi
  admin=$(new_secret)
  value=$(new_secret)
  printf 'ADMIN_PASSWORD=%s\nCHASSIS_PASSWORD=%s\n' "$admin" "$value" |
    put_env_secret "$PLATFORM" kafka-sasl
  printf '%s' "$value" | put_secret "$AGENTS" kafka-sasl KAFKA_SASL_PASSWORD
  unset admin value
  log "made kafka-sasl in $PLATFORM (SCRAM users) and $AGENTS (the chassis password)"
}

seed_kafka_topics() {
  local topic
  kctl -n "$PLATFORM" rollout status deploy/kafka --timeout=180s >/dev/null ||
    die "kafka is not Ready; apply platform/kafka.yaml after: seed.sh kafka"
  for topic in "${KAFKA_TOPICS[@]}"; do
    kctl -n "$PLATFORM" exec deploy/kafka -c kafka -- env KAFKA_HEAP_OPTS=-Xmx64m \
      /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 \
      --command-config /tmp/admin-client.properties \
      --create --if-not-exists --topic "$topic" --partitions 3 --replication-factor 1 >/dev/null
    log "topic $topic is there"
  done
}

usage() { sed -n '2,19p' "$0"; }

(($# > 0)) || { usage; exit 0; }
case $1 in
  base) preflight; seed_base ;;
  keys) preflight; seed_keys ;;
  rotate) preflight; rotate "${2:-}" ;;
  rekey) preflight; rekey ;;
  revoke-grants) preflight; revoke_grants "${2:-}" ;;
  revoke) preflight; revoke "${2:-}" ;;
  key-info) preflight; key_info ;;
  kafka) preflight; seed_kafka ;;
  kafka-topics) preflight; seed_kafka_topics ;;
  status) status ;;
  *) usage; exit 2 ;;
esac
