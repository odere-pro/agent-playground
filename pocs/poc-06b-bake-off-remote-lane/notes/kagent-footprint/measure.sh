#!/usr/bin/env bash
# The kagent-adk footprint on kind, for criterion 7 (footprint) of the PoC-6a scorecard:
# pocs/poc-06a-bake-off-sidecar-lane/notes/2026-10-09-scorecard.md.
#
# Usage: measure.sh <phase> <out-dir>     phase: idle | after-tests
#
# .github/workflows/poc06-kind.yml runs it after `deploy/kind/poc06/run.sh up` (idle) and after
# `run.sh test` (after-tests), then uploads <out-dir> as the artifact `kagent-adk-footprint`.
# It writes <out-dir>/<phase>.json and prints one `FOOTPRINT engine=kagent-adk ...` line.
#
# What it reads, and nothing else: `docker image inspect` of the image `run.sh build` made on the
# runner, and /proc/<pid>/status of each process in the workload container, through
# `kubectl exec` on context kind-poc05. No pod log, no environment, no Secret.
#
# - Image size: `.Size` in bytes, uncompressed, the same measure as `docker image ls`; MB is
#   10^6 bytes, as `docker image ls` prints it.
# - RSS: the sum of VmRSS over the container's processes, and the sum of VmHWM (each process's
#   peak). Under gVisor these are the sandbox kernel's own numbers for the workload. They leave
#   out the gVisor Sentry and Gofer on the node, so they are not the node's cost of the pod.
set -euo pipefail

CONTEXT=kind-poc05
NAMESPACE=poc05-remote
SELECTOR=app.kubernetes.io/name=remote-kagent-adk
CONTAINER=workload
IMAGE=kind.local/agent-platform/kagent-adk:kind

die() { echo "measure.sh: $*" >&2; exit 2; }

phase=${1:-}
out=${2:-}
case "$phase" in
  idle | after-tests) ;;
  *) die "usage: measure.sh idle|after-tests <out-dir>" ;;
esac
[[ -n "$out" ]] || die "usage: measure.sh idle|after-tests <out-dir>"

kctl() { kubectl --context "$CONTEXT" -n "$NAMESPACE" "$@"; }

bytes=$(docker image inspect --format '{{.Size}}' "$IMAGE")
[[ "$bytes" =~ ^[0-9]+$ ]] || die "image size of $IMAGE is not a number: $bytes"

pod=$(kctl get pods -l "$SELECTOR" -o jsonpath='{.items[0].metadata.name}')
[[ "$pod" =~ ^[a-z0-9.-]+$ ]] || die "no pod for $SELECTOR in $NAMESPACE"

# A process can exit between the glob and the read, so a missing file is skipped, not an error.
# shellcheck disable=SC2016 # expanded in the container, not here
status=$(kctl exec "$pod" -c "$CONTAINER" -- sh -c \
  'for f in /proc/[0-9]*/status; do cat "$f" 2>/dev/null || true; done')

read -r rss_kb hwm_kb processes < <(awk '
  /^VmRSS:/ { rss += $2; n++ }
  /^VmHWM:/ { hwm += $2 }
  END { printf "%d %d %d\n", rss, hwm, n }
' <<<"$status")
((processes > 0)) || die "no process with VmRSS in $pod/$CONTAINER"

image_mb=$((bytes / 1000000))
rss_mb=$(awk -v kb="$rss_kb" 'BEGIN { printf "%.1f", kb / 1024 }')
hwm_mb=$(awk -v kb="$hwm_kb" 'BEGIN { printf "%.1f", kb / 1024 }')

mkdir -p "$out"
printf '{"engine": "kagent-adk", "phase": "%s", "commit": "%s", "image": "%s", "image_bytes": %s, "image_mb": %s, "pod": "%s", "container": "%s", "processes": %s, "rss_kb": %s, "hwm_kb": %s}\n' \
  "$phase" "${GITHUB_SHA:-local}" "$IMAGE" "$bytes" "$image_mb" "$pod" "$CONTAINER" \
  "$processes" "$rss_kb" "$hwm_kb" >"$out/$phase.json"
echo "FOOTPRINT engine=kagent-adk phase=$phase image_mb=$image_mb rss_mb=$rss_mb hwm_mb=$hwm_mb processes=$processes"
