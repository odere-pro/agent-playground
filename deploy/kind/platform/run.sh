#!/usr/bin/env bash
# The reference stack on kind (139 CH-19): seven parts on the PoC-5 cluster.
#
#   deploy/kind/platform/run.sh up     PoC-5 `up kafka` (LiteLLM and its MCP gateway, Valkey,
#                                      Kafka), then the OTel Collector and Jaeger, then the
#                                      example agent from deploy/helm. A second `up` converges
#   deploy/kind/platform/run.sh smoke  the seven parts Ready, then the example agent's demo calls
#                                      (run, chat, MCP) and one trace query in Jaeger
#   deploy/kind/platform/run.sh down   delete the cluster (PoC-5's `delete`)
#   deploy/kind/platform/run.sh plan   print every command of `up smoke down`; runs none
#   Several verbs run in order: run.sh up smoke
#
# `make platform-up`, `make platform-smoke`, and `make platform-down` call this script. Like PoC-6,
# it edits no PoC-5 file and calls deploy/kind/poc05/run.sh for the bring-up and the delete. Every
# kubectl call pins `--context kind-poc05`, every helm call `--kube-context kind-poc05`, every
# kind call `--name poc05`; it never reads the current context.
#
# PLATFORM_DRY_RUN=1 prints each external command (`+ ...`) instead of running it, for any verb.
# Secrets: the OTLP token is made here, piped into two Secrets, never printed or put in argv.
set -euo pipefail

CLUSTER=poc05
CONTEXT=kind-$CLUSTER
LOG_PREFIX=kind-platform
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
P5=$ROOT/deploy/kind/poc05/run.sh
DEPLOYER=system:serviceaccount:agent-platform-system:deployer
PLATFORM_NS=poc05-platform
AGENTS_NS=poc05-agents
RELEASE=example-agent
DRY_RUN=${PLATFORM_DRY_RUN:-0}
# The chart's images: the chassis under the library chart's tag (values-kind.yaml names the
# kind.local repository), the workload under values-kind.yaml's image.
CHASSIS_IMAGE=kind.local/agent-platform/chassis:v0.1.0
ECHO_IMAGE=kind.local/agent-platform/echo-python:poc09
IMAGES=(
  "$CHASSIS_IMAGE|packages/chassis/Dockerfile"
  "$ECHO_IMAGE|packages/workloads/echo-python/Dockerfile"
)
# The seven parts: "<name>|<namespace>|<deployment>". The MCP gateway is LiteLLM's /mcp/ route.
PARTS=(
  "litellm|$PLATFORM_NS|litellm"
  "mcp-gateway|$PLATFORM_NS|litellm"
  "valkey|$PLATFORM_NS|valkey"
  "kafka|$PLATFORM_NS|kafka"
  "otel-collector|$PLATFORM_NS|otel-collector"
  "jaeger|$PLATFORM_NS|jaeger"
  "example-agent|$AGENTS_NS|$RELEASE"
)
AGENT_PORT=18083
JAEGER_PORT=18686

# shellcheck source=deploy/kind/lib.sh
source "$HERE/../lib.sh"
# apply_policies NS FILE and apply_deployer, shared with PoC-5 and PoC-6.
# shellcheck source=deploy/kind/policies.sh
source "$HERE/../policies.sh"

# x CMD...: run CMD, or print it under a dry run.
x() {
  if [[ $DRY_RUN == 1 ]]; then
    printf '+ %s\n' "$*"
  else
    "$@"
  fi
}
xk() { x kubectl --context "$CONTEXT" "$@"; }
xh() { x helm --kube-context "$CONTEXT" "$@"; }

