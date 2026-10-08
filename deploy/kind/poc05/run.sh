#!/usr/bin/env bash
# PoC-5 on kind: the cluster base (plan docs/plans/2026-10-02-poc-05-sandboxed.md, section 8).
#
#   deploy/kind/poc05/run.sh create   cluster poc05, gVisor in the node, base/, agent-sandbox
#   deploy/kind/poc05/run.sh smoke    runsc marker, PSA, default deny, agent-sandbox (exit 1 on a fail)
#   deploy/kind/poc05/run.sh status   nodes, RuntimeClass, namespaces, policies, controller, ANP API
#   deploy/kind/poc05/run.sh build    docker build the PoC-5 images (tag poc05)
#   deploy/kind/poc05/run.sh load     kind load the PoC-5 images
#   deploy/kind/poc05/run.sh admission  params, RBAC, policy, binding (in that order), then a canary
#   deploy/kind/poc05/run.sh workloads  tools/, remote/, agents/ as the platform deployer
#   deploy/kind/poc05/run.sh seed     seed.sh base, then seed.sh keys once LiteLLM is Ready
#   deploy/kind/poc05/run.sh apply    platform/, tools/, seed keys, remote/, agents/, each waited for
#   deploy/kind/poc05/run.sh request  normal requests through agent-echo and chassis-echo-remote
#   deploy/kind/poc05/run.sh up       all of it from nothing, in the plan's section 8 order; a
#                                     second `up` on a running cluster converges
#   deploy/kind/poc05/run.sh test     the kind tier: POC05_KIND=1 pytest -m network (PoC-5 tests)
#   deploy/kind/poc05/run.sh test-remote  the kind remote-lane and code-runner files (CI job); fails
#                                     naming any file in REMOTE_TESTS that does not exist yet
#   deploy/kind/poc05/run.sh with-gateway CMD...  run CMD with a port-forward to LiteLLM and
#                                     POC05_GATEWAY_MCP_URL, POC05_CHASSIS_VIRTUAL_KEY exported
#                                     (the key from its Secret, never in argv or output); last verb
#   deploy/kind/poc05/run.sh pods     every pod and Sandbox in the PoC-5 namespaces
#   deploy/kind/poc05/run.sh logs     every PoC-5 pod's last log lines, through redact_logs
#   deploy/kind/poc05/run.sh redact   redact_logs as a filter: stdin to stdout, no cluster call
#   deploy/kind/poc05/run.sh delete   delete the cluster (`down` is the same)
#   Several verbs run in order: run.sh create smoke
#
# `make kind-poc05 ARGS="<verb>"` calls this script. It touches only the kind cluster `poc05`: every
# kubectl call goes through `kctl`, which pins `--context kind-poc05`, and every kind call passes
# `--name poc05`. It never uses the current context. It refuses to create while `poc04` runs.
set -euo pipefail

CLUSTER=poc05
CONTEXT=kind-$CLUSTER
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
AGENT_SANDBOX_SHA256=e89fd95c0aa57609fa24be4112bd52ce67fe8939ecf6f3c17edf2f1e8f1eb860

# The images `build` makes and `load` puts in the node: "<image>|<Dockerfile>|<context>", paths
# relative to the repo root. To add one, append a line; both verbs pick it up.
# The names start with kind.local/agent-platform/, the registry prefix in admission/params.yaml:
# kind.local is a host no public registry serves, and every pod outside the remote lane sets
# imagePullPolicy: Never (admission rule 8), so only these loaded images run (security review F1).
REGISTRY=kind.local/agent-platform
IMAGES=(
  "$REGISTRY/chassis:poc05|packages/chassis/Dockerfile|."
  "$REGISTRY/fake-model-server:poc05|packages/fake-model-server/Dockerfile|."
  "$REGISTRY/fake-mcp-server:poc05|packages/fake-mcp-server/Dockerfile|."
  "$REGISTRY/code-runner:poc05|packages/code-runner/Dockerfile|."
  "$REGISTRY/echo-python:poc05|packages/workloads/echo-python/Dockerfile|."
  "$REGISTRY/echo-typescript:poc05|packages/workloads/echo-typescript/Dockerfile|packages/workloads/echo-typescript"
)

