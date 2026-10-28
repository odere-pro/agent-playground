#!/usr/bin/env bash
# scale.sh plus two things the PoC-4 stack lacks, for the PoC-6 Mac run. The stack is still
# project poc04, and scale.sh still starts, builds, and stops it; this wrapper only layers on top:
#   - POC06_SCALE_MODEL=slm: LiteLLM in front of the host's llama-server, and every chassis on
#     route `local-small` (scale-litellm-slm.yaml). Needs POC06_LITELLM_KEY in the environment.
#     The default, `fake`, leaves the model as the PoC-4 stack has it.
#   - the engine echo-openai-agents, which scale.sh does not list: its image replaces the
#     echo-python image in every pair.
#
#   scale-slm.sh build
#   scale-slm.sh up <engine> <pairs>      # engine: scale.sh's four, or echo-openai-agents
#   scale-slm.sh down | ps
#
# `pocs/poc-04-stateless-scalable/load/run_matrix.py` calls this in place of scale.sh through
# load_driver.py. Nothing here names or stops a container of another project.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
COMPOSE_DIR=$(dirname "$HERE")
ROOT=$(cd "$COMPOSE_DIR/../.." && pwd)
SCALE="$COMPOSE_DIR/scale.sh"
SECRETS="$COMPOSE_DIR/.env.poc04"
SUPPORTED="echo-python echo-pydanticai echo-langgraph echo-typescript"
MODEL=${POC06_SCALE_MODEL:-fake}
TAG=poc04

usage() {
  sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

build() {
  "$SCALE" build
  docker build -q -f "$ROOT/packages/workloads/echo-openai-agents/Dockerfile" \
    -t "agent-platform/echo-openai-agents:$TAG" "$ROOT"
}

up() {
  local engine=${1:-} pairs=${2:-}
  [[ -n "$engine" && -n "$pairs" ]] || usage
  case "$MODEL" in fake | slm) ;; *) echo "POC06_SCALE_MODEL: fake or slm" >&2; exit 2 ;; esac
  local base=$engine layer=0
  if [[ " $SUPPORTED " != *" $engine "* ]]; then
    [[ "$engine" == echo-openai-agents ]] || { echo "engine: $SUPPORTED echo-openai-agents" >&2; exit 2; }
    base=echo-python
    layer=1
  fi
  [[ "$MODEL" == slm ]] && layer=1
  # scale.sh makes the secrets file, removes any earlier pairs, and starts the project.
  "$SCALE" up "$base" "$pairs"
  [[ "$layer" == 1 ]] || return 0

  local files=(-f docker-compose.scale.yaml) profiles=()
  [[ "$pairs" == 2 ]] && profiles+=(--profile pairs2)
  [[ "$pairs" == 4 ]] && profiles+=(--profile pairs4)
  CHASSIS_CONFIG=scale.yaml
  if [[ "$MODEL" == slm ]]; then
    : "${POC06_LITELLM_KEY:?set POC06_LITELLM_KEY for POC06_SCALE_MODEL=slm}"
    files+=(-f poc06/scale-litellm-slm.yaml)
    CHASSIS_CONFIG=scale-slm.yaml
  fi
  export CHASSIS_CONFIG
  export WORKLOAD_IMAGE="agent-platform/$engine:$TAG"
  echo "scale-slm.sh: $engine, $pairs pair(s), model $MODEL, config $CHASSIS_CONFIG"
  (
    cd "$COMPOSE_DIR"
    docker compose -p poc04 --env-file "$SECRETS" "${files[@]}" ${profiles[@]+"${profiles[@]}"} \
      up -d --wait --no-build --force-recreate --remove-orphans
  )
}

cmd=${1:-}
[[ -n "$cmd" ]] || usage
shift
case "$cmd" in
  build) build ;;
  up) up "$@" ;;
  down) "$SCALE" down ;;
  ps) "$SCALE" ps "$@" ;;
  *) usage ;;
esac