build_and_load() {
  local entry image dockerfile
  for entry in "${IMAGES[@]}"; do
    image=${entry%%|*}
    dockerfile=${entry#*|}
    x docker build -t "$image" -f "$ROOT/$dockerfile" "$ROOT"
    x kind load docker-image "$image" --name "$CLUSTER"
  done
}

# The chassis tag the chart runs, added to rule 9's list (cluster admin, as PoC-5 applies its own).
apply_params() { xk apply -f "$HERE/admission/params.yaml"; }

# seed_otlp: one token, in the Collector's Secret and, as the OTLP header, in the chassis's.
seed_otlp() {
  if [[ $DRY_RUN != 1 ]] && kctl -n "$PLATFORM_NS" get secret otel-collector-auth >/dev/null 2>&1; then
    log "the OTLP token Secrets exist"
    return
  fi
  local token=""
  [[ $DRY_RUN == 1 ]] || token=$(openssl rand -hex 32)
  printf '%s' "$token" |
    xk -n "$PLATFORM_NS" create secret generic otel-collector-auth --from-file=token=/dev/stdin
  printf 'Authorization=Bearer%%20%s' "$token" |
    xk -n "$AGENTS_NS" create secret generic "$RELEASE-otlp" \
      --from-file=OTEL_EXPORTER_OTLP_HEADERS=/dev/stdin
}

apply_observability() {
  if [[ $DRY_RUN == 1 ]]; then
    printf '+ kubectl --context %s apply -n %s -f %s\n' "$CONTEXT" "$PLATFORM_NS" "$HERE/network-policy.yaml"
    printf '+ kubectl --context %s apply -n %s -f %s --as=%s\n' \
      "$CONTEXT" "$PLATFORM_NS" "$HERE/observability.yaml" "$DEPLOYER"
  else
    apply_policies "$PLATFORM_NS" "$HERE/network-policy.yaml"
    kctl apply -n "$PLATFORM_NS" -f "$HERE/observability.yaml" --dry-run=client -o name >/dev/null
    apply_deployer <"$HERE/observability.yaml"
  fi
  xk -n "$PLATFORM_NS" rollout status deployment/otel-collector --timeout=180s
  xk -n "$PLATFORM_NS" rollout status deployment/jaeger --timeout=180s
}

install_agent() {
  local work
  work=$(mktemp -d "${TMPDIR:-/tmp}/platform-helm.XXXXXX")
  # shellcheck disable=SC2064 # expand now: $work is local
  trap "rm -rf '$work'" EXIT
  x cp -R "$ROOT/deploy/helm/agent-lib" "$ROOT/deploy/helm/example-agent" "$work/"
  x rm -rf "$work/example-agent/charts"
  xh dependency update --skip-refresh "$work/example-agent"
  xh upgrade --install "$RELEASE" "$work/example-agent" --namespace "$AGENTS_NS" \
    -f "$work/example-agent/values-kind.yaml" -f "$HERE/example-agent-values.yaml" \
    --wait --timeout 300s
}

up() {
  x "$P5" up kafka
  build_and_load
  apply_params
  seed_otlp
  apply_observability
  install_agent
  log "up: the seven parts are applied; run 'smoke'"
}

# with_forward NS SVC LOCAL:REMOTE CMD...: CMD while a port-forward runs; stops it by PID.
with_forward() {
  local ns=$1 svc=$2 ports=$3 pid status=0
  shift 3
  if [[ $DRY_RUN == 1 ]]; then
    printf '+ kubectl --context %s -n %s port-forward svc/%s %s\n' "$CONTEXT" "$ns" "$svc" "$ports"
    "$@"
    return
  fi
  kctl -n "$ns" port-forward "svc/$svc" "$ports" >/dev/null 2>&1 &
  pid=$!
  sleep 2
  "$@" || status=$?
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  return "$status"
}

demo_calls() {
  local url=http://127.0.0.1:$AGENT_PORT
  local mcp=(-fsS -X POST "$url/v1/mcp" -H 'content-type: application/json'
    -H 'accept: application/json, text/event-stream')
  x curl -fsS -X POST "$url/v1/run" -H 'content-type: application/json' \
    -d '{"input": {"text": "hello"}}'
  x curl -fsS -X POST "$url/v1/chat/completions" -H 'content-type: application/json' \
    -d '{"model": "example-agent", "messages": [{"role": "user", "content": "hello"}]}'
  x curl "${mcp[@]}" -d '{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "0"}}}'
}

trace_query() {
  x curl -fsS "http://127.0.0.1:$JAEGER_PORT/api/traces?service=$RELEASE&limit=1"
}

smoke() {
  local part name ns deployment
  for part in "${PARTS[@]}"; do
    IFS='|' read -r name ns deployment <<<"$part"
    printf '== smoke: %s (%s/%s)\n' "$name" "$ns" "$deployment"
    xk -n "$ns" rollout status "deployment/$deployment" --timeout=180s
  done
  printf '== smoke: demo (example-agent: run, chat, MCP)\n'
  with_forward "$AGENTS_NS" "$RELEASE" "$AGENT_PORT:8080" demo_calls
  printf '== smoke: trace (Jaeger, service %s)\n' "$RELEASE"
  with_forward "$PLATFORM_NS" jaeger "$JAEGER_PORT:16686" trace_query
}

down() { x "$P5" delete; }

usage() { sed -n '2,19p' "$0"; }

[[ $# -gt 0 ]] || { usage; exit 0; }
for cmd in "$@"; do
  case $cmd in
    up) up ;;
    smoke) smoke ;;
    down | delete) down ;;
    plan) DRY_RUN=1 up; DRY_RUN=1 smoke; DRY_RUN=1 down ;;
    *) usage; exit 1 ;;
  esac
done