kctl() { kubectl --context "$CONTEXT" "$@"; }

# The one redaction filter for every `kubectl logs` output, here and in the CI failure step
# (security review 2026-10-02, item 2). LiteLLM's 401 line quotes the refused key's suffix and
# hash. Strips the value after `Bearer `, `Received API Key = `, `Key Hash (Token) = `, and any
# `sk-` token; keeps the text around it. sed -E only, so it runs on GNU and BSD sed.
redact_logs() {
  sed -E \
    -e 's/([Bb]earer )[^[:space:],;"]+/\1[redacted]/g' \
    -e 's/(Received API Key = )[^[:space:],;"]+/\1[redacted]/g' \
    -e 's/(Key Hash \(Token\) = )[^[:space:],;"]+/\1[redacted]/g' \
    -e 's/(^|[^[:alnum:]_-])sk-[^[:space:],;"]+/\1[redacted]/g'
}
log() { printf '[kind-poc05] %s\n' "$*" >&2; }
die() { log "$*"; exit 1; }

cluster_exists() { kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; }

preflight() {
  local tool
  for tool in docker kind kubectl; do
    command -v "$tool" >/dev/null || die "missing $tool"
  done
  # Memory: the VM has 7.75 GiB and the PoC-4 cluster takes about 1.1 GiB on its own.
  if [[ -n $(docker ps --filter "name=^poc04-control-plane$" --format '{{.Names}}') ]]; then
    die "the PoC-4 cluster poc04 is running; delete it first: deploy/kind/run.sh delete"
  fi
}

install_agent_sandbox() {
  local manifest=$HERE/base/agent-sandbox/upstream-v1.0.5.yaml sum
  sum=$(shasum -a 256 "$manifest" | cut -d' ' -f1)
  [[ $sum == "$AGENT_SANDBOX_SHA256" ]] || die "agent-sandbox manifest sha256 $sum, want $AGENT_SANDBOX_SHA256"
  kctl apply -k "$HERE/base/agent-sandbox"
  kctl wait --for=condition=Established crd/sandboxes.agents.x-k8s.io --timeout=60s
  kctl -n agent-sandbox-system rollout status deployment/agent-sandbox-controller --timeout=180s
}

create() {
  preflight
  local start=$SECONDS
  if cluster_exists; then
    log "cluster $CLUSTER exists"
  else
    kind create cluster --config "$HERE/cluster.yaml" --name "$CLUSTER" --wait 120s
    "$HERE/install-gvisor.sh" "$CLUSTER"
    kctl wait --for=condition=Ready node --all --timeout=120s
  fi
  kctl apply -k "$HERE/base"
  install_agent_sandbox
  log "create done in $((SECONDS - start)) s"
}

build() {
  local entry image dockerfile context
  for entry in "${IMAGES[@]}"; do
    IFS='|' read -r image dockerfile context <<<"$entry"
    docker build -t "$image" -f "$ROOT/$dockerfile" "$ROOT/$context"
  done
}

load() {
  local entry image
  for entry in "${IMAGES[@]}"; do
    image=${entry%%|*}
    kind load docker-image "$image" --name "$CLUSTER"
  done
}

DEPLOYER=system:serviceaccount:agent-platform-system:deployer

