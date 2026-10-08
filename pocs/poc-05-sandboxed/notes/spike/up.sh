#!/usr/bin/env bash
# PoC-5 spike: create the poc05-spike kind cluster with gVisor, end to end.
#   up.sh        create cluster, install runsc, apply the gvisor RuntimeClass, smoke-test a pod
#   up.sh down   delete the cluster
set -euo pipefail
cd "$(dirname "$0")"
k() { kubectl --context kind-poc05-spike "$@"; }
if [ "${1:-up}" = down ]; then kind delete cluster --name poc05-spike; exit; fi
kind create cluster --config cluster.yaml
./install-gvisor.sh poc05-spike
k apply -f q1-gvisor.yaml
k wait --for=condition=Ready pod/gv-hello --timeout=180s
k exec gv-hello -- cat /proc/version          # expect: Linux version 4.19.0-gvisor
k exec gv-hello -- grep Seccomp /proc/self/status
# keep the RuntimeClass, drop the smoke pods
k delete pod gv-hello runc-hello --wait=false
