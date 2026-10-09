#!/usr/bin/env bash
# PoC-5 demo (plan docs/plans/2026-10-02-poc-05-sandboxed.md, section 7, T26), on the kind cluster
# poc05 only. Assumes the cluster is up: `make kind-poc05 ARGS=up`. Brings nothing up or down.
#   1. A normal request through each lane: agent-echo (sidecar), chassis-echo-remote (remote)
#   2. The remote probe: secrets (H25), a public address (H19), disk (H21), processes (H23), the
#      unlisted tool (H08), the model proxy without its token (H17); each refused, its control allowed
#   3. A normal request through each lane again: both still answer
#   4. The sidecar probe: LiteLLM (H05) and the MCP gateway (H07) from the workload, refused;
#      through the chassis, allowed. Then the in-pod probe suite (test_poc05_kind_probe.py), or
#      the T10 exception line while that suite is not built (notes/2026-10-09-t10-probe-exception.md)
#   5. Admission: every fixture, `apply --dry-run=server`; the rejected ones name their rule
#   6. The code runner: one run_python call on gVisor, no egress
#   7. H11, the broker: the Kafka SASL case (test_poc05_kind_hardreq1.py -k kafka)
#   8. Where it was logged: probe.check lines, the remote listener's 401/403, LiteLLM's 401 lines
# Every check reuses a run.sh verb or an existing kind test; nothing probes on its own. Each pytest
# step prints one line per check: id, attempt, outcome, control. A failed, skipped, or missing
# check stops the demo non-zero. The last line is `result: every step ok`; while the probe suite is
# not built it is `result: every step ok; T10 recorded exception`. All captured output goes through
# `run.sh redact`; the gateway key reaches pytest only through `run.sh with-gateway` (env, never
# argv or output). Every kubectl call pins --context kind-poc05. Writes the record to
# demo/<date>-demo-sandboxed.md.
#
#   pocs/poc-05-sandboxed/demo/demo.sh [record.md]
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
POC="$ROOT/pocs/poc-05-sandboxed"
TESTS=pocs/poc-05-sandboxed/tests
RUN="$ROOT/deploy/kind/poc05/run.sh"
CONTEXT=kind-poc05
OUT=${1:-"$POC/demo/$(date -u +%F)-demo-sandboxed.md"}
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
cd "$ROOT"

kctl() { kubectl --context "$CONTEXT" "$@"; }

step() {
  printf '\n## %s\n\n' "$1"
}

run() {
  # Print the command, run it, and keep its redacted output in the record.
  printf '$ %s\n' "$*"
  "$@" 2>&1 | "$RUN" redact
}

# The checks the summary names: test function|id|attempt|outcome when it passes|allowed control.
# A test not listed here prints its H id (from its name) and its name; its docstring holds the rest.
CHECKS=$(
  cat <<'EOF'
test_remote_holds_one_secret_its_own_token|H25|remote pod reads a Secret other than its token|refused: none mounted|its own CHASSIS_API_TOKEN is there
test_h19_remote_reaches_no_outside_address|H19|remote pod TCP to 1.1.1.1:443 and :80|refused: policy drop|an unpoliced caller reaches 1.1.1.1
test_h21_root_is_read_only_and_tmp_is_writable|H21|remote pod writes to /var/tmp on /|refused: EROFS|the same write to /tmp works
test_h23_pids_cap_is_the_configured_limit|H23|remote pod processes past podPidsLimit|capped: pids.max=256|pids.current above zero, under the cap
test_unlisted_probe_is_refused_with_its_control|H08|call the unlisted probe tool|refused: not listed|glossary_lookup answers
test_h17_listener_answers_only_the_remote_token_inside_a_run|H17|model call to 8091, no or wrong token, or outside a run|refused: 401, 403|own token inside a run: 200
test_litellm_refuses_the_workload_without_the_chassis_key|H05|workload calls LiteLLM, no or wrong key|refused: 401|through the chassis proxy: 200
test_mcp_gateway_refuses_the_workload_without_the_chassis_key|H07|workload opens the MCP gateway, no or wrong key|refused: 401, no tool|through chassis /mcp: tool listed
test_fixture_outcome_matches_its_header|ADM|apply --dry-run=server of the fixture|as its header says|the fixture's admitted twin
test_code_runs_on_gvisor_with_no_egress|H28|run_python TCP to outside, LiteLLM, dispatcher, API|refused: policy drop|its own 127.0.0.1:8000; each target up from an allowed peer
EOF
)

# summarize JUNIT: one line per test case; exit 1 unless every case passed.
SUMMARY_PY='
import re, sys
import xml.etree.ElementTree as ET
rows = {}
for line in sys.argv[2].splitlines():
    name, *rest = line.split("|")
    rows[name] = rest
bad = 0
for case in ET.parse(sys.argv[1]).iter("testcase"):
    full = case.get("name", "")
    func, _, param = full.partition("[")
    param = param.rstrip("]")
    hid = re.search(r"h(\d\d)", func)
    cid, attempt, ok, control = rows.get(
        func, [f"H{hid.group(1)}" if hid else "-", func, "ok", "in the test docstring"]
    )
    if cid == "ADM" and param:
        cid = param
    elif param:
        attempt = f"{attempt} [{param}]"
    outcome = ok
    for tag in ("failure", "error", "skipped"):
        node = case.find(tag)
        if node is not None:
            reason = " ".join((node.get("message") or "").split())[:80]
            outcome = f"{tag.upper()}: {reason}"
            bad += 1
            break
    print(f"{cid:<34} | {attempt:<52} | {outcome:<24} | control: {control}")
sys.exit(1 if bad else 0)
'