# The trust rule (admission/policy.yaml header). Order matters: params first (the binding denies
# everything without them), the policy before the binding (a policy that does not compile enforces
# nothing), and a canary last: the rule-1 fixture must be refused by agent-trust-rule and its twin
# admitted, so a silent no-op policy fails here.
admission() {
  local dir=$HERE/admission fixtures=$HERE/admission/fixtures/rule1-trust-label out i
  kctl apply -f "$dir/params.yaml"
  kctl apply -f "$dir/rbac.yaml"
  kctl apply -f "$dir/policy.yaml"
  # The type check runs in the background; status.typeChecking appears once it has (an empty
  # object: no warnings). Any expressionWarning stops here, before the binding.
  for i in $(seq 1 30); do
    out=$(kctl get validatingadmissionpolicy agent-trust-rule -o json |
      jq -c '.status | select(has("typeChecking")) | .typeChecking') && [[ -n $out ]] && break
    sleep 1
  done
  log "admission: status.typeChecking = ${out:-<not set after 30 s>}"
  [[ $out == "{}" || $out == '{"expressionWarnings":[]}' ]] ||
    die "admission: the policy has type warnings or was not checked: ${out:-none}"
  kctl apply -f "$dir/binding.yaml"
  # The rule-1 fixture is in poc05-agents, where only the deployer may create pods (review F2;
  # the kind test picks the identity the same way). Retry briefly: the binding reaches the API
  # server's informer within seconds.
  for i in $(seq 1 15); do
    if out=$(kctl apply --dry-run=server --as="$DEPLOYER" -f "$fixtures/rejected.yaml" 2>&1); then
      sleep 2
      continue
    fi
    [[ $out == *agent-trust-rule* ]] || die "canary refused by something else: $out"
    kctl apply --dry-run=server --as="$DEPLOYER" -f "$fixtures/admitted.yaml" >/dev/null ||
      die "canary twin refused"
    log "admission: canary refused by agent-trust-rule, twin admitted (try $i)"
    return 0
  done
  die "admission: the canary was admitted; the policy is not enforcing"
}

# The workload folders, applied as the platform deployer (admission/rbac.yaml), never as a tenant:
# chassis pods and their Secrets live in poc05-agents, where the submitter has no rights (review
# F2). Needs admission, platform/, and seed.sh first; remote/ before agents/ (agents/
# kustomization.yaml).
workloads() {
  apply_folder tools
  apply_folder remote
  apply_folder agents
}

# --- bring-up: platform, seed, workloads, requests (plan section 8) ----------------------------

PLATFORM_NS=poc05-platform
NAMESPACES=(poc05-platform poc05-tools poc05-remote poc05-agents agent-sandbox-system)
SEED=$HERE/platform/seed.sh

# apply_folder FOLDER: remote/, tools/, agents/ go in as the platform deployer only (review F2).
apply_folder() { kctl apply -k "$HERE/$1" --as="$DEPLOYER"; }

# On a failed wait: the object's events and the last log lines, never a Secret.
explain() {
  local ns=$1 sel=$2
  log "not ready: -n $ns -l $sel; events and logs follow"
  kctl -n "$ns" get pods -l "$sel" -o wide || true
  kctl -n "$ns" get events --sort-by=.lastTimestamp | tail -n 15 || true
  kctl -n "$ns" logs -l "$sel" --all-containers --tail=30 2>&1 | redact_logs || true
}

wait_rollout() {
  local ns=$1 name=$2 timeout=${3:-180s}
  kctl -n "$ns" rollout status "deployment/$name" --timeout="$timeout" ||
    { explain "$ns" "app.kubernetes.io/name=$name"; die "deployment $ns/$name not ready"; }
}

wait_sandbox() {
  local ns=$1 name=$2
  kctl -n "$ns" wait --for=condition=Ready "sandbox/$name" --timeout=180s ||
    { explain "$ns" "app.kubernetes.io/name=$name"; die "sandbox $ns/$name not ready"; }
}

litellm_ready() {
  [[ $(kctl -n "$PLATFORM_NS" get deployment litellm \
    -o jsonpath='{.status.readyReplicas}' 2>/dev/null) == 1 ]]
}

# replace_changed_job NAME: delete platform Job NAME only if it finished and its manifest no
# longer applies (the template is immutable). A running Job is never touched.
replace_changed_job() {
  local name=$1 out finished
  finished=$(kctl -n "$PLATFORM_NS" get job "$name" --ignore-not-found \
    -o jsonpath='{.status.conditions[?(@.status=="True")].type}')
  [[ $finished == *Complete* || $finished == *Failed* ]] || return 0
  out=$(kctl apply -k "$HERE/platform" -l "app.kubernetes.io/name=$name" --dry-run=server 2>&1) &&
    return 0
  [[ $out == *"field is immutable"* ]] || die "job $name: dry run failed: ${out:0:300}"
  log "job $name: finished and its manifest changed; deleting it so apply makes it again"
  kctl -n "$PLATFORM_NS" delete job "$name" --wait=true --timeout=60s
}

