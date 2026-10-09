#!/usr/bin/env bash
# PoC-6 on kind, hosted model: the untrusted engines (echo-smolagents, echo-claude-agent, kagent-adk)
# run the bake-off tasks on the hosted model `big-default`, in the remote lane, under gVisor. It runs
# on a cluster that `deploy/kind/poc06/run.sh up` has brought up. It is part 2 of the Mac command
# (scripts/poc06_mac.sh, step `kind`), and it closes PoC-6b exit criterion 5 for these engines.
#
#   printf '%s' "$KEY" | hosted.sh up     the provider key arrives on stdin. Makes the Secret
#                                         `litellm-provider`, a PoC-6 copy of PoC-5's LiteLLM
#                                         ConfigMap with one added route (`big-default`), the egress
#                                         NetworkPolicy for LiteLLM, and the env on the LiteLLM
#                                         Deployment; scopes the three chassis keys to two routes with
#                                         a budget; switches the three chassis (and kagent-adk's
#                                         workload) to `big-default`; waits until they serve it.
#   hosted.sh run OUTDIR                  the bake-off tasks (smoke, simplifier, lookup) against the
#                                         three chassis, POC06_REPEAT times (default 5); results.json
#                                         and results.md in OUTDIR, scrubbed of anything key-like.
#   hosted.sh down                        undoes `up`; idempotent; safe on a missing cluster.
#
# What it does not touch: deploy/kind/poc05/ and deploy/kind/poc06/run.sh. It patches live objects.
# Every kubectl call goes through `kctl`, pinned to the context kind-poc05.
#
# Rules (checked by pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_hosted_static.py):
# - The provider key is read from stdin into a shell variable, never argv, never an exported
#   variable, never a file, never printed. It goes only into the Secret, through a pipe
#   (`printf ... | kubectl create secret ... =/dev/stdin ... | kubectl apply`). Only the LiteLLM pod
#   reads that Secret, as the env POC06_PROVIDER_KEY.
# - The LiteLLM master key and a chassis key go to curl in a config on stdin (`curl -K -`), made by
#   a printf into the pipe. Neither is ever an argument of any process. No `set -x`.
# - LiteLLM is the only pod with internet egress, on 443 only (hosted/network-policy.yaml).
# - A chassis key lists two routes (fake-chat and big-default) and a budget; a workload never holds
#   a provider key, or any LiteLLM key.
#
# Findings that shape this script (2026-10-09, read from the code and the manifests):
# - Chassis config reload. The three chassis use `config: memory`, so the mounted ConfigMap is read
#   once at start. `spec.model.route` is in RELOADABLE, but only InMemoryConfig.put can reload it, and
#   nothing calls that. A changed ConfigMap needs a `rollout restart`; that is what this script does.
# - Who picks the route. The chassis model proxy uses the `model` field of the request body as the
#   route (chassis/server/model_proxy.py, `route=body.model`); `spec.model.route` is only what it
#   tells the workload (ctx model_route) and stamps on the envelope. echo-smolagents and
#   echo-claude-agent send the route they are given, so the chassis config switches them. kagent-adk
#   does not: its ConfigMap `remote-kagent-adk-config` (config.json) names the model `fake-chat`, and
#   the proxy would pass that on. So `up` also edits that ConfigMap to `big-default` and restarts the
#   kagent-adk pod. The chassis does not pin the route: the workload can name any route its key lists.
#   That is why every chassis key is limited to two routes and a budget.
# - The chassis public port binds the pod IP only, so `kubectl port-forward` cannot reach it. `run`
#   uses hosted/relay.py (kubectl exec into the chassis container, as the kind tests do).
set -euo pipefail

CONTEXT=kind-poc05
PLATFORM=poc05-platform
AGENTS=poc05-agents
REMOTE=poc05-remote
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
HOSTED_DIR=$HERE/hosted

