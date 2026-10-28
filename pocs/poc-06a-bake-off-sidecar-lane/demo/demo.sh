#!/usr/bin/env bash
# PoC-6a offline demo (planning doc docs/planning/poc/006-PoC-6-framework-bake-off.md, "Demo"),
# on localhost with the fake model server. No Docker, no kind, no key, no network at run time.
#   1. make ts-check: build and test the TypeScript agent (echo-typescript), so its rows run
#   2. uv run python -m bakeoff smoke: the super simple task on every engine and lane
#   3. uv run python -m bakeoff run: a short run of the three tasks on four sidecar engines
# Needs first: `make setup`, and Node with the npm registry reachable for `npm ci` in step 1
# (or an installed node_modules). The hosted-model demo comes from the Mac run (`make poc06-mac`);
# this script does not run it.
# Exit non-zero on the first failed step; the record is written only when every step passed.
#
#   pocs/poc-06a-bake-off-sidecar-lane/demo/demo.sh [record.md]
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
POC="$ROOT/pocs/poc-06a-bake-off-sidecar-lane"
OUT=${1:-"$POC/demo/$(date -u +%F)-demo-bakeoff-offline.md"}
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
  echo '# PoC-6a offline demo: every sidecar engine and the untrusted engines on the fake model'
  echo
  echo "Where it ran: localhost on $(uname -sr), $(nproc) CPUs. Processes on random ports; the model is the fake model server (\`packages/fake-model-server/scripts/bakeoff.yaml\`). No Docker, no kind, no key."
  echo "Commit: \`$COMMIT\` ($DIRTY changed or untracked paths at the start). Date: $(date -u +%FT%TZ)."
  echo "Script: \`pocs/poc-06a-bake-off-sidecar-lane/demo/demo.sh\`. The uv \`UV_NATIVE_TLS\` deprecation warning is dropped from the output."
  echo "Hosted-model numbers are not here: they come from the Mac run, \`make poc06-mac\`."
} >"$REC"

step "1. The TypeScript agent: make ts-check"
run -t 14 make ts-check

step "2. The super simple task on every framework: bakeoff smoke"
run uv run python -m bakeoff smoke

step "3. A short run of the three tasks on four sidecar engines: bakeoff run"
RUN_DIR="$TMP/run"
run -t 12 uv run python -m bakeoff run \
  --engine echo-python,echo-pydanticai,echo-openai-agents,echo-typescript --lane sidecar \
  --tasks smoke,simplifier,lookup --repeat 3 --out "$RUN_DIR"
{
  printf '\n`results.md` of that run (offline, fake model; the numbers are the stack'"'"'s own time):\n\n'
  cat "$RUN_DIR/results.md"
} >>"$REC"

ELAPSED=$(($(date +%s) - STARTED))
{
  printf '\n## What this shows\n\n'
  echo "- Criterion 1: every new Python workload (here \`echo-openai-agents\`) runs offline against the fake model server, in memory (\`inprocess\`) and on localhost (\`sidecar\`)."
  echo "- Criterion 2 (the non-Python agent): \`echo-typescript\` passes the same smoke and tasks behind the generic sidecar connector."
  echo "- Criterion 3: the benchmark kit runs the same three tasks on every engine through the same \`/v1/run\` call. The scorecard is \`notes/2026-10-09-scorecard.md\`."
  echo "- The untrusted engines (\`echo-smolagents\`, \`echo-claude-agent\`) pass smoke in the \`remote\` lane; \`kagent-adk\` is SKIP here by design (kind, from CI)."
  echo "- Not shown: hosted-model tokens and latency (criterion 5), the load and hostile suites (criterion 4), and anything on kind. Those wait on the Mac run and on \`poc06-kind.yml\`."
  echo
  echo "Result: every step ok. Total time ${ELAPSED} s. Last command exit code: 0."
} >>"$REC"

mkdir -p "$(dirname "$OUT")"
cp "$REC" "$OUT"
echo "wrote $OUT"
echo "result: every step ok"