seed() {
  "$SEED" base
  if litellm_ready; then
    "$SEED" keys
  else
    log "seed: LiteLLM not Ready yet; keys come in \`apply\` (seed.sh keys)"
  fi
}

# Platform services in section 8 order: Postgres, then LiteLLM (it runs its migrations on start),
# then the fake servers, Valkey, MinIO and its one-shot Job. A Job is immutable: a finished one is
# left alone, a missing one (ttl after 600 s) is made again, and its steps are idempotent. A
# finished one whose manifest changed is deleted first, so a second `up` still converges.
apply_platform() {
  replace_changed_job minio-init
  kctl apply -k "$HERE/platform"
  wait_rollout "$PLATFORM_NS" postgres
  wait_rollout "$PLATFORM_NS" litellm 300s
  local name
  for name in fake-model-server fake-mcp-server valkey minio; do
    wait_rollout "$PLATFORM_NS" "$name"
  done
  kctl -n "$PLATFORM_NS" wait --for=condition=Complete job/minio-init --timeout=120s ||
    { explain "$PLATFORM_NS" app.kubernetes.io/name=minio-init; die "minio-init did not finish"; }
}

# The code-runner Sandbox; LiteLLM's config names it (platform/litellm/config.yaml, mcp_servers),
# so "registering" it is LiteLLM reaching it: the Sandbox is Ready and LiteLLM's NetworkPolicy has
# the edge. The tool list itself is checked through a chassis in `request`.
apply_tools() {
  apply_folder tools
  wait_sandbox poc05-tools code-runner
}

apply_remote() {
  apply_folder remote
  wait_sandbox poc05-remote remote-echo
}

apply_agents() {
  apply_folder agents
  wait_rollout poc05-agents agent-echo
  wait_rollout poc05-agents chassis-echo-remote
}

apply_all() {
  apply_platform
  apply_tools
  "$SEED" keys
  apply_remote
  apply_agents
}

# --- one normal request per lane ---------------------------------------------------------------

# The chassis binds its public port to the pod IP only (B6), and `kubectl port-forward` dials
# 127.0.0.1 inside the pod, so the request is sent from inside the chassis container to
# $POD_IP:8080 with the image's Python. Nothing secret is in the command or the output.
REQUEST_PY='
import json, os, sys, urllib.error, urllib.request
body = json.dumps({"input": {"text": sys.argv[1]}}).encode()
url = "http://%s:8080/v1/run" % os.environ["POD_IP"]
req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        code, raw = r.status, r.read()
except urllib.error.HTTPError as e:
    code, raw = e.code, e.read()
print(code)
print(raw.decode(errors="replace"))
'

REQUEST_FAILS=0
# run_one SVC TEXT: POST /v1/run on the chassis's public port; prints the status and a summary.
run_one() {
  local svc=$1 text=$2 out code body
  out=$(kctl -n poc05-agents exec "deploy/$svc" -c chassis -- python -c "$REQUEST_PY" "$text" 2>&1) ||
    true
  code=$(head -n 1 <<<"$out")
  body=$(tail -n +2 <<<"$out")
  printf '%-20s %-10s HTTP %s  %s\n' "$svc" "\"$text\"" "$code" \
    "$(jq -c '{status, output, error}' <<<"$body" 2>/dev/null || head -c 400 <<<"$body")"
  [[ $code == 200 ]] || REQUEST_FAILS=$((REQUEST_FAILS + 1))
}

# "hello": the fake model's default reply. "glossary": the fake model asks for glossary_lookup, so
# the tool path runs once (workload -> chassis /mcp -> LiteLLM MCP gateway -> fake_tools).
request() {
  REQUEST_FAILS=0
  local svc
  for svc in agent-echo chassis-echo-remote; do
    run_one "$svc" hello
    run_one "$svc" glossary
  done
  ((REQUEST_FAILS == 0)) || die "request: $REQUEST_FAILS request(s) failed"
}

pods() {
  kctl get pods -n poc05-platform -o wide
  local ns
  for ns in "${NAMESPACES[@]:1}"; do
    kctl get pods -n "$ns" -o wide --no-headers
  done
  kctl get sandbox -A
}