ROUTE=big-default
BASE_ROUTE=fake-chat
HOSTED_MODEL=${POC06_HOSTED_MODEL:-openai/gpt-4o-mini}
REPEAT=${POC06_REPEAT:-5}
# suggested: 1.0 USD per chassis key, the budget the PoC-5 seed gives each key.
KEY_BUDGET=1.0
PROVIDER_SECRET=litellm-provider
PROVIDER_ENV=POC06_PROVIDER_KEY
HOSTED_CM=litellm-config-poc06-hosted
ORIGINAL_ANNOTATION=poc06-hosted/original-config
# The three untrusted engines: "<bakeoff engine>:<chassis service>". The chassis Deployment is
# chassis-<service>; its key Secret is chassis-<service>-litellm.
ENGINES=(
  echo-smolagents:smolagents-remote
  echo-claude-agent:claude-agent-remote
  kagent-adk:kagent-adk-remote
)
KAGENT_CM=remote-kagent-adk-config
KAGENT_SELECTOR=app.kubernetes.io/name=remote-kagent-adk

kctl() { kubectl --context "$CONTEXT" "$@"; }
log() { printf '[hosted-poc06] %s\n' "$*" >&2; }
die() { log "$*"; exit 1; }

[[ $HOSTED_MODEL =~ ^[A-Za-z0-9._/:-]+$ ]] || die "POC06_HOSTED_MODEL has characters outside [A-Za-z0-9._/:-]"
[[ $REPEAT =~ ^[0-9]+$ ]] || die "POC06_REPEAT must be a number"

# Anything key-like out of text on stdin.
scrub() {
  sed -E \
    -e 's/(sk-|Bearer )[A-Za-z0-9._-]{8,}/\1[redacted]/g' \
    -e 's/sk-[A-Za-z0-9._-]*\*+[A-Za-z0-9]*/sk-[redacted]/g' \
    -e 's#/(Users|home)/[^/ ]+#~#g'
}

secret_exists() { kctl -n "$1" get secret "$2" >/dev/null 2>&1; }

# put_secret NS NAME KEY: one key, its value on stdin.
put_secret() {
  kctl -n "$1" create secret generic "$2" --from-file="$3"=/dev/stdin --dry-run=client -o yaml |
    kctl apply --server-side --force-conflicts --field-manager=poc06-hosted -f - >/dev/null
}

# read_secret NS NAME KEY: the decoded value on stdout, for a command substitution only.
read_secret() { kctl -n "$1" get secret "$2" -o "jsonpath={.data.$3}" | base64 -d; }

deploy_path() { kctl -n "$1" get deploy "$2" -o "jsonpath=$3" 2>/dev/null || true; }

explain() {
  local ns=$1 name=$2
  log "not ready: $ns/$name; events and logs follow"
  kctl -n "$ns" get pods -l "app.kubernetes.io/name=$name" -o wide || true
  kctl -n "$ns" get events --sort-by=.lastTimestamp | tail -n 15 || true
  kctl -n "$ns" logs -l "app.kubernetes.io/name=$name" --all-containers --tail=30 2>&1 | scrub || true
}

wait_rollout() {  # NS NAME
  kctl -n "$1" rollout status "deployment/$2" --timeout=300s >&2 ||
    { explain "$1" "$2"; die "deployment $1/$2 not ready"; }
}

