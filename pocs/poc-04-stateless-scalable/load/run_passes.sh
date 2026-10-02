#!/usr/bin/env bash
# Run the whole load matrix N times (suggested: 3) and keep each pass apart, because single runs
# on the shared Docker Desktop VM varied up to 3x on 2026-10-01. Each pass goes to
# notes/load/passes/<n>/ (results.json, raw/); median.py then picks the median pass per scenario.
#   pocs/poc-04-stateless-scalable/load/run_passes.sh [passes] [duration_s]
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
LOAD="$ROOT/pocs/poc-04-stateless-scalable/notes/load"
PASSES=${1:-3}
DURATION=${2:-30}
for n in $(seq 1 "$PASSES"); do
  for engine in echo-python echo-pydanticai echo-langgraph echo-typescript; do
    echo "== pass $n $engine $(date -u +%T)"
    uv run python "$ROOT/pocs/poc-04-stateless-scalable/load/run_matrix.py" \
      --engine "$engine" --only main --duration-s "$DURATION" 2>&1 | grep -E "^echo-|run_matrix"
  done
  echo "== pass $n hop+idem $(date -u +%T)"
  uv run python "$ROOT/pocs/poc-04-stateless-scalable/load/run_matrix.py" \
    --only hop --only idem --duration-s "$DURATION" 2>&1 | grep -E "^echo-|^hop|run_matrix"
  mkdir -p "$LOAD/passes/$n"
  mv "$LOAD/results.json" "$LOAD/passes/$n/results.json"
  mv "$LOAD/raw" "$LOAD/passes/$n/raw"
done
"$ROOT/deploy/compose/scale.sh" down >/dev/null 2>&1
echo "== done $(date -u +%T)"
