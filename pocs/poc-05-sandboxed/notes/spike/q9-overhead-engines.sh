#!/usr/bin/env bash
# Q9 (exit criterion 9): gVisor overhead for the engines that run in the remote lane, on the
# PoC-5 cluster kind-poc05. Q7 measured an idle busybox; this measures the real images:
# echo-python (remote-echo) and code-runner. Each image runs as a plain Pod, once with
# `runtimeClassName: gvisor` and once on the default runc, in a scratch namespace this script
# creates and deletes. The pods carry the manifests' hardening (non-root, read-only root, drop ALL,
# RuntimeDefault seccomp), so PSA restricted admits them. No admission label: the platform's
# ValidatingAdmissionPolicy does not select this namespace.
#
# Per image and runtime:
# - Startup: wall time from before `kubectl apply` to the return of `kubectl wait Ready`, RUNS
#   times; each sample pod is deleted (and gone) before the next.
# - Latency: from a runc client pod in the same namespace (echo-python image, its own Python),
#   REQS GETs after 5 warmup requests, one new connection each: echo-python's agent card with the
#   bearer, code-runner's /health. For code-runner also CALLS `run_python` calls (`print(1)`, a
#   fresh idempotency key each), which fork a Python child in the sandbox.
# - Memory: the pod cgroup on the node after the requests and a 5 s settle, the bring-up item 10
#   method (notes/2026-10-02-bring-up.md, section 10): memory.current, working set
#   (current - inactive_file), memory.peak, in MiB. For gVisor that is the whole sandbox.
# - Kernel: the pod's `platform.release()`; gVisor reports its own (4.4.0), runc the node's.
#
# The echo token is a random value made here, piped into a Secret in the scratch namespace, and
# never printed. echo-typescript is not measured: it cannot be a remote yet (no inbound token
# check; blind-spots note, section 3).
#
# Run: pocs/poc-05-sandboxed/notes/spike/q9-overhead-engines.sh   (RUNS, REQS, CALLS override.)
set -euo pipefail

CTX=kind-poc05
NODE=poc05-control-plane
NS=poc05-q9-overhead
ECHO_IMAGE=kind.local/agent-platform/echo-python:poc05
RUNNER_IMAGE=kind.local/agent-platform/code-runner:poc05
RUNS="${RUNS:-5}"
REQS="${REQS:-50}"
CALLS="${CALLS:-10}"
CGROOT=/sys/fs/cgroup/kubelet.slice/kubelet-kubepods.slice

k() { kubectl --context "$CTX" "$@"; }
ms() { python3 -c 'import time; print(int(time.time() * 1000))'; }
median() { printf '%s\n' "$@" | sort -n | awk '{a[NR] = $1} END {print a[int((NR + 1) / 2)]}'; }
minmax() { printf '%s\n' "$@" | sort -n | awk 'NR == 1 {lo = $1} {hi = $1} END {print lo "-" hi}'; }

cleanup() { k delete namespace "$NS" --ignore-not-found --wait=false >/dev/null 2>&1 || true; }

k get --raw /readyz >/dev/null || { echo "no cluster at context $CTX" >&2; exit 1; }
docker inspect "$NODE" >/dev/null || { echo "no kind node container $NODE" >&2; exit 1; }
if k get namespace "$NS" >/dev/null 2>&1; then
  echo "namespace $NS exists (a run in progress, or one interrupted): delete it first" >&2
  exit 1
fi
trap cleanup EXIT

k apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: $NS
  labels:
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/enforce-version: latest
EOF
# Default deny, ingress and egress, plus same-namespace traffic only: no pod here, the runc
# code-runner included, has egress beyond the namespace. No DNS rule: the client addresses the
# pods by IP, and the kubelet's probes and `kubectl exec` do not cross a NetworkPolicy.
k apply -f - >/dev/null <<EOF
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny
  namespace: $NS
spec:
  podSelector: {}
  policyTypes: [Ingress, Egress]
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: same-namespace
  namespace: $NS
spec:
  podSelector: {}
  policyTypes: [Ingress, Egress]
  ingress: [{from: [{podSelector: {}}]}]
  egress: [{to: [{podSelector: {}}]}]
EOF
# This script makes its own random token in a scratch namespace, an exception to "Secrets come
# from the seed script only" (owner platform-security). It is piped through stdin, never in argv,
# authenticates nothing real, and is deleted with the namespace. Using the seed's token would copy
# a live credential.
openssl rand -hex 24 | tr -d '\n' \
  | k create secret generic q9-token -n "$NS" --from-file=token=/dev/stdin >/dev/null

