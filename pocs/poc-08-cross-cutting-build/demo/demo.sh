#!/usr/bin/env bash
# PoC-8 offline demo (planning doc docs/planning/poc/008-PoC-8-build-cross-cutting.md, "Demo"):
# "A change to the business logic that drops facts fails the eval step in CI. In a live call, a
# low score triggers a retry and then a fallback. The trace shows each step, and a thumbs-down
# feedback call appears on the same trace and on the dashboard."
#   1. The eval gate passes the normal business logic: DeepEval on the 50 gate cases for
#      echo-python in the sidecar lane, then `bakeoff.evalgate` against evals/gate/baseline.json.
#   2. The eval gate fails the drop-facts logic: `make evals-regression`, the CI step of
#      poc08-evals.yml. The gate exits 1; the target turns that into the step's pass.
#   3. A live call, echo-langgraph in the sidecar lane, every stage on: the main route's answer
#      scores low, the gate retries twice (spec.harness.retries: 2), then falls back to
#      spec.model.fallback_route. Then every chassis span of the run, a thumbs-down
#      POST /v1/feedback on its request id, and the gate and feedback lines of GET /metrics.
# Not shown offline: the Grafana dashboard itself (no Grafana here; the dashboards in
# deploy/dashboards read the same /metrics families, including the feedback panel), the real
# Langfuse, LiteLLM, and Prompt Guard 2 (the Mac run). The judge's scores are scripted.
# Needs first: `make setup`; DeepEval 4.3.2 in the uv environment for steps 1 and 2.
# No Docker, no kind, no key. Localhost TCP in steps 1 and 2 (the fake model server); Unix sockets
# in step 3. Exit non-zero on the first failed step; the record is written only when every step
# passed.
#
#   pocs/poc-08-cross-cutting-build/demo/demo.sh [record.md]
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
POC="$ROOT/pocs/poc-08-cross-cutting-build"
DEMO_PY="pocs/poc-08-cross-cutting-build/demo/poc08_demo.py"
OUT=${1:-"$POC/demo/$(date -u +%F)-demo-cross-cutting-build-offline.md"}
TMP=$(mktemp -d)
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT
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
  if [ "${1:-}" = "-t" ]; then
    tail_lines=$2
    shift 2
  fi
  printf '$ %s\n\n```\n' "${*//$TMP/\$TMP}" >>"$REC"
  "$@" >"$TMP/out.txt" 2>&1 || rc=$?
  { grep -v 'UV_NATIVE_TLS' "$TMP/out.txt" || true; } | sed "s#$TMP#\$TMP#g" >"$TMP/clean.txt"
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
  echo '# PoC-8 offline demo: a fact-dropping change fails the eval gate; a live call retries, falls back, and takes a thumbs-down on its trace'
  echo
  echo "Where it ran: localhost on $(uname -sr). Steps 1 and 2: the \`sidecar\` lane in a \`bakeoff\` stack (the fake model server on localhost TCP as the engine's model and the judge). Step 3: the \`sidecar\` lane on Unix sockets, every PoC-8 stage on, the LiteLLM stub and the guardrail server (pattern stand-in) in process. No Docker, no kind, no key."
  echo "Commit: \`$COMMIT\` ($DIRTY changed or untracked paths at the start). Date: $(date -u +%FT%TZ)."
  echo "Script: \`pocs/poc-08-cross-cutting-build/demo/demo.sh\` (helper \`poc08_demo.py\`, which reuses \`poc_harness.full_stack\`). The uv \`UV_NATIVE_TLS\` deprecation warning is dropped from the output; the temp folder is shown as \`\$TMP\`."
  echo "Not shown here: a Grafana dashboard (no Grafana runs offline; step 3 shows the \`/metrics\` lines the dashboards read, including `chassis_feedback_total`, which the dashboards' feedback panel reads), the real Langfuse, LiteLLM, and Prompt Guard 2 (the Mac run). The judge's scores are scripted: they show the mechanics, not judge quality."
} >"$REC"

step "1. The eval gate passes the normal business logic (echo-python, sidecar)"
run make evals EVAL_OUT="$TMP/evals" EVAL_ARGS="--engine echo-python --lane sidecar"

step "2. Business logic that drops facts fails the eval gate (the CI regression step)"
printf '%s\n\n' "The gate's own exit code is 1 here; \`make evals-regression\` turns exactly that into exit 0 and fails on anything else." >>"$REC"
run make evals-regression EVAL_OUT="$TMP/evals"

step "3. A live call: low score, retry, fallback; its trace; a thumbs-down on it; /metrics"
run uv run python "$DEMO_PY" live --engine echo-langgraph --lane sidecar

ELAPSED=$(($(date +%s) - STARTED))
{
  printf '\n## What this shows\n\n'
  echo "- Criterion 5: the drop-facts logic takes the pass rate from 0.780 to 0.000 and fails every must-pass case; \`bakeoff.evalgate\` exits 1, the step that blocks a merge in \`poc08-evals.yml\`. The normal logic passes at the baseline."
  echo "- Criterion 6: the gate retries as many times as \`spec.harness.retries\` says (2), then the model proxy sends the next attempt to \`spec.model.fallback_route\`; the status is \`fallback\` and the low-scored answer never leaves."
  echo "- Criterion 3: every chassis span of the call (run, engine attempts, router, model proxy, evaluator score) carries the run's one trace id; \`/metrics\` counts the request, the model calls per route, the retries, and the fallback."
  echo "- Criterion 4: the thumbs-down with only the \`request_id\` lands on the run's trace and is counted in \`chassis_feedback_total{label=\"thumbs-down\"}\`."
  echo "- Not shown: the dashboard panel. The planning doc's \"on the dashboard\" waits on Grafana loading \`deploy/dashboards\`; their \`User feedback by label\` panel reads \`chassis_feedback_total\`, shown in step 3."
  echo
  echo "Result: every step ok. Total time ${ELAPSED} s. Last command exit code: 0."
} >>"$REC"

mkdir -p "$(dirname "$OUT")"
cp "$REC" "$OUT"
echo "wrote $OUT"
echo "result: every step ok"
