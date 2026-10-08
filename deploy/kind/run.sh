#!/usr/bin/env bash
# PoC-4 on kind: the container-roles drills (deploy/README.md, section "kind (PoC-4)").
#
#   deploy/kind/run.sh up native-sidecar [python|typescript]   create, build, load, apply
#   deploy/kind/run.sh create | build | load | delete | status
#   deploy/kind/run.sh apply <native-sidecar|prestop> [python|typescript]
#   deploy/kind/run.sh drill-rolling [seconds]                 rolling restart under load
#   deploy/kind/run.sh drill-hung                              SIGSTOP one workload
#
# `make kind-poc04 ARGS="up native-sidecar"` calls this script. It touches only the kind cluster
# `poc04`: every kubectl call goes through `kctl`, which pins `--context kind-poc04`, and every
# kind call passes `--name poc04`. It never uses the current context.
set -euo pipefail

CLUSTER=poc04
CONTEXT=kind-$CLUSTER
NS=poc04
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
URL=${POC04_KIND_URL:-http://127.0.0.1:18081}
IMAGES=(
  agent-platform/chassis:poc04
  agent-platform/fake-model-server:poc04
  agent-platform/echo-python:poc04
  agent-platform/echo-typescript:poc04
)

kctl() { kubectl --context "$CONTEXT" "$@"; }
log() { printf '[kind-poc04] %s\n' "$*" >&2; }
die() { log "$*"; exit 1; }

overlay() {
  local variant=$1 engine=${2:-python}
  case $variant in native-sidecar | prestop) ;; *) die "variant: native-sidecar or prestop" ;; esac
  case $engine in
    python) echo "$HERE/poc04/$variant" ;;
    typescript) echo "$HERE/poc04/typescript/$variant" ;;
    *) die "engine: python or typescript" ;;
  esac
}

create() {
  if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
    log "cluster $CLUSTER exists"
  else
    kind create cluster --config "$HERE/cluster.yaml" --name "$CLUSTER" --wait 120s
  fi
}

build() {
  docker build -t agent-platform/chassis:poc04 -f "$ROOT/packages/chassis/Dockerfile" "$ROOT"
  docker build -t agent-platform/fake-model-server:poc04 \
    -f "$ROOT/packages/fake-model-server/Dockerfile" "$ROOT"
  docker build -t agent-platform/echo-python:poc04 \
    -f "$ROOT/packages/workloads/echo-python/Dockerfile" "$ROOT"
  docker build -t agent-platform/echo-typescript:poc04 "$ROOT/packages/workloads/echo-typescript"
}

load() {
  local image
  for image in "${IMAGES[@]}"; do kind load docker-image "$image" --name "$CLUSTER"; done
}

secret() {
  # The namespace first (the Secret lives in it), then VALKEY_PASSWORD, generated once per
  # cluster. The value goes through a pipe, never argv, a file, or the log.
  kctl create namespace "$NS" --dry-run=client -o yaml | kctl apply -f - >/dev/null
  if ! kctl -n "$NS" get secret poc04-secrets >/dev/null 2>&1; then
    printf '%s' "$(openssl rand -hex 24)" |
      kctl -n "$NS" create secret generic poc04-secrets --from-file=VALKEY_PASSWORD=/dev/stdin
  fi
}

apply() {
  local variant=${1:?apply needs a variant} engine=${2:-python} path current
  path=$(overlay "$variant" "$engine")
  secret
  # Both variants own Deployment/agent; the workload moves between initContainers and
  # containers, so a switch replaces the Deployment instead of merging into it.
  current=$(kctl -n "$NS" get deployment agent -o jsonpath='{.metadata.labels.poc04/variant}' \
    2>/dev/null || true)
  if [[ -n $current && $current != "$variant" ]]; then
    log "switching $current -> $variant: deleting deployment/agent"
    kctl -n "$NS" delete deployment agent --wait=true
  fi
  kctl apply -k "$path"
  local d
  for d in fake-model-server valkey agent; do
    kctl -n "$NS" rollout status "deployment/$d" --timeout=180s
  done
}