hardening() { # uid
  cat <<EOF
      securityContext:
        readOnlyRootFilesystem: true
        runAsNonRoot: true
        runAsUser: $1
        runAsGroup: $1
        allowPrivilegeEscalation: false
        capabilities: {drop: [ALL]}
        seccompProfile: {type: RuntimeDefault}
EOF
}

pod_head() { # name runtimeClass(or empty) role
  cat <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: $1
  namespace: $NS
  labels: {spike: q9, q9-role: $3}
spec:
  ${2:+runtimeClassName: $2}
  automountServiceAccountToken: false
  enableServiceLinks: false
  terminationGracePeriodSeconds: 0
  securityContext:
    runAsNonRoot: true
    seccompProfile: {type: RuntimeDefault}
EOF
}

# echo-python as in deploy/kind/poc05/remote/remote-echo.yaml, minus the chassis URLs' use: the
# agent card needs no model.
echo_pod() { # name runtimeClass
  pod_head "$1" "$2" engine
  cat <<EOF
  containers:
    - name: workload
      image: $ECHO_IMAGE
      imagePullPolicy: Never
      args: ["--require-token-env", "CHASSIS_API_TOKEN", "--drain-timeout-s", "5"]
      env:
        - {name: POD_IP, valueFrom: {fieldRef: {fieldPath: status.podIP}}}
        - {name: HOST, value: \$(POD_IP)}
        - {name: PORT, value: "9000"}
        - {name: CHASSIS_MODEL_URL, value: "http://127.0.0.1:9/v1"}
        - {name: CHASSIS_API_TOKEN, valueFrom: {secretKeyRef: {name: q9-token, key: token}}}
      ports: [{containerPort: 9000}]
      readinessProbe: {tcpSocket: {port: 9000}, periodSeconds: 1}
      resources:
        requests: {cpu: 100m, memory: 128Mi}
        limits: {cpu: 500m, memory: 256Mi}
$(hardening 10002)
      volumeMounts: [{name: tmp, mountPath: /tmp}]
  volumes:
    - {name: tmp, emptyDir: {medium: Memory, sizeLimit: 32Mi}}
EOF
}

# code-runner as in deploy/kind/poc05/tools/code-runner.yaml.
runner_pod() { # name runtimeClass
  pod_head "$1" "$2" engine
  cat <<EOF
  containers:
    - name: code-runner
      image: $RUNNER_IMAGE
      imagePullPolicy: Never
      ports: [{containerPort: 8000}]
      readinessProbe: {httpGet: {path: /health, port: 8000}, periodSeconds: 1}
      resources:
        requests: {cpu: 100m, memory: 256Mi}
        limits: {cpu: 500m, memory: 256Mi}
$(hardening 10003)
      volumeMounts: [{name: tmp, mountPath: /tmp}, {name: shm, mountPath: /dev/shm}]
  volumes:
    - {name: tmp, emptyDir: {medium: Memory, sizeLimit: 64Mi}}
    - {name: shm, emptyDir: {medium: Memory, sizeLimit: 8Mi}}
EOF
}

client_pod() {
  pod_head q9-client "" client
  cat <<EOF
  containers:
    - name: client
      image: $ECHO_IMAGE
      imagePullPolicy: Never
      command: ["python", "-c", "import time; time.sleep(3600)"]
      env:
        - {name: CHASSIS_API_TOKEN, valueFrom: {secretKeyRef: {name: q9-token, key: token}}}
      resources:
        requests: {cpu: 100m, memory: 64Mi}
        limits: {cpu: 500m, memory: 128Mi}
$(hardening 10002)
EOF
}

# In the client: argv url n mode(get|bearer|mcp). Prints "p50_ms p95_ms errors". Warms up with
# 5 requests first. The token is read from the env here and never printed.
LATENCY='
import json, os, sys, time, urllib.request, uuid
url, n, mode = sys.argv[1], int(sys.argv[2]), sys.argv[3]
def one():
    headers, body = {}, None
    if mode == "bearer":
        headers["Authorization"] = "Bearer " + os.environ["CHASSIS_API_TOKEN"]
    if mode == "mcp":
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "run_python", "arguments": {"code": "print(1)", "timeout_s": 10},
            "_meta": {"idempotency_key": uuid.uuid4().hex}}}).encode()
    t0 = time.perf_counter()
    with urllib.request.urlopen(urllib.request.Request(url, body, headers), timeout=30) as r:
        r.read()
        ok = r.status == 200
    return (time.perf_counter() - t0) * 1000, ok
