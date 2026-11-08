#!/usr/bin/env bash
# The library chart on kind: the drain-order drill and the H14 check of ADR-012 (items 4, 5, 7).
#
#   deploy/kind/chart/run.sh up                      create, build, load
#   deploy/kind/chart/run.sh create | build | load | delete | status
#   deploy/kind/chart/run.sh install [<git ref>|<dir>]   example-agent, 3 replicas, from that chart
#   deploy/kind/chart/run.sh drill-rolling [seconds]  rolling restart under load (default 90 s)
#   deploy/kind/chart/run.sh roles                    which container is the native sidecar
#
# `make kind-chart ARGS="up"` calls this script. It touches only the kind cluster `agent-chart`:
# every kubectl call goes through `kctl` and every helm call through `khelm`, both pinned to
# `kind-agent-chart`, and every kind call passes `--name agent-chart`. Like PoC-4's run.sh,
# `create` leaves kind's switch of the current context in place; nothing here reads it.
#
# `build` builds the images from this checkout, or from BUILD_ROOT (another worktree's root, for
# a chassis change that is not here yet).
#
# `install` takes the chart from a git ref (default HEAD) or from a directory that holds
# agent-lib/ and example-agent/ (another worktree's deploy/helm). It copies the charts to a scratch
# directory first, so `helm dependency update` writes no charts/ folder into any checkout.
#
# The H14 check is a pytest file, run against this cluster:
#   KIND_DRILLS=1 uv run pytest -m network \
#     pocs/poc-09-agent-mvp-template/tests/test_poc09_kind_h14.py
set -euo pipefail

CLUSTER=agent-chart
CONTEXT=kind-$CLUSTER
LOG_PREFIX=kind-agent-chart
NS=agents
RELEASE=example-agent
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
BUILD_ROOT=${BUILD_ROOT:-$ROOT}
URL=${CHART_KIND_URL:-http://127.0.0.1:18082}
CHASSIS_IMAGE=kind.local/agent-platform/chassis:poc09
ECHO_IMAGE=kind.local/agent-platform/echo-python:poc09
H14_IMAGE=kind.local/agent-platform/h14-squat:poc09
IMAGES=("$CHASSIS_IMAGE" "$ECHO_IMAGE" "$H14_IMAGE")

# shellcheck source=deploy/kind/lib.sh
source "$HERE/../lib.sh"

khelm() { helm --kube-context "$CONTEXT" "$@"; }

create() {
  if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
    log "cluster $CLUSTER exists"
    return
  fi
  kind create cluster --config "$HERE/cluster.yaml" --name "$CLUSTER" --wait 120s
  log "kind set the current context to $CONTEXT; this script never uses the current context"
}

build() {
  log "building from $BUILD_ROOT ($(git -C "$BUILD_ROOT" rev-parse --short HEAD))"
  docker build -t "$CHASSIS_IMAGE" -f "$BUILD_ROOT/packages/chassis/Dockerfile" "$BUILD_ROOT"
  docker build -t "$ECHO_IMAGE" -f "$BUILD_ROOT/packages/workloads/echo-python/Dockerfile" \
    "$BUILD_ROOT"
  docker build -t "$H14_IMAGE" --build-arg "ECHO_IMAGE=$ECHO_IMAGE" "$HERE/h14"
}

load() {
  local image
  for image in "${IMAGES[@]}"; do kind load docker-image "$image" --name "$CLUSTER"; done
}

charts_from() {
  # Copy deploy/helm from a git ref or a directory into $1.
  local out=$1 source=$2
  if [[ -d $source ]]; then
    [[ -d $source/agent-lib && -d $source/example-agent ]] || die "$source: no agent-lib/ or example-agent/"
    cp -R "$source/agent-lib" "$source/example-agent" "$out/"
  else
    git -C "$ROOT" archive --end-of-options "$source" deploy/helm | tar -x -C "$out" --strip-components 2
  fi
  rm -rf "$out/example-agent/charts"
  helm dependency update --skip-refresh "$out/example-agent" >/dev/null
}

install() {
  local source=${1:-HEAD} work
  work=$(mktemp -d "${TMPDIR:-/tmp}/kind-chart-helm.XXXXXX")
  # shellcheck disable=SC2064 # expand now: $work is local
  trap "rm -rf '$work'" EXIT
  charts_from "$work" "$source"
  log "installing $RELEASE from $source"
  kctl create namespace "$NS" --dry-run=client -o yaml | kctl apply -f - >/dev/null
  # A swap of the container roles moves the workload between initContainers and containers; a
  # fresh install keeps the two runs apart.
  if khelm -n "$NS" status "$RELEASE" >/dev/null 2>&1; then
    khelm -n "$NS" uninstall "$RELEASE" --wait >/dev/null
  fi
  khelm -n "$NS" install "$RELEASE" "$work/example-agent" \
    -f "$work/example-agent/values-kind.yaml" -f "$HERE/values-drill.yaml" --wait --timeout 300s
  kctl apply -f "$HERE/nodeport.yaml" >/dev/null
  roles
}

roles() {
  # One line per pod: the init containers and the main containers, by name.
  kctl -n "$NS" get pod -l "app.kubernetes.io/name=$RELEASE" -o go-template='{{range .items}}{{.metadata.name}} init=[{{range .spec.initContainers}}{{.name}}{{if .restartPolicy}}({{.restartPolicy}}){{end}} {{end}}] main=[{{range .spec.containers}}{{.name}} {{end}}]{{"\n"}}{{end}}'
}

drill_rolling() {
  # `rollout restart` while 20 clients send complete calls with no retry (PoC-4's drill 1).
  # Pass: the client reports 0 failed requests (its exit code). The fake profile keeps state in
  # memory, so there is no store to flush between runs.
  local seconds=${1:-90} pid
  kctl -n "$NS" rollout status "deployment/$RELEASE" --timeout=120s >/dev/null
  roles
  (cd "$ROOT" && uv run python -m load_tools.steady_client --url "$URL" --concurrency 20 \
    --duration-s "$seconds" --no-retry) &
  pid=$!
  sleep 10
  log "rollout restart at +10 s"
  kctl -n "$NS" rollout restart "deployment/$RELEASE"
  kctl -n "$NS" rollout status "deployment/$RELEASE" --timeout=300s
  log "rollout done; waiting for the client"
  wait "$pid"
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
  up)
    create
    build
    load
    ;;
  install) install "$@" ;;
  roles) roles ;;
  status) status ;;
  drill-rolling) drill_rolling "$@" ;;
  delete) kind delete cluster --name "$CLUSTER" ;;
  *) sed -n '2,20p' "$0" ;;
esac