# Preflight for `up`: poc04 is down (create checks it), the tools are here, and Docker has room.
# The plan's 6 GiB free assumes the other stacks on this VM are stopped; this run never stops
# them, so it warns under 6 GiB and refuses under 3 GiB (suggested).
up_preflight() {
  local tool total used free
  for tool in jq openssl curl shasum; do
    command -v "$tool" >/dev/null || die "missing $tool"
  done
  total=$(docker info --format '{{.MemTotal}}')
  # Our own node does not count: a second `up` on a running cluster must not refuse itself.
  used=$(docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' |
    awk -v own="$CLUSTER-control-plane" '$1 != own {print $2}' | awk '{
      v = $1; u = v; gsub(/[0-9.]/, "", u); sub(/[A-Za-z]+$/, "", v)
      m = (u == "GiB") ? 1073741824 : (u == "MiB") ? 1048576 : (u == "KiB") ? 1024 : 1
      s += v * m } END { printf "%d", s }')
  free=$(((total - used) / 1048576))
  log "Docker memory: $((total / 1048576)) MiB total, $free MiB not used by running containers"
  ((free >= 3072)) || die "under 3 GiB free in Docker; stop something you own first"
  ((free >= 6144)) || log "warning: under 6 GiB free (plan section 8); bring-up may be tight"
}

up() {
  local start=$SECONDS
  up_preflight
  create
  smoke
  admission
  build
  load
  "$SEED" base
  apply_platform
  apply_tools
  "$SEED" keys
  apply_remote
  apply_agents
  pods
  request
  log "up done in $((SECONDS - start)) s"
}

run_tests() {
  (cd "$ROOT" && POC05_KIND=1 uv run pytest -m network pocs/poc-05-sandboxed/tests -q -rs)
}

# The kind remote-lane and code-runner tests, selected by file path (review B1: a `-k` match
# picked only admission fixture ids). No admission file belongs here.
REMOTE_TESTS=(
  pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_lane.py
  pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_controls.py
  pocs/poc-05-sandboxed/tests/test_poc05_kind_code_runner.py
)