preflight() {
  local tool pair svc
  for tool in kubectl jq curl base64 sed; do
    command -v "$tool" >/dev/null || die "missing $tool"
  done
  kctl get namespace "$PLATFORM" "$AGENTS" "$REMOTE" >/dev/null ||
    die "context $CONTEXT has no PoC-5 namespaces; run: deploy/kind/poc06/run.sh up"
  secret_exists "$PLATFORM" litellm-master || die "no $PLATFORM/litellm-master; run: deploy/kind/poc06/run.sh up"
  kctl -n "$PLATFORM" get deployment litellm >/dev/null || die "no litellm Deployment in $PLATFORM"
  for pair in "${ENGINES[@]}"; do
    svc=${pair#*:}
    secret_exists "$AGENTS" "chassis-$svc-litellm" ||
      die "no $AGENTS/chassis-$svc-litellm; run: deploy/kind/poc06/run.sh up"
    kctl -n "$AGENTS" get deployment "chassis-$svc" >/dev/null || die "no chassis-$svc Deployment"
  done
}

# ---- LiteLLM admin API ------------------------------------------------------------------------

PF_PID=""
PF_LOG=""
LITELLM=""

stop_forward() {
  if [[ -n $PF_PID ]]; then kill "$PF_PID" 2>/dev/null || true; fi
  if [[ -n $PF_LOG ]]; then rm -f "$PF_LOG"; fi
  PF_PID=""
  PF_LOG=""
}

start_forward() {
  local port=""
  PF_LOG=$(mktemp "${TMPDIR:-/tmp}/hosted-pf-XXXXXX")
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

# set_key_models SVC ROUTE...: /key/update on the chassis key of SVC, models = the routes, the
# budget = KEY_BUDGET. The master key and the chassis key go to curl in a config on stdin (`-K -`),
# made by one printf into the pipe. Neither is an argument of any process. The response holds the
# key, so it is checked with jq and never printed. The key and the master key are checked to be
# plain token characters first, so they cannot break the config's quoting.
set_key_models() {
  local svc=$1 master key resp routes="" r
  shift
  for r in "$@"; do routes="$routes${routes:+,}\\\"$r\\\""; done
  master=$(read_secret "$PLATFORM" litellm-master LITELLM_MASTER_KEY)
  key=$(read_secret "$AGENTS" "chassis-$svc-litellm" LITELLM_API_KEY)
  [[ $master =~ ^[A-Za-z0-9_-]+$ ]] || die "$PLATFORM/litellm-master is empty or not a plain token"
  [[ $key =~ ^[A-Za-z0-9_-]+$ ]] || die "$AGENTS/chassis-$svc-litellm is empty or not a plain token"
  resp=$(printf 'header = "Authorization: Bearer %s"\nheader = "Content-Type: application/json"\ndata-binary = "{\\"key\\":\\"%s\\",\\"models\\":[%s],\\"max_budget\\":%s}"\n' \
    "$master" "$key" "$routes" "$KEY_BUDGET" | curl -sS -K - "$LITELLM/key/update") || { unset master key resp; die "curl to LiteLLM failed for $svc"; }
  unset master key
  if ! printf '%s' "$resp" | jq -e '(.error // .detail // null) == null' >/dev/null 2>&1; then
    printf '%s' "$resp" | jq -r '.error.message // .detail // "unreadable response"' 2>/dev/null | scrub >&2 || true
    unset resp
    die "LiteLLM refused /key/update for $svc"
  fi
  for r in "$@"; do
    if ! printf '%s' "$resp" | jq -e --arg r "$r" 'if .models then (.models | index($r)) != null else true end' >/dev/null 2>&1; then
      unset resp
      die "LiteLLM's answer for $svc does not list the route $r"
    fi
  done
  unset resp
  log "chassis-$svc-litellm: routes $*, max_budget $KEY_BUDGET"
}

set_all_keys() {  # ROUTE...
  local pair
  start_forward
  for pair in "${ENGINES[@]}"; do
    set_key_models "${pair#*:}" "$@"
  done
  stop_forward
}

# ---- chassis route ----------------------------------------------------------------------------

chassis_cm() {  # SVC: the name of the ConfigMap the chassis Deployment mounts
  deploy_path "$AGENTS" "chassis-$1" '{.spec.template.spec.volumes[?(@.name=="chassis-config")].configMap.name}'
}

# set_chassis_route SVC ROUTE: edit the one `route:` line in the mounted ConfigMap and restart the
# Deployment (config: memory is read at start). No change, no restart. Prints "changed" or "same".
set_chassis_route() {
  local svc=$1 route=$2 cm cfg new
  cm=$(chassis_cm "$svc")
  [[ -n $cm ]] || die "chassis-$svc mounts no chassis-config volume"
  cfg=$(kctl -n "$AGENTS" get configmap "$cm" -o 'jsonpath={.data.config\.yaml}')
  [[ $(printf '%s\n' "$cfg" | grep -c '^    route: ') == 1 ]] || die "$cm: expected exactly one 4-space 'route:' line"
  new=$(printf '%s\n' "$cfg" | sed -E "s/^(    route: ).*/\\1$route/")
  if [[ $new == "$cfg" ]]; then
    printf 'same\n'
    return 0
  fi
  kctl -n "$AGENTS" patch configmap "$cm" --type merge \
    -p "$(jq -nc --arg c "$new"$'\n' '{data: {"config.yaml": $c}}')" >/dev/null
  kctl -n "$AGENTS" rollout restart "deployment/chassis-$svc" >/dev/null
  printf 'changed\n'
}

# set_kagent_model MODEL: kagent-adk picks its own model name (config.json). Edit it, and replace
# the pod so it reads the file again. Prints "changed" or "same".
set_kagent_model() {
  local model=$1 cfg new old_uid uid i
  cfg=$(kctl -n "$REMOTE" get configmap "$KAGENT_CM" -o 'jsonpath={.data.config\.json}')
  new=$(printf '%s' "$cfg" | jq -c --arg m "$model" '.model.model = $m')
  if [[ $new == "$cfg" ]]; then
    printf 'same\n'
    return 0
  fi
  old_uid=$(kctl -n "$REMOTE" get pod -l "$KAGENT_SELECTOR" -o 'jsonpath={.items[0].metadata.uid}' 2>/dev/null || true)
  kctl -n "$REMOTE" patch configmap "$KAGENT_CM" --type merge \
    -p "$(jq -nc --arg c "$new" '{data: {"config.json": $c}}')" >/dev/null
  kctl -n "$REMOTE" delete pod -l "$KAGENT_SELECTOR" --wait=true >/dev/null
  # The Sandbox controller makes a new pod. Wait for a new uid, then for Ready.
  for i in $(seq 1 90); do
    uid=$(kctl -n "$REMOTE" get pod -l "$KAGENT_SELECTOR" -o 'jsonpath={.items[0].metadata.uid}' 2>/dev/null || true)
    [[ -n $uid && $uid != "$old_uid" ]] && break
    sleep 2
    [[ $i -lt 90 ]] || die "the kagent-adk pod did not come back in 180 s"
  done
  kctl -n "$REMOTE" wait --for=condition=Ready pod -l "$KAGENT_SELECTOR" --timeout=240s >/dev/null ||
    { explain "$REMOTE" remote-kagent-adk; die "kagent-adk pod not ready"; }
  printf 'changed\n'
}

# serves_route SVC ROUTE: the running chassis container has the route in its mounted config.
serves_route() {
  kctl -n "$AGENTS" exec "deploy/chassis-$1" -c chassis -- python -c \
    'print(open("/etc/chassis/config.yaml").read())' 2>/dev/null | grep -q "^    route: $2\$"
}

# ---- LiteLLM Deployment -----------------------------------------------------------------------

litellm_config_cm() {
  deploy_path "$PLATFORM" litellm '{.spec.template.spec.volumes[?(@.name=="config")].configMap.name}'
}

provider_env_present() {
  [[ -n $(deploy_path "$PLATFORM" litellm "{.spec.template.spec.containers[0].env[?(@.name==\"$PROVIDER_ENV\")].name}") ]]
}

apply_hosted_config() {
  sed "s|__POC06_HOSTED_MODEL__|$HOSTED_MODEL|" "$HOSTED_DIR/litellm-config.yaml" |
    kctl -n "$PLATFORM" create configmap "$HOSTED_CM" --from-file=config.yaml=/dev/stdin --dry-run=client -o yaml |
    kctl apply -f - >/dev/null
}

patch_litellm() {  # CONFIGMAP: point the config volume at it, and add the provider env
  local cm=$1
  kctl -n "$PLATFORM" patch deployment litellm -p "$(cat <<EOF
{"spec": {"template": {"spec": {
  "containers": [{"name": "litellm", "env": [
    {"name": "$PROVIDER_ENV", "valueFrom": {"secretKeyRef": {"name": "$PROVIDER_SECRET", "key": "api_key"}}}]}],
  "volumes": [{"name": "config", "configMap": {"name": "$cm"}}]}}}}
EOF
)" >/dev/null
}

unpatch_litellm() {  # ORIGINAL_CONFIGMAP: drop the provider env, point the config volume back
  local cm=$1
  kctl -n "$PLATFORM" patch deployment litellm -p "$(cat <<EOF
{"spec": {"template": {"spec": {
  "containers": [{"name": "litellm", "env": [{"name": "$PROVIDER_ENV", "\$patch": "delete"}]}],
  "volumes": [{"name": "config", "configMap": {"name": "$cm"}}]}}}}
EOF
)" >/dev/null
}

