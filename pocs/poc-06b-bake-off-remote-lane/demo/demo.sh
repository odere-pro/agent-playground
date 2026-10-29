#!/usr/bin/env bash
# PoC-6b offline demo (planning doc docs/planning/poc/006-PoC-6-framework-bake-off.md, "Demo"):
# the `remote` lane on localhost with the fake model server. No Docker, no kind, no key, no
# network at run time.
#   1. make ts-check: build and test the TypeScript agent, so its remote row runs
#   2. uv run python -m bakeoff smoke --lane remote: the super simple task on every remote engine
#   3. uv run python -m bakeoff run: the three tasks on the untrusted engines and the TypeScript
#      agent, all in the `remote` lane, through the same /v1/run call
#   4. make test-poc POC=06b: the contract suite over A2A in the remote lane (criteria 1 and 2),
#      the plain-A2A stub of kagent-adk, and the static kind checks; the kind tests skip here
# Needs first: `make setup`, and Node with the npm registry reachable for `npm ci` in step 1
# (or an installed node_modules). The kind half (kagent-adk, the hostile and load suites) is the
# `poc06-kind.yml` CI run table in notes/2026-10-09-lanes-b-kind.md; this script does not run it.
# The hosted-model demo comes from the Mac run (`make poc06-mac`).
# Exit non-zero on the first failed step; the record is written only when every step passed.
#
#   pocs/poc-06b-bake-off-remote-lane/demo/demo.sh [record.md]
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
POC="$ROOT/pocs/poc-06b-bake-off-remote-lane"
OUT=${1:-"$POC/demo/$(date -u +%F)-demo-remote-lane-offline.md"}
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
REC="$TMP/record.md"
STARTED=$(date +%s)
cd "$ROOT"

step() {
  printf '\n## %s\n\n' "$1" >>"$REC"
  printf '== %s\n' "$1"
}

# run [-t N] <command...>: print the command, run it, keep its output (the last N lines with -t)
# and its exit code in the record. A non-zero exit stops the demo.
run() {
  local tail_lines=0 rc=0
  if [ "$1" = "-t" ]; then
    tail_lines=$2
    shift 2
  fi
  printf '$ %s\n\n```\n' "$*" >>"$REC"
  "$@" >"$TMP/out.txt" 2>&1 || rc=$?
  { grep -v 'UV_NATIVE_TLS' "$TMP/out.txt" || true; } >"$TMP/clean.txt"
  local total
  total=$(wc -l <"$TMP/clean.txt" | tr -d ' ')
  if [ "$tail_lines" -gt 0 ] && [ "$total" -gt "$tail_lines" ]; then
    printf '(%s earlier lines trimmed)\n' "$((total - tail_lines))" >>"$REC"
    tail -n "$tail_lines" "$TMP/clean.txt" >>"$REC"
  else
    cat "$TMP/clean.txt" >>"$REC"
  fi
  printf '```\n\nexit code: %s\n' "$rc" >>"$REC"
  if [ "$rc" -ne 0 ]; then
    cat "$TMP/clean.txt"
    echo "demo failed: '$*' exited $rc" >&2
    exit "$rc"
  fi
}

COMMIT=$(git rev-parse --short HEAD)
DIRTY=$(git status --short | wc -l | tr -d ' ')
{
  echo '# PoC-6b offline demo: the remote lane on the fake model'
  echo
  echo "Where it ran: localhost on $(uname -sr), $(nproc) CPUs. Processes on random ports; the model is the fake model server (\`packages/fake-model-server/scripts/bakeoff.yaml\`). No Docker, no kind, no key."
  echo "Commit: \`$COMMIT\` ($DIRTY changed or untracked paths at the start). Date: $(date -u +%FT%TZ)."
  echo "Script: \`pocs/poc-06b-bake-off-remote-lane/demo/demo.sh\`. The uv \`UV_NATIVE_TLS\` deprecation warning is dropped from the output."
  echo "Not here: kind (\`kagent-adk\`, the hostile and load suites; see \`notes/2026-10-09-lanes-b-kind.md\`) and hosted-model numbers (the Mac run, \`make poc06-mac\`)."
} >"$REC"

step "1. The TypeScript agent: make ts-check"
run -t 14 make ts-check

step "2. The super simple task on every remote engine: bakeoff smoke --lane remote"
run uv run python -m bakeoff smoke --lane remote

step "3. The three tasks on the untrusted engines and the TypeScript agent: bakeoff run"
RUN_DIR="$TMP/run"
run -t 12 uv run python -m bakeoff run \
  --engine echo-smolagents,echo-claude-agent,echo-typescript --lane remote \
  --tasks smoke,simplifier,lookup --repeat 3 --out "$RUN_DIR"
{
  printf '\n`results.md` of that run (offline, fake model; the numbers are the stack'"'"'s own time):\n\n'
  cat "$RUN_DIR/results.md"
} >>"$REC"

step "4. The PoC-6b scenario tests: make test-poc POC=06b"
run -t 6 make test-poc POC=06b

ELAPSED=$(($(date +%s) - STARTED))
{
  printf '\n## What this shows\n\n'
  echo "- Criterion 1: \`echo-smolagents\` and \`echo-claude-agent\` run offline in the \`remote\` lane against the fake model server and pass the \`EngineConnectorContract\` over A2A (\`tests/test_poc06b_remote_engine_contract.py\`, in step 4)."
  echo "- Criterion 2: the TypeScript agent runs the same tasks as a remote, and the plain-A2A mode passes the suite against a stub shaped like the kagent probe (\`tests/test_poc06b_plain_a2a_contract.py\`). The real \`kagent-adk\` is SKIP here by design: kind, from CI."
  echo "- Criterion 3: the benchmark kit runs the same three tasks on every remote engine through the same \`/v1/run\` call. The scorecard is \`../poc-06a-bake-off-sidecar-lane/notes/2026-10-09-scorecard.md\`."
  echo "- Criterion 8: the contract freeze is pinned by \`packages/chassis/tests/test_contract_freeze.py\`, not by this demo."
  echo "- Not shown: kind (criteria 2 and 4 on the cluster; the CI run table in \`notes/2026-10-09-lanes-b-kind.md\`) and hosted-model tokens (criterion 5, the Mac run)."
  echo
  echo "Result: every step ok. Total time ${ELAPSED} s. Last command exit code: 0."
} >>"$REC"

mkdir -p "$(dirname "$OUT")"
cp "$REC" "$OUT"
echo "wrote $OUT"
echo "result: every step ok"
