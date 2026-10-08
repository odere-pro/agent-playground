#!/usr/bin/env bash
# PoC-4 demo (docs/planning/poc/004-PoC-4-stateless-scalable.md, "Demo"), on the Compose scale
# stack, project poc04 only:
#   1. scale.sh up echo-python 2
#   2. kill drill: SIGKILL one pair under retried load; retried keys replay the same result
#   3. scale.sh up echo-python 2 again (a fresh pair 2), graceful drill: SIGTERM, no retry
#   4. config reload drill: a good document in MinIO takes effect with no restart, a bad one is
#      refused and the last good one stays
#   5. scale.sh down (also on any failure)
# The load matrix is not rerun here; its record is notes/2026-10-01-load-results.md.
# Exits non-zero on the first failed step. Writes the record to demo/<date>-demo-scale.md.
# Needs the images: deploy/compose/scale.sh build.
#
#   pocs/poc-04-stateless-scalable/demo/demo.sh [record.md]
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
POC="$ROOT/pocs/poc-04-stateless-scalable"
SCALE="$ROOT/deploy/compose/scale.sh"
OUT=${1:-"$POC/demo/$(date -u +%F)-demo-scale.md"}
LOAD="$POC/load"
TMP=$(mktemp -d)

cleanup() {
  "$SCALE" down >/dev/null 2>&1 || true
  rm -rf "$TMP"
}
trap cleanup EXIT

step() {
  printf '\n## %s\n\n' "$1"
}

run() {
  # Print the command, run it, and keep its output in the record.
  printf '$ %s\n' "$*"
  "$@" 2>&1
}

{
  echo "# PoC-4 demo: kill a replica, drain a replica, reload the config (Compose, project poc04)"
  echo
  echo "Recorded $(date -u +%FT%TZ) by pocs/poc-04-stateless-scalable/demo/demo.sh."
  echo "Docker $(docker version --format '{{.Server.Version}}'), $(uname -sm)."
  echo
  echo '```console'
  step "1. Two pairs of echo-python"
  run "$SCALE" up echo-python 2 | grep -v '^ Container' || true
  [[ $(docker ps -q --filter label=com.docker.compose.project=poc04 \
    --filter name=poc04-chassis- | wc -l) -eq 2 ]]
  step "2. Kill drill: SIGKILL pair 2 under load; the client retries with the same key"
  run uv run python "$LOAD/kill_drill.py" --mode kill --pair 2 --out "$TMP/kill.json" | tail -32
  python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); sys.exit(0 if r["passed"] else 1)' \
    "$TMP/kill.json"
  step "3. Graceful drill: a fresh pair 2, SIGTERM its chassis under load, no retry"
  run "$SCALE" up echo-python 2 | grep -v '^ Container' || true
  run uv run python "$LOAD/kill_drill.py" --mode graceful --pair 2 --out "$TMP/graceful.json" |
    tail -24
  python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); sys.exit(0 if r["passed"] else 1)' \
    "$TMP/graceful.json"
  step "4. Config reload: a good document, then a bad one, in MinIO; no restart"
  run uv run python "$LOAD/reload_drill.py"
  step "5. Down"
  run "$SCALE" down | grep -v '^ Container' || true
  echo
  echo "result: every step ok"
  echo '```'
} 2>&1 | tee "$OUT"