for _ in range(5):
    try:
        one()
    except Exception:
        pass
times, errors = [], 0
for _ in range(n):
    try:
        ms, ok = one()
        times.append(ms)
        errors += 0 if ok else 1
    except Exception:
        errors += 1
times.sort()
pick = lambda q: times[min(len(times) - 1, int(q * len(times)))] if times else float("nan")
print("%.1f %.1f %d" % (pick(0.50), pick(0.95), errors))
'

wait_ready() { k wait -n "$NS" --for=condition=Ready "pod/$1" --timeout=180s >/dev/null; }

startup_samples() { # make-fn runtimeClass rc-name image-tag
  local times=() name t0 t1 i
  for i in $(seq "$RUNS"); do
    name="q9-$4-$3-s$i"
    t0=$(ms)
    "$1" "$name" "$2" | k apply -f - >/dev/null
    wait_ready "$name"
    t1=$(ms)
    times+=($((t1 - t0)))
    k delete pod -n "$NS" "$name" --wait=true >/dev/null
  done
  echo "${times[*]}"
}

cgroup_mib() { # pod name: "current working_set peak" in MiB
  local uid dir
  uid=$(k get pod -n "$NS" "$1" -o jsonpath='{.metadata.uid}' | tr - _)
  dir=$(docker exec "$NODE" find "$CGROOT" -maxdepth 2 -type d -name "*-pod${uid}.slice")
  [ -n "$dir" ] || { echo "no pod cgroup for $1" >&2; echo "? ? ?"; return; }
  docker exec "$NODE" sh -c "cur=\$(cat $dir/memory.current); peak=\$(cat $dir/memory.peak); \
    inact=\$(awk '\$1==\"inactive_file\"{print \$2}' $dir/memory.stat); \
    echo \$((cur/1048576)) \$(((cur-inact)/1048576)) \$((peak/1048576))"
}

kernel() { # pod container
  k exec -n "$NS" "$1" -c "$2" -- python -c 'import platform; print(platform.release())'
}

client_run() { k exec -n "$NS" q9-client -c client -- python -c "$LATENCY" "$@"; }

echo "node load: $(docker exec "$NODE" cat /proc/loadavg)"
echo "runs=$RUNS reqs=$REQS calls=$CALLS"
client_pod | k apply -f - >/dev/null
wait_ready q9-client

ROWS=()
for engine in echo-python code-runner; do
  for rc in runc gvisor; do
    cls=""
    [ "$rc" = gvisor ] && cls=gvisor
    if [ "$engine" = echo-python ]; then
      make="echo_pod" container="workload" port=9000 path="/.well-known/agent-card.json"
      mode="bearer" tag="echo"
    else
      make="runner_pod" container="code-runner" port=8000 path="/health"
      mode="get" tag="runner"
    fi
    read -r -a starts <<<"$(startup_samples "$make" "$cls" "$rc" "$tag")"
    echo "$engine $rc startup ms: ${starts[*]}"

    name="q9-$tag-$rc"
    "$make" "$name" "$cls" | k apply -f - >/dev/null
    wait_ready "$name"
    ip=$(k get pod -n "$NS" "$name" -o jsonpath='{.status.podIP}')
    rel=$(kernel "$name" "$container")
    read -r p50 p95 errs <<<"$(client_run "http://$ip:$port$path" "$REQS" "$mode")"
    run="-"
    if [ "$engine" = code-runner ]; then
      read -r c50 c95 cerrs <<<"$(client_run "http://$ip:8000/mcp" "$CALLS" mcp)"
      run="$c50 / $c95 (err $cerrs)"
    fi
    sleep 5
    read -r cur ws peak <<<"$(cgroup_mib "$name")"
    ROWS+=("| $engine | $rc | $rel | $(median "${starts[@]}") ($(minmax "${starts[@]}")) \
| $p50 / $p95 (err $errs) | $run | $cur | $ws | $peak |")
    k delete pod -n "$NS" "$name" --wait=true >/dev/null
  done
done

echo
echo "| Engine | Runtime | Kernel | Ready ms, median (min-max), N=$RUNS \
| Request ms p50 / p95, N=$REQS | run_python ms p50 / p95, N=$CALLS \
| current MiB | working set MiB | peak MiB |"
echo "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"
printf '%s\n' "${ROWS[@]}"
echo
echo "Request: echo-python GET /.well-known/agent-card.json with the bearer; code-runner GET /health."
echo "Not measured: echo-typescript (no inbound token check, so not a remote yet; blind-spots note)."