workload_field() {
  # One field of the workload container's status in pod $1: initContainerStatuses in the native
  # sidecar variant, containerStatuses in the preStop one. Exactly one of the two is non-empty.
  local pod=$1 field=$2
  kctl -n "$NS" get pod "$pod" -o jsonpath="{.status.initContainerStatuses[?(@.name==\"workload\")].$field}{.status.containerStatuses[?(@.name==\"workload\")].$field}"
}

pod_ready() {
  kctl -n "$NS" get pod "$1" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}'
}

drill_rolling() {
  # Drill 1: `rollout restart` while 20 clients send complete calls with no retry.
  # Pass: the client reports 0 failed requests (its exit code).
  local seconds=${1:-90} client="$ROOT/pocs/poc-04-stateless-scalable/load/steady_client.py"
  [[ -f $client ]] || die "missing $client (a later package adds it)"
  # Every call stores an idempotency entry, and Valkey runs `--maxmemory 256mb` with
  # `noeviction`: about 186k calls fill it, then every call is a 503 (2026-10-01, notes
  # 2026-10-01-container-roles.md). Start each drill with an empty store. The password stays
  # inside the Valkey container's environment.
  kctl -n "$NS" exec deployment/valkey -- sh -c 'REDISCLI_AUTH="$VALKEY_PASSWORD" valkey-cli flushall' >/dev/null
  (cd "$ROOT" && uv run python "$client" --url "$URL" --concurrency 20 --duration-s "$seconds" \
    --no-retry) &
  local pid=$!
  sleep 10
  kctl -n "$NS" rollout restart deployment/agent
  kctl -n "$NS" rollout status deployment/agent --timeout=300s
  wait "$pid"
}

drill_hung() {
  # Drill 2: SIGSTOP the workload in one pod from the node (a namespace's PID 1 ignores SIGSTOP
  # sent from inside). Pass: /ready false within 10 s, the workload restarts within 60 s, and the
  # pod is Ready again.
  local pod cid restarts start
  # A settled pod: not one a rollout is still terminating.
  kctl -n "$NS" rollout status deployment/agent --timeout=300s >/dev/null
  pod=$(kctl -n "$NS" get pod -l app.kubernetes.io/name=agent --field-selector=status.phase=Running \
    -o go-template='{{range .items}}{{if not .metadata.deletionTimestamp}}{{.metadata.name}}{{"\n"}}{{end}}{{end}}' |
    head -n 1)
  [[ -n $pod ]] || die "no running agent pod"
  cid=$(workload_field "$pod" containerID)
  cid=${cid#containerd://}
  restarts=$(workload_field "$pod" restartCount)
  [[ -n $cid ]] || die "no workload container in $pod"
  log "SIGSTOP workload $cid in $pod (restarts so far: $restarts)"
  docker exec "$CLUSTER-control-plane" sh -c \
    "kill -STOP \$(crictl inspect --output go-template --template '{{.info.pid}}' $cid)"
  start=$SECONDS
  until [[ $(pod_ready "$pod") == False ]]; do
    ((SECONDS - start <= 10)) || die "FAIL: $pod still Ready after 10 s"
    sleep 0.5
  done
  log "not Ready after $((SECONDS - start)) s"
  until [[ $(workload_field "$pod" restartCount) -gt $restarts ]]; do
    ((SECONDS - start <= 60)) || die "FAIL: the workload did not restart within 60 s"
    sleep 1
  done
  log "workload restarted after $((SECONDS - start)) s"
  until [[ $(pod_ready "$pod") == True ]]; do
    ((SECONDS - start <= 120)) || die "FAIL: $pod not Ready again within 120 s"
    sleep 1
  done
  log "PASS: Ready again after $((SECONDS - start)) s"
}

status() {
  kctl -n "$NS" get deployment,pod,service -o wide
}

cmd=${1:-help}
shift || true
case $cmd in
  create) create ;;
  build) build ;;
  load) load ;;
  secret) secret ;;
  apply) apply "$@" ;;
  up) create && build && load && apply "$@" ;;
  status) status ;;
  drill-rolling) drill_rolling "$@" ;;
  drill-hung) drill_hung ;;
  delete) kind delete cluster --name "$CLUSTER" ;;
  *) sed -n '2,12p' "$0" ;;
esac