original_config() {
  local orig
  orig=$(kctl -n "$PLATFORM" get deployment litellm -o "jsonpath={.metadata.annotations.poc06-hosted/original-config}" 2>/dev/null || true)
  if [[ -z $orig ]]; then
    orig=$(kctl -n "$PLATFORM" get configmap -o name 2>/dev/null | sed 's#^configmap/##' |
      grep -E '^litellm-config-[a-z0-9]+$' | head -n 1 || true)
  fi
  printf '%s' "$orig"
}

# ---- verbs ------------------------------------------------------------------------------------

up() {
  local key current pair svc state already=0
  [[ ! -t 0 ]] || die "up: the provider key goes on stdin (printf '%s' \"\$KEY\" | $0 up)"
  key=$(cat)
  [[ -n $key ]] || die "up: no provider key on stdin"
  [[ $key =~ ^[[:graph:]]+$ ]] || { unset key; die "up: the key on stdin has spaces or control characters"; }
  preflight

  log "1/6 Secret $PLATFORM/$PROVIDER_SECRET (from stdin; value not shown)"
  printf '%s' "$key" | put_secret "$PLATFORM" "$PROVIDER_SECRET" api_key
  unset key

  log "2/6 ConfigMap $HOSTED_CM: PoC-5's config plus the route $ROUTE ($HOSTED_MODEL)"
  apply_hosted_config

  log "3/6 NetworkPolicy litellm-hosted-egress: LiteLLM only, TCP 443, public addresses"
  kctl apply -f "$HOSTED_DIR/network-policy.yaml" >/dev/null

  log "4/6 LiteLLM Deployment: env $PROVIDER_ENV from the Secret, config from $HOSTED_CM"
  current=$(litellm_config_cm)
  [[ -n $current ]] || die "the litellm Deployment has no config volume"
  if [[ $current == "$HOSTED_CM" ]] && provider_env_present; then already=1; fi
  if [[ $current != "$HOSTED_CM" ]]; then
    kctl -n "$PLATFORM" annotate deployment litellm "$ORIGINAL_ANNOTATION=$current" --overwrite >/dev/null
  fi
  patch_litellm "$HOSTED_CM"
  if [[ $already == 1 ]]; then
    # A re-run changes no pod template, so restart to read the new key and model.
    kctl -n "$PLATFORM" rollout restart deployment/litellm >/dev/null
  fi
  wait_rollout "$PLATFORM" litellm

  log "5/6 chassis keys: routes $BASE_ROUTE and $ROUTE, max_budget $KEY_BUDGET"
  set_all_keys "$BASE_ROUTE" "$ROUTE"

  log "6/6 chassis and workloads on $ROUTE"
  for pair in "${ENGINES[@]}"; do
    svc=${pair#*:}
    state=$(set_chassis_route "$svc" "$ROUTE")
    log "chassis-$svc: $state"
  done
  state=$(set_kagent_model "$ROUTE")
  log "kagent-adk workload model: $state"
  for pair in "${ENGINES[@]}"; do
    svc=${pair#*:}
    wait_rollout "$AGENTS" "chassis-$svc"
    serves_route "$svc" "$ROUTE" || die "chassis-$svc is ready but does not serve route $ROUTE"
  done
  log "up done: the three untrusted chassis serve $ROUTE"
}

RELAY_PIDS=""
RELAY_LOGS=""

stop_relays() {
  local pid f
  for pid in $RELAY_PIDS; do kill "$pid" 2>/dev/null || true; done
  for f in $RELAY_LOGS; do rm -f "$f"; done
  RELAY_PIDS=""
  RELAY_LOGS=""
}

# start_relay SVC: prints the local port.
start_relay() {
  local svc=$1 rlog port="" pid i
  rlog=$(mktemp "${TMPDIR:-/tmp}/hosted-relay-XXXXXX")
  (cd "$ROOT" && exec uv run python -I "$HOSTED_DIR/relay.py" "chassis-$svc") >"$rlog" 2>&1 &
  pid=$!
  RELAY_PIDS="$RELAY_PIDS $pid"
  RELAY_LOGS="$RELAY_LOGS $rlog"
  for i in $(seq 1 100); do
    port=$(sed -n 's/^port \([0-9][0-9]*\)$/\1/p' "$rlog" | head -n 1)
    [[ -z $port ]] || break
    kill -0 "$pid" 2>/dev/null || die "relay for chassis-$svc stopped: $(scrub <"$rlog")"
    sleep 0.3
    [[ $i -lt 100 ]] || die "relay for chassis-$svc did not start in 30 s"
  done
  printf '%s' "$port"
}

# scrub_file FILE: key-like text out of a file, in place.
scrub_file() {
  [[ -f $1 ]] || return 0
  scrub <"$1" >"$1.scrubbed" && mv "$1.scrubbed" "$1"
}

run() {
  local out=${1:-} pair svc engine port rc=0 targets
  targets=()
  [[ -n $out ]] || die "run: usage: $0 run OUTDIR"
  preflight
  for pair in "${ENGINES[@]}"; do
    svc=${pair#*:}
    serves_route "$svc" "$ROUTE" || die "chassis-$svc does not serve $ROUTE; run: $0 up"
  done
  command -v uv >/dev/null || die "missing uv"
  mkdir -p "$out"
  trap stop_relays EXIT
  for pair in "${ENGINES[@]}"; do
    engine=${pair%%:*}
    svc=${pair#*:}
    port=$(start_relay "$svc")
    targets+=("$engine=http://127.0.0.1:$port")
    log "relay chassis-$svc on 127.0.0.1:$port"
  done
  # One run over the three targets: one results file. A target that fails is marked in its cell;
  # the others still run.
  (cd "$ROOT" && uv run python -m bakeoff run --target "${targets[@]}" \
    --tasks smoke,simplifier,lookup --repeat "$REPEAT" --out "$out") 2>&1 | scrub || rc=${PIPESTATUS[0]}
  stop_relays
  trap - EXIT
  scrub_file "$out/results.json"
  scrub_file "$out/results.md"
  return "$rc"
}

down() {
  local pair svc orig k
  if ! kctl get namespace "$PLATFORM" >/dev/null 2>&1; then
    log "down: no cluster or no $PLATFORM in $CONTEXT; nothing to undo"
    return 0
  fi
  log "1/5 chassis and workloads back on $BASE_ROUTE"
  if kctl -n "$AGENTS" get deployment >/dev/null 2>&1; then
    for pair in "${ENGINES[@]}"; do
      svc=${pair#*:}
      if kctl -n "$AGENTS" get deployment "chassis-$svc" >/dev/null 2>&1; then
        k=$(set_chassis_route "$svc" "$BASE_ROUTE" || echo failed)
        log "chassis-$svc: $k"
      fi
    done
    if kctl -n "$REMOTE" get configmap "$KAGENT_CM" >/dev/null 2>&1; then
      k=$(set_kagent_model "$BASE_ROUTE" || echo failed)
      log "kagent-adk workload model: $k"
    fi
  fi
  log "2/5 NetworkPolicy litellm-hosted-egress"
  kctl -n "$PLATFORM" delete networkpolicy litellm-hosted-egress --ignore-not-found >/dev/null
  log "3/5 LiteLLM Deployment: drop $PROVIDER_ENV, config back to PoC-5's ConfigMap"
  orig=$(original_config)
  if [[ -n $orig ]] && { provider_env_present || [[ $(litellm_config_cm) == "$HOSTED_CM" ]]; }; then
    unpatch_litellm "$orig"
    kctl -n "$PLATFORM" annotate deployment litellm "$ORIGINAL_ANNOTATION-" >/dev/null 2>&1 || true
    ( wait_rollout "$PLATFORM" litellm ) || log "warning: litellm did not settle"
  fi
  log "4/5 chassis keys back to $BASE_ROUTE"
  if secret_exists "$PLATFORM" litellm-master; then
    ( trap stop_forward EXIT; set_all_keys "$BASE_ROUTE" ) ||
      log "warning: could not reset the chassis keys (the cluster is usually deleted next)"
  fi
  log "5/5 ConfigMap $HOSTED_CM and Secret $PROVIDER_SECRET"
  kctl -n "$PLATFORM" delete configmap "$HOSTED_CM" --ignore-not-found >/dev/null
  kctl -n "$PLATFORM" delete secret "$PROVIDER_SECRET" --ignore-not-found >/dev/null
  log "down done"
}

usage() { sed -n '2,19p' "$0"; }

trap stop_forward EXIT

(($# > 0)) || { usage; exit 0; }
case $1 in
  up) up ;;
  run) run "${2:-}" ;;
  down) down ;;
  *) usage; exit 2 ;;
esac
