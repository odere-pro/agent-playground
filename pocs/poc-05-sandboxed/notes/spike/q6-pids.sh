#!/usr/bin/env bash
# Q6: does the kubelet podPidsLimit (256, cluster.yaml) cap processes in a runc pod and in a gVisor pod?
# Spawns up to 400 long sleeps inside each hardened pod (q6-hardened.yaml), counts how many run,
# then shows the pod cgroup's pids.max/pids.current on the node and whether the node still answers.
set -uo pipefail
k() { kubectl --context kind-poc05-spike "$@"; }
NODE=poc05-spike-control-plane
k delete -f "$(dirname "$0")/q6-hardened.yaml" --ignore-not-found --wait=true >/dev/null
k apply -f "$(dirname "$0")/q6-hardened.yaml" >/dev/null
k wait --for=condition=Ready pod/hard-gvisor pod/hard-runc --timeout=120s >/dev/null
for p in hard-runc hard-gvisor; do
  echo "== $p"
  uid=$(k get pod "$p" -o jsonpath='{.metadata.uid}' | tr - _)
  cg=/sys/fs/cgroup/kubelet.slice/kubelet-kubepods.slice/kubelet-kubepods-burstable.slice/kubelet-kubepods-burstable-pod$uid.slice
  kubectl --context kind-poc05-spike exec "$p" -- sh -c '
    ok=0; fail=0; n=0
    while [ $n -lt 400 ]; do
      if sleep 600 </dev/null >/dev/null 2>&1 & then ok=$((ok+1)); else fail=$((fail+1)); fi
      n=$((n+1))
    done
    sleep 3
    echo "spawn attempts=400 running sleeps=$(ps -o comm 2>/dev/null | grep -c "^sleep" || ps | grep -c "sleep 600")"
  ' 2>&1 | sort | uniq -c | sort -rn | head -4
  docker exec "$NODE" sh -c "echo host pod cgroup pids.max=\$(cat $cg/pids.max) pids.current=\$(cat $cg/pids.current)"
  k get pod "$p" --no-headers
done
echo "node still answers: $(k get --raw /readyz)"