# kind_tests LABEL NODEID...: run the kind tests through with-gateway, print pytest's tail, then
# one line per check. A missing test file fails the step by name, before anything runs.
kind_tests() {
  local label=$1 xml rc=0 id
  shift
  for id in "$@"; do
    [[ $id != *.py && $id != *.py::* ]] && continue
    [[ -f ${id%%::*} ]] || { echo "missing test file: ${id%%::*} (not written yet?)"; return 1; }
  done
  xml="$TMP/$label.xml"
  printf '$ run.sh with-gateway env POC05_KIND=1 uv run pytest -m network -q -rs %s\n' "$*"
  "$RUN" with-gateway env POC05_KIND=1 uv run pytest -m network -q -rs -p no:cacheprovider \
    --junitxml="$xml" "$@" 2>&1 | "$RUN" redact | tail -n 12 || rc=$?
  [[ -s $xml ]] || { echo "no test report for $label"; return 1; }
  echo
  python3 -c "$SUMMARY_PY" "$xml" "$CHECKS" || rc=1
  return "$rc"
}

# count_lines LABEL PATTERN: how many redacted pod log lines match PATTERN; prints the last three.
# count_lines LABEL REGEX [POD]: lines of the saved logs matching REGEX, only inside the
# `::group::<ns>/<POD>...` blocks of `run.sh logs` when POD is given; the count and the last 3.
count_lines() {
  local lines n
  lines=$(awk -v pod="${3-}" '
    /^::group::/ { split(substr($0, 10), p, "/"); keep = (pod == "" || index(p[2], pod) == 1); next }
    /^::endgroup::/ { keep = (pod == ""); next }
    keep' "$TMP/logs.txt" | grep -E "$2" || true)
  n=$(grep -c . <<<"$lines" || true)
  printf '%-36s %s line(s)\n' "$1" "$n"
  [[ -z $lines ]] || tail -n 3 <<<"$lines" | cut -c1-200
}

{
  echo "# PoC-5 demo: two lanes, the probes refused, their controls allowed (kind, cluster poc05)"
  echo
  echo "Recorded $(date -u +%FT%TZ) by pocs/poc-05-sandboxed/demo/demo.sh."
  echo "kubectl $(kctl version --client -o json | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["clientVersion"]["gitVersion"])'), $(uname -sm)."
  echo
  echo '```console'
  step "0. The cluster is up"
  kind get clusters | grep -qx poc05 || { echo "cluster poc05 is not up: make kind-poc05 ARGS=up"; exit 1; }
  run kctl -n poc05-agents get deployment agent-echo chassis-echo-remote
  step "1. A normal request through each lane"
  run "$RUN" request
  step "2. The remote probe: H25, H19, H21, H23, H08, H17"
  kind_tests remote-probe \
    "$TESTS/test_poc05_kind_remote_controls.py::test_remote_holds_one_secret_its_own_token" \
    "$TESTS/test_poc05_kind_remote_controls.py::test_h19_remote_reaches_no_outside_address" \
    "$TESTS/test_poc05_kind_remote_controls.py::test_h21_root_is_read_only_and_tmp_is_writable" \
    "$TESTS/test_poc05_kind_remote_controls.py::test_h23_pids_cap_is_the_configured_limit" \
    "$TESTS/test_poc05_kind_tool_gateway.py::test_unlisted_probe_is_refused_with_its_control" \
    "$TESTS/test_poc05_kind_remote_controls.py::test_h17_listener_answers_only_the_remote_token_inside_a_run"
  step "3. A normal request again: both lanes still answer"
  run "$RUN" request
  step "4. The sidecar probe: H05 and H07 from the workload, then the in-pod probe suite"
  kind_tests sidecar-probe \
    "$TESTS/test_poc05_kind_hardreq1.py::test_litellm_refuses_the_workload_without_the_chassis_key" \
    "$TESTS/test_poc05_kind_hardreq1.py::test_mcp_gateway_refuses_the_workload_without_the_chassis_key"
  RESULT="every step ok"
  if [[ -f "$TESTS/test_poc05_kind_probe.py" ]]; then
    kind_tests probe-suite "$TESTS/test_poc05_kind_probe.py"
  else
    echo "T10 | - | exception: in-pod probe not built (WIP) | notes/2026-10-09-t10-probe-exception.md"
    RESULT="every step ok; T10 recorded exception"
  fi
  step "5. Admission: every fixture through a server dry run"
  kind_tests admission \
    "$TESTS/test_poc05_kind_admission.py::test_fixture_outcome_matches_its_header"
  step "6. The code runner: run_python on gVisor, no egress"
  kind_tests code-runner \
    "$TESTS/test_poc05_kind_code_runner.py::test_code_runs_on_gvisor_with_no_egress"
  step "7. H11, the broker: Kafka with SASL"
  kind_tests kafka "$TESTS/test_poc05_kind_hardreq1.py" -k kafka
  step "8. Where it was logged (pod logs through redact_logs; no secret values)"
  "$RUN" logs 2>/dev/null | "$RUN" redact >"$TMP/logs.txt"
  count_lines "probe.check" 'probe\.check'
  # The remote chassis logs `remote_unauthenticated` to its telemetry port (`memory` here), not
  # stdout; its access log shows the remote listener's 401 (no or wrong token) and 403 (no run).
  count_lines "chassis-echo-remote 401/403" 'HTTP/1\.1" 40[13] ' chassis-echo-remote
  count_lines "LiteLLM 401" '" 401 |Authentication Error' litellm
  echo
  echo "result: $RESULT"
  echo '```'
} 2>&1 | tee "$OUT"