# The remote-lane CI job (.github/workflows/remote-lane.yml) runs this after `up`. A missing file
# fails the verb by name, so the job never goes green on an empty selection. The code-runner file
# calls the tool through the gateway, so the run goes through with_gateway (it skips without it).
run_tests_remote() {
  local f missing=()
  for f in "${REMOTE_TESTS[@]}"; do
    [[ -f $ROOT/$f ]] || missing+=("$f")
  done
  ((${#missing[@]} == 0)) ||
    die "test-remote: kind remote tests not written yet, nothing run: ${missing[*]}"
  (cd "$ROOT" && with_gateway env POC05_KIND=1 uv run pytest -m network "${REMOTE_TESTS[@]}" -q -rs)
}

# with_gateway CMD...: the kind tests that call the gateway from the host (test_poc05_kind_*.py)
# need its URL and the chassis's virtual key. Port-forward LiteLLM on a free local port, export
# both, run CMD, then stop the forward. The key goes from the Secret into this process's env only:
# never into argv, a file, or the output.
with_gateway() {
  (($# > 0)) || die "with-gateway: no command given"
  local pf_log pf port="" i
  pf_log=$(mktemp)
  kctl -n "$PLATFORM_NS" port-forward svc/litellm :4000 >"$pf_log" 2>&1 &
  pf=$!
  # shellcheck disable=SC2064 # expand now: the trap runs after the locals are gone
  trap "kill $pf 2>/dev/null || true; rm -f '$pf_log'" EXIT
  for i in $(seq 1 50); do
    port=$(sed -nE 's/^Forwarding from 127\.0\.0\.1:([0-9]+).*/\1/p' "$pf_log" | head -n 1)
    [[ -n $port ]] && break
    sleep 0.2
  done
  [[ -n $port ]] || die "with-gateway: the port-forward to litellm did not start"
  export POC05_GATEWAY_MCP_URL="http://127.0.0.1:$port/mcp/"
  POC05_CHASSIS_VIRTUAL_KEY=$(kctl -n poc05-agents get secret chassis-echo-litellm \
    -o 'jsonpath={.data.LITELLM_API_KEY}' | base64 -d)
  [[ -n $POC05_CHASSIS_VIRTUAL_KEY ]] || die "with-gateway: secret chassis-echo-litellm is empty"
  export POC05_CHASSIS_VIRTUAL_KEY
  "$@"
}

anp_api() {
  # The AdminNetworkPolicy API (policy.networking.k8s.io). kind ships no CRD for it.
  if kctl api-resources --api-group=policy.networking.k8s.io -o name 2>/dev/null |
    grep -q adminnetworkpolicies; then
    echo present
  else
    echo absent
  fi
}

status() {
  kctl get nodes -o wide
  kctl get runtimeclass
  kctl get namespaces -l agents.platform/poc=05 --show-labels
  kctl get networkpolicy -A
  kctl -n agent-sandbox-system get deployment,pod -o wide
  echo "AdminNetworkPolicy API: $(anp_api)"
}

# --- smoke -----------------------------------------------------------------------------------
# Each check prints one PASS or FAIL line with the evidence. To add a check: write a function that
# echoes its evidence and returns non-zero on a fail, then call `check "<what>" <function>` in
# smoke(). Objects it needs go in smoke/smoke.yaml, labeled agents.platform/smoke=true.

FAILS=0
check() {
  local what=$1 out rc=0
  shift
  out=$("$@" 2>&1) || rc=$?
  if ((rc == 0)); then
    printf 'PASS  %s\n' "$what"
  else
    printf 'FAIL  %s\n' "$what"
    FAILS=$((FAILS + 1))
  fi
  [[ -z $out ]] || printf '        %s\n' "${out//$'\n'/$'\n        '}"
}

SMOKE_NS=(poc05-smoke)

smoke_clean() {
  local ns
  for ns in "${SMOKE_NS[@]}"; do
    kctl -n "$ns" delete sandbox,pod,networkpolicy -l agents.platform/smoke=true \
      --ignore-not-found --wait=true --timeout=60s >/dev/null
  done
}

proc_version() { kctl -n "$1" exec "$2" -- cat /proc/version; }

c_gvisor_marker() {
  local v
  v=$(proc_version poc05-smoke smoke-gvisor)
  echo "smoke-gvisor (runtimeClassName gvisor): $v"
  kctl -n poc05-smoke get pod smoke-gvisor -o jsonpath='runtimeClassName={.spec.runtimeClassName}{"\n"}'
  [[ $v == *gvisor* ]]
}

c_runc_no_marker() {
  local v
  v=$(proc_version poc05-smoke smoke-runc)
  echo "smoke-runc (default runtime): $v"
  [[ $v != *gvisor* ]]
}

c_seccomp_in_gvisor() {
  local s
  s=$(kctl -n poc05-smoke exec smoke-gvisor -- grep Seccomp: /proc/self/status)
  echo "smoke-gvisor $s (2 = filter; oci-seccomp honors RuntimeDefault)"
  [[ $s == *2 ]]
}

c_psa_rejects_root() {
  local out rc=0
  out=$(kctl apply --dry-run=server -f "$HERE/smoke/root-pod.yaml" 2>&1) || rc=$?
  echo "$out"
  ((rc != 0)) && [[ $out == *"violates PodSecurity \"restricted"* ]]
}

c_psa_admits_hardened() {
  # The admitted twin: the hardened pods in smoke.yaml exist, so PSA let them in.
  kctl -n poc05-smoke get pod smoke-runc -o jsonpath='smoke-runc admitted, phase={.status.phase}{"\n"}'
}

wget_from() {
  # wget from pod $1 to the server pod's IP; prints the body or the error.
  local ip
  ip=$(kctl -n poc05-smoke get pod smoke-server -o jsonpath='{.status.podIP}')
  kctl -n poc05-smoke exec "$1" -- wget -q -T 3 -O - "http://$ip:8080/" 2>&1
}

c_netpol_allowed() {
  local out rc=0
  out=$(wget_from smoke-client) || rc=$?
  echo "smoke-client -> smoke-server:8080 (allowed by smoke-allow-*): rc=$rc $out"
  ((rc == 0)) && [[ $out == ok ]]
}

c_netpol_denied() {
  local out rc=0
  out=$(wget_from smoke-other) || rc=$?
  echo "smoke-other -> smoke-server:8080 (default deny only): rc=$rc $out"
  ((rc != 0))
}

c_controller_ready() {
  kctl -n agent-sandbox-system get deployment agent-sandbox-controller \
    -o jsonpath='ready={.status.readyReplicas}/{.spec.replicas} image={.spec.template.spec.containers[0].image}{"\n"}'
  kctl -n agent-sandbox-system wait --for=condition=Available deployment/agent-sandbox-controller \
    --timeout=60s
}

c_sandbox_gvisor() {
  local v
  kctl -n poc05-smoke wait --for=condition=Ready sandbox/smoke-sandbox --timeout=120s
  kctl -n poc05-smoke get sandbox smoke-sandbox
  kctl -n poc05-smoke wait --for=condition=Ready pod/smoke-sandbox --timeout=60s >/dev/null
  kctl -n poc05-smoke get pod smoke-sandbox \
    -o jsonpath='pod runtimeClassName={.spec.runtimeClassName} owner={.metadata.ownerReferences[0].kind}{"\n"}'
  v=$(proc_version poc05-smoke smoke-sandbox)
  echo "smoke-sandbox: $v"
  [[ $v == *gvisor* ]]
}

smoke() {
  cluster_exists || die "no cluster $CLUSTER; run: $0 create"
  smoke_clean
  kctl apply -f "$HERE/smoke/smoke.yaml" >/dev/null
  local p
  for p in poc05-smoke/smoke-runc poc05-smoke/smoke-client poc05-smoke/smoke-other \
    poc05-smoke/smoke-server poc05-smoke/smoke-gvisor; do
    kctl -n "${p%%/*}" wait --for=condition=Ready "pod/${p#*/}" --timeout=180s >/dev/null
  done
  FAILS=0
  check "runsc pod shows the gVisor marker" c_gvisor_marker
  check "runc pod does not" c_runc_no_marker
  check "seccomp RuntimeDefault applies inside gVisor" c_seccomp_in_gvisor
  check "PSA restricted rejects a root pod" c_psa_rejects_root
  check "PSA restricted admits the hardened twin" c_psa_admits_hardened
  check "default deny blocks pod-to-pod (poc05-smoke, same policy as every PoC-5 namespace)" c_netpol_denied
  check "an explicitly allowed pair connects (control)" c_netpol_allowed
  check "agent-sandbox controller is Ready" c_controller_ready
  check "a Sandbox with runtimeClassName gvisor runs" c_sandbox_gvisor
  echo "AdminNetworkPolicy API: $(anp_api)"
  smoke_clean
  if ((FAILS > 0)); then
    die "smoke: $FAILS check(s) failed"
  fi
  log "smoke: all checks passed"
}

# Every PoC-5 pod's last 200 log lines, each through redact_logs (the CI failure step).
pod_logs() {
  local ns pod
  for ns in $(kctl get namespaces -o name 2>/dev/null | sed 's|^namespace/||' | grep '^poc05-'); do
    kctl -n "$ns" get pods -o wide 2>&1 || true
    for pod in $(kctl -n "$ns" get pods -o name 2>/dev/null); do
      printf '::group::%s/%s\n' "$ns" "${pod#pod/}"
      kctl -n "$ns" logs "$pod" --all-containers --tail=200 2>&1 | redact_logs || true
      printf '::endgroup::\n'
    done
  done
}

usage() { sed -n '2,30p' "$0"; }

(($# > 0)) || { usage; exit 0; }
# `with-gateway` takes the rest of the line as its command, so it ends the verb list.
if [[ $1 == with-gateway ]]; then
  shift
  with_gateway "$@"
  exit
fi
for cmd in "$@"; do
  case $cmd in
    create) create ;;
    smoke) smoke ;;
    status) status ;;
    build) build ;;
    load) load ;;
    admission) admission ;;
    workloads) workloads ;;
    seed) seed ;;
    apply) apply_all ;;
    request) request ;;
    up) up ;;
    test) run_tests ;;
    test-remote) run_tests_remote ;;
    pods) pods ;;
    logs) pod_logs ;;
    redact) redact_logs ;;
    delete | down) kind delete cluster --name "$CLUSTER" ;;
    *) usage; exit 2 ;;
  esac
done
