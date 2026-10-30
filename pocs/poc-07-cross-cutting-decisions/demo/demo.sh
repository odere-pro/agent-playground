#!/usr/bin/env bash
# PoC-7 offline demo (planning doc docs/planning/poc/007-PoC-7-cross-cutting-decisions.md, "Demo"):
# "For two engines, one trace shows the whole call: chassis, engine steps in the workload, model
# calls, and tool calls. A feedback call shows up on that trace. The eval run compares the two
# engines on the same cases. The red-team set is blocked."
#   1. Trace + feedback, echo-langgraph: one tool-using /v1/run through the chassis in the sidecar
#      lane, OtelTelemetry and the workload's OpenInference tracing into one in-memory exporter;
#      the span tree; then POST /v1/feedback for that request id: the score in the fake Langfuse
#      and the redacted agents.feedback.received.v1 event, both on the run's trace id.
#   2. The same for echo-openai-agents.
#   3. Evals: DeepEval (the pick) on the 20 golden cases for echo-python and echo-langgraph, via
#      `python -m bakeoff.evaltrial --offline`; eval-report.v0 summaries side by side. If the tool
#      cannot run (no warm uv cache), the committed reports in notes/evals/ are shown and the
#      record says so.
#   4. Red team: `python -m chassis_contracts.redteam` (no --write), per-category table. It uses
#      the pattern stand-in classifier, not Prompt Guard 2.
#   5. Gate: one sync-mode call the judge fails; it retries once and falls back (status fallback).
# Not shown offline: the real Langfuse and Jaeger UIs (`poc07-stack.yml`, deploy/compose); Prompt
# Guard 2 and the NLI encoder (the Mac run, Hugging Face weights). Judge scores are scripted.
# Needs first: `make setup`; DeepEval 4.3.2 wheels in the uv cache for step 3 (else the fallback).
# No Docker, no kind, no key. Unix sockets and ASGI in steps 1, 2, 5; localhost TCP in step 3.
# Exit non-zero on the first failed step; the record is written only when every step passed.
#
#   pocs/poc-07-cross-cutting-decisions/demo/demo.sh [record.md]
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
POC="$ROOT/pocs/poc-07-cross-cutting-decisions"
DEMO_PY="pocs/poc-07-cross-cutting-decisions/demo/demo.py"
# demo.py imports the PoC-7 test harnesses and two test modules; the shared ones come from the
# poc-harness package.
export PYTHONPATH="$POC/tests${PYTHONPATH:+:$PYTHONPATH}"
OUT=${1:-"$POC/demo/$(date -u +%F)-demo-cross-cutting-offline.md"}
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

# run [-t N] [-s] <command...>: print the command, run it, keep its output (the last N lines with
# -t) and its exit code in the record. A non-zero exit stops the demo, unless -s (soft): then the
# exit code is returned for the caller to handle.
run() {
  local tail_lines=0 soft=0 rc=0
  while [ "$#" -gt 0 ]; do
    case "$1" in
      -t) tail_lines=$2; shift 2 ;;
      -s) soft=1; shift ;;
      *) break ;;
    esac
  done
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
    if [ "$soft" -eq 1 ]; then
      return "$rc"
    fi
    echo "demo failed: '$*' exited $rc" >&2
    exit "$rc"
  fi
}

COMMIT=$(git rev-parse --short HEAD)
DIRTY=$(git status --short | wc -l | tr -d ' ')
{
  echo '# PoC-7 offline demo: one trace and feedback on two engines, evals on two engines, the red-team set, the evaluator gate'
  echo
  echo "Where it ran: localhost on $(uname -sr), $(nproc) CPUs, \`sidecar\` lane: the workload's template A2A server on a Unix socket, the chassis over ASGI, the fake model server, the fake MCP server, and the fake Langfuse API in process. No Docker, no kind, no key, no Hugging Face."
  echo "Commit: \`$COMMIT\` ($DIRTY changed or untracked paths at the start). Date: $(date -u +%FT%TZ)."
  echo "Script: \`pocs/poc-07-cross-cutting-decisions/demo/demo.sh\` (helper \`demo.py\`, which reuses the PoC test harnesses). The uv \`UV_NATIVE_TLS\` deprecation warning is dropped from the output."
  echo "Not shown here: the real Langfuse and Jaeger (the \`poc07-stack.yml\` Compose stack), Prompt Guard 2 and the NLI encoder (the Mac run). The judge's scores are scripted: they show the mechanics, not judge quality."
} >"$REC"

step "1. One trace, then feedback on it: echo-langgraph"
run uv run python "$DEMO_PY" trace --engine echo-langgraph

step "2. One trace, then feedback on it: echo-openai-agents"
run uv run python "$DEMO_PY" trace --engine echo-openai-agents

step "3. Evals: DeepEval on the 20 golden cases, echo-python and echo-langgraph"
EVALS="$TMP/evals"
if run -s uv run python -m bakeoff.evaltrial --tool deepeval --offline \
  --engine echo-python,echo-langgraph --out "$EVALS"; then
  printf '\nThe reports that run wrote, in the shared format:\n\n' >>"$REC"
  run uv run python "$DEMO_PY" evals "$EVALS"
else
  printf '\nThe DeepEval run could not start here (no warm uv cache, no network). Below are the committed reports from `notes/evals/`, not a fresh run.\n\n' >>"$REC"
  run uv run python "$DEMO_PY" evals pocs/poc-07-cross-cutting-decisions/notes/evals
fi

step "4. Red team: the set through the chassis, per category"
printf 'The classifier is the pattern stand-in, not Prompt Guard 2 (its numbers wait on the Mac run). "stopped/cases" per guardrail stack; misses and owners are in `notes/2026-10-28-redteam.md`.\n\n' >>"$REC"
run uv run python -m chassis_contracts.redteam

step "5. Evaluator gate: the judge fails the answer, one retry, then the fallback route"
run uv run python "$DEMO_PY" gate --engine echo-langgraph
printf '\nOne harness line is filtered in this step: a server thread'"'"'s asyncio `InvalidStateError` on `Future.set_result` at shutdown (a cancel race in the test server, not in the chassis).\n' >>"$REC"

ELAPSED=$(($(date +%s) - STARTED))
{
  printf '\n## What this shows\n\n'
  echo "- Criterion 4: on each engine, every span (chassis, workload, OpenInference, model proxy, \`/mcp\`) carries the caller's one trace id, joined by \`traceparent\` over A2A."
  echo "- Criterion 5 (observability, feedback, evals): the same chassis code serves both engines' traces, the feedback, and the gate; no engine-specific branch."
  echo "- Feedback: \`POST /v1/feedback\` with only the request id lands on the run's trace in the fake Langfuse, and \`agents.feedback.received.v1\` is published with the comment redacted."
  echo "- Criterion 7: the red-team set runs through the chassis with a pass rate per category; it is not fully blocked, and every miss has an owner in the note. Stand-in classifier only."
  echo "- Criterion 8: DeepEval writes \`eval-report.v0\` for both engines, the same cases and the same verdicts."
  echo "- Evaluator gate (017 H-4): a sync failure retries once and falls back; the bad answer never leaves."
  echo
  echo "Result: every step ok. Total time ${ELAPSED} s. Last command exit code: 0."
} >>"$REC"

mkdir -p "$(dirname "$OUT")"
cp "$REC" "$OUT"
echo "wrote $OUT"
echo "result: every step ok"
