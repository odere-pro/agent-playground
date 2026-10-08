#!/usr/bin/env bash
# Q7: rough overhead, runc vs gVisor (systrap). Same busybox pod, image already on the node.
# Startup: wall time of `kubectl apply` + `kubectl wait Ready`, RUNS times each.
# Memory: the pod cgroup's memory.current on the node (sentry + gofer for gVisor; the shim is
# outside the pod cgroup in both cases), plus RSS of the pod's runsc processes on the host.
set -euo pipefail
k() { kubectl --context kind-poc05-spike "$@"; }
NODE=poc05-spike-control-plane
RUNS="${RUNS:-5}"
pod() { # name runtimeClass(or empty)
  cat <<EOF
apiVersion: v1
kind: Pod
metadata: {name: $1, labels: {spike: q7}}
spec:
  ${2:+runtimeClassName: $2}
  automountServiceAccountToken: false
  containers:
    - {name: c, image: "busybox:1.37", command: ["sleep", "3600"]}
EOF
}
ms() { python3 -c 'import time; print(int(time.time()*1000))'; }
for rc in runc gvisor; do
  cls=""; [ "$rc" = gvisor ] && cls=gvisor
  times=()
  for i in $(seq "$RUNS"); do
    name="ovh-$rc-$i"
    t0=$(ms); pod "$name" "$cls" | k apply -f - >/dev/null
    k wait --for=condition=Ready "pod/$name" --timeout=120s >/dev/null; t1=$(ms)
    times+=($((t1 - t0)))
  done
  echo "$rc startup ms: ${times[*]}"
  sleep 5
  for i in 1 2; do
    name="ovh-$rc-$i"
    uid=$(k get pod "$name" -o jsonpath='{.metadata.uid}' | tr - _)
    cg=/sys/fs/cgroup/kubelet.slice/kubelet-kubepods.slice/kubelet-kubepods-besteffort.slice/kubelet-kubepods-besteffort-pod$uid.slice
    docker exec "$NODE" sh -c "echo \"  $name pod cgroup memory.current=\$((\$(cat $cg/memory.current)/1024)) KiB\""
  done
done
echo "host processes (RSS KiB) per gVisor pod, summed over all gVisor pods:"
docker exec "$NODE" sh -c "ps -eo rss,comm | awk '/runsc|gvisor|containerd-shim/ {s[\$2]+=\$1; n[\$2]++} END {for (c in s) print \"  \" c, n[c], \"procs\", s[c], \"KiB\"}'"
echo "runc shims:"
docker exec "$NODE" sh -c "ps -eo rss,args | grep 'containerd-shim-runc-v2' | grep -v grep | awk '{s+=\$1; n++} END {print \"  \" n, \"procs\", s, \"KiB\"}'"
k delete pod -l spike=q7 --wait=false >/dev/null
