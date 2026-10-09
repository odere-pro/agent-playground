#!/usr/bin/env bash
# PoC-6 on kind: the bake-off engines in the PoC-5 cluster (plan docs/plans/2026-10-09-poc-06-bake-off.md,
# tasks B2 to B5). It reuses the PoC-5 cluster, namespaces, gVisor, agent-sandbox, platform, and
# admission rules, and adds the PoC-6 engines on top. It edits no PoC-5 file: it calls
# deploy/kind/poc05/run.sh for the bring-up, the logs, the redaction filter, and the delete.
#
#   deploy/kind/poc06/run.sh up       PoC-5 `up`, then build and load the PoC-6 images (kagent-adk from source), extend the
#                                     trusted repositories, switch the fake model's script, seed the
#                                     PoC-6 keys and tokens, apply remote/, agents/, and (with a
#                                     kagent/, each waited for. A second `up` converges
#   deploy/kind/poc06/run.sh build    docker build the PoC-6 images (tag poc06), kagent-adk included
#   deploy/kind/poc06/run.sh load     kind load the PoC-6 images
#   deploy/kind/poc06/run.sh seed     seed.sh keys (needs LiteLLM Ready)
#   deploy/kind/poc06/run.sh seccomp  derive the gVisor seccomp profile from PoC-5's running remote-echo
#                                     pod and write it to every kind node (`up` and `apply` run it first)
#   deploy/kind/poc06/run.sh apply    admission params, fake model script, seed, remote/, agents/,
#                                     kagent/ (no build, no PoC-5 bring-up)
#   deploy/kind/poc06/run.sh test     the kind tests: POC06_KIND=1 pytest -m kind (PoC-6b tests)
#   deploy/kind/poc06/run.sh pods     every pod and Sandbox in the PoC-5 namespaces
#   deploy/kind/poc06/run.sh logs     every pod's last log lines, through PoC-5's redact_logs
#   deploy/kind/poc06/run.sh redact   PoC-5's redact_logs as a filter: stdin to stdout
#   deploy/kind/poc06/run.sh delete   delete the cluster (`down` is the same)
#   Several verbs run in order: run.sh build load
#
# `make kind-poc06 ARGS="<verb>"` calls this script. The cluster is PoC-5's: `kind-poc05`. Every
# kubectl call goes through `kctl`, which pins that context; it never uses the current context.
#
# What PoC-6 changes in the live cluster, and why (none of it is a PoC-5 file):
# - `trustedRepositories` gets echo-openai-agents (admission/params.yaml). A trusted sidecar pod runs
#   only listed repositories (rule 4). Applied as the cluster admin, as PoC-5 applies its params.
# - The fake model server runs platform/fake-model-script.yaml, not the example script. The
#   bake-off tasks need rules the example script lacks, and its catch-all `after_tool` rule would
#   swallow the lookup loop. This cluster state is for the PoC-6 kind tests; the PoC-5 kind suite
#   needs a PoC-5-only cluster (`poc05/run.sh up` alone).
#
# The gVisor seccomp profile (seccomp/derive_profile.py, `seccomp` verb): the nodes run runsc with
# oci-seccomp, and runsc turns every SCMP_ACT_ERRNO rule into EPERM, so RuntimeDefault's clone3 rule
# (ENOSYS in containerd) breaks `pthread_create` in every multi-threaded gVisor pod. The profile
# `poc06-runsc-clone3.json` is the node's own RuntimeDefault with that one rule allowed. The PoC-6
# gVisor pods use it as a Localhost profile. Note: notes/2026-10-09-lanes-b-kind.md, "gVisor and
# clone3". Remove it when runsc has google/gvisor#14721.
#
# kagent-adk (the remote solution, kagent/): built from kagent's own source, not pulled. `build_kagent`
# clones https://github.com/kagent-dev/kagent, checks out the commit in kagent/source.commit, fails
# closed unless `git rev-parse HEAD` equals it, and builds python/Dockerfile as
# kind.local/agent-platform/kagent-adk:poc06. That Dockerfile does not pin its base images by digest
# (debian:bookworm-slim, the uv image by tag); an accepted, recorded gap for a PoC remote (see
# notes/2026-10-09-lanes-b-kind.md). To add another plain-A2A remote, copy kagent/ (a Sandbox, a
# chassis with `spec.engine.protocol: a2a`, a ConfigMap) and add it below.
set -euo pipefail

CLUSTER=poc05
CONTEXT=kind-$CLUSTER
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
P5=$ROOT/deploy/kind/poc05/run.sh
SEED=$HERE/seed.sh
DEPLOYER=system:serviceaccount:agent-platform-system:deployer
PLATFORM_NS=poc05-platform
AGENTS_NS=poc05-agents
REMOTE_NS=poc05-remote
KAGENT_COMMIT_FILE=$HERE/kagent/source.commit
KAGENT_REPO=https://github.com/kagent-dev/kagent
KAGENT_IMAGE=kind.local/agent-platform/kagent-adk:poc06
SECCOMP_DERIVE=$HERE/seccomp/derive_profile.py
SECCOMP_DIR=/var/lib/kubelet/seccomp/profiles
SECCOMP_FILE=poc06-runsc-clone3.json
SOURCE_POD_LABEL=app.kubernetes.io/name=remote-echo

# The images `build` makes and `load` puts in the node: "<image>|<Dockerfile>|<context>", relative
# to the repo root. echo-typescript is PoC-5's image (kind.local/agent-platform/echo-typescript:poc05,
# built from the same source by `poc05/run.sh build`), so it is not repeated here.
REGISTRY=kind.local/agent-platform
IMAGES=(
  "$REGISTRY/echo-openai-agents:poc06|packages/workloads/echo-openai-agents/Dockerfile|."
  "$REGISTRY/echo-smolagents:poc06|packages/workloads/echo-smolagents/Dockerfile|."
  "$REGISTRY/echo-claude-agent:poc06|packages/workloads/echo-claude-agent/Dockerfile|."
)

# What `apply` waits for.
SANDBOXES=(remote-smolagents remote-claude-agent remote-typescript)
DEPLOYMENTS=(
  agent-openai-agents
  agent-typescript
  chassis-smolagents-remote
  chassis-claude-agent-remote
  chassis-typescript-remote
)
# The kind test files `test` runs; it fails naming any that is missing.
KIND_TESTS=(
  pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_kind_engines.py
  pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_kind_remote_controls.py
  pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_kind_sidecar_controls.py
)

kctl() { kubectl --context "$CONTEXT" "$@"; }
log() { printf '[kind-poc06] %s\n' "$*" >&2; }
die() { log "$*"; exit 1; }

# kagent_commit: the pinned commit, the first 40-hex line of kagent/source.commit.
kagent_commit() {
  local commit
  commit=$(grep -E '^[0-9a-f]{40}$' "$KAGENT_COMMIT_FILE" | head -n 1 || true)
  [[ -n $commit ]] || die "no 40-hex commit in $KAGENT_COMMIT_FILE"
  printf '%s\n' "$commit"
}

# build_kagent: kagent-adk from kagent's source at the pinned commit. The clone goes to its own
# new empty directory, and its path is an argument to git and docker; nothing runs from inside it.
# Fails closed unless HEAD is exactly the pinned commit.
build_kagent() {
  local commit dir head
  commit=$(kagent_commit)
  dir=$(mktemp -d)
  # shellcheck disable=SC2064 # expand now: the trap runs after the locals are gone
  trap "rm -rf '$dir'" RETURN
  git clone --quiet --filter=blob:none "$KAGENT_REPO" "$dir/kagent"
  git -C "$dir/kagent" checkout --quiet --detach "$commit"
  head=$(git -C "$dir/kagent" rev-parse HEAD)
  [[ $head == "$commit" ]] || die "kagent: HEAD is $head, want $commit; refusing to build"
  docker build -f "$dir/kagent/python/Dockerfile" -t "$KAGENT_IMAGE" "$dir/kagent/python"
}

build() {
  local entry image dockerfile context
  for entry in "${IMAGES[@]}"; do
    IFS='|' read -r image dockerfile context <<<"$entry"
    docker build -t "$image" -f "$ROOT/$dockerfile" "$ROOT/$context"
  done
  build_kagent
}

load() {
  local entry image
  for entry in "${IMAGES[@]}"; do
    image=${entry%%|*}
    kind load docker-image "$image" --name "$CLUSTER"
  done
  kind load docker-image "$KAGENT_IMAGE" --name "$CLUSTER"
}

# On a failed wait: the object's events and the last log lines, redacted, never a Secret.
explain() {
  local ns=$1 sel=$2
  log "not ready: -n $ns -l $sel; events and logs follow"
  kctl -n "$ns" get pods -l "$sel" -o wide || true
  kctl -n "$ns" get events --sort-by=.lastTimestamp | tail -n 15 || true
  kctl -n "$ns" logs -l "$sel" --all-containers --tail=30 2>&1 | "$P5" redact || true
}

wait_rollout() {
  local ns=$1 name=$2 timeout=${3:-240s}
  kctl -n "$ns" rollout status "deployment/$name" --timeout="$timeout" ||
    { explain "$ns" "app.kubernetes.io/name=$name"; die "deployment $ns/$name not ready"; }
}

wait_sandbox() {
  local ns=$1 name=$2
  kctl -n "$ns" wait --for=condition=Ready "sandbox/$name" --timeout=240s ||
    { explain "$ns" "app.kubernetes.io/name=$name"; die "sandbox $ns/$name not ready"; }
}

# Rule 4's list, extended by one repository (admission/params.yaml). Cluster admin, like PoC-5.
apply_params() {
  kctl apply -f "$HERE/admission/params.yaml"
}

# The fake model's script: a ConfigMap, then the patch that mounts it and points the server at it.
# The script's sha256 is a pod annotation, so a changed script restarts the pod.
apply_fake_model() {
  local sum
  sum=$(shasum -a 256 "$HERE/platform/fake-model-script.yaml" | cut -d' ' -f1)
  kctl -n "$PLATFORM_NS" create configmap fake-model-script-poc06 \
    --from-file=script.yaml="$HERE/platform/fake-model-script.yaml" --dry-run=client -o yaml |
    kctl apply -f -
  kctl -n "$PLATFORM_NS" patch deployment fake-model-server \
    --patch-file "$HERE/platform/fake-model-server-patch.yaml"
  kctl -n "$PLATFORM_NS" patch deployment fake-model-server -p \
    "$(jq -nc --arg s "$sum" '{spec: {template: {metadata: {annotations: {"poc06/script-sha256": $s}}}}}')"
  wait_rollout "$PLATFORM_NS" fake-model-server
}

# apply_folder FOLDER: remote/ and agents/ go in as the platform deployer only (PoC-5 review F2).
apply_folder() { kctl apply -k "$HERE/$1" --as="$DEPLOYER"; }

apply_remote() {
  local name
  apply_folder remote
  for name in "${SANDBOXES[@]}"; do
    wait_sandbox "$REMOTE_NS" "$name"
  done
}

apply_agents() {
  local name
  apply_folder agents
  for name in "${DEPLOYMENTS[@]}"; do
    wait_rollout "$AGENTS_NS" "$name"
  done
}

apply_kagent() {
  apply_folder kagent
  wait_sandbox "$REMOTE_NS" remote-kagent-adk
  wait_rollout "$AGENTS_NS" chassis-kagent-adk-remote
}

# Python run on the source pod's JSON: it is the PoC-5 remote-echo Sandbox's pod, on gVisor, and its
# workload container drops ALL and has seccomp RuntimeDefault (container or pod level). Fails closed.
SOURCE_POD_CHECK='
import json, sys
pod = json.load(sys.stdin)
owners = pod["metadata"].get("ownerReferences") or []
assert any(o.get("kind") == "Sandbox" and o.get("name") == "remote-echo" for o in owners), "owner"
spec = pod["spec"]
assert spec.get("runtimeClassName") == "gvisor", "runtimeClassName"
c = [c for c in spec["containers"] if c["name"] == "workload"]
assert len(c) == 1, "workload container"
sc = c[0].get("securityContext") or {}
assert (sc.get("capabilities") or {}).get("drop") == ["ALL"], "capabilities"
sec = sc.get("seccompProfile") or (spec.get("securityContext") or {}).get("seccompProfile") or {}
assert sec.get("type") == "RuntimeDefault", "seccomp"
'

# The gVisor seccomp profile. Source: PoC-5's running remote-echo pod (gVisor, RuntimeDefault, drop
# ALL), so the profile containerd generated for it is the one we want. Every step fails closed.
seccomp_profile() {
  command -v docker >/dev/null || die "seccomp: docker not found"
  command -v python3 >/dev/null || die "seccomp: python3 not found"
  local pod node cid profile sum nodes n
  pod=$(kctl -n "$REMOTE_NS" get pods -l "$SOURCE_POD_LABEL" --field-selector=status.phase=Running \
    -o jsonpath='{.items[*].metadata.name}') || die "seccomp: cannot list pods in $REMOTE_NS"
  [[ -n $pod && $pod != *" "* ]] || die "seccomp: want exactly one running $SOURCE_POD_LABEL pod, got '$pod'"
  kctl -n "$REMOTE_NS" get pod "$pod" -o json | python3 -c "$SOURCE_POD_CHECK" ||
    die "seccomp: source pod $pod is not the PoC-5 remote-echo gVisor RuntimeDefault drop-ALL pod"
  node=$(kctl -n "$REMOTE_NS" get pod "$pod" -o jsonpath='{.spec.nodeName}') || die "seccomp: no node for $pod"
  cid=$(kctl -n "$REMOTE_NS" get pod "$pod" \
    -o jsonpath='{.status.containerStatuses[?(@.name=="workload")].containerID}') ||
    die "seccomp: no container id for $pod"
  [[ $cid == containerd://?* ]] || die "seccomp: container id '$cid' is not containerd://<id>"
  cid=${cid#containerd://}
  [[ -n $node ]] || die "seccomp: pod $pod has no node"
  log "seccomp: source pod $REMOTE_NS/$pod on node $node"
  # The inspect JSON holds the remote-echo container env, including its token. It flows only through
  # this pipe into derive_profile.py, which never prints its input; no error path here echoes it.
  profile=$(docker exec "$node" crictl inspect "$cid" | python3 "$SECCOMP_DERIVE") ||
    die "seccomp: crictl inspect or derive_profile.py failed for $cid on $node"
  [[ -n $profile ]] || die "seccomp: derived profile is empty"
  sum=$(printf '%s\n' "$profile" | sha256sum | cut -d' ' -f1)
  nodes=$(kctl get nodes -o jsonpath='{.items[*].metadata.name}') || die "seccomp: cannot list nodes"
  [[ -n $nodes ]] || die "seccomp: no nodes"
  for n in $nodes; do
    # Write a temp file, check its sha256, then rename it into place (atomic on one filesystem).
    printf '%s\n' "$profile" |
      docker exec -i "$n" sh -c "mkdir -p $SECCOMP_DIR && cat > $SECCOMP_DIR/.$SECCOMP_FILE.tmp" ||
      die "seccomp: cannot write the profile on node $n"
    docker exec "$n" sha256sum "$SECCOMP_DIR/.$SECCOMP_FILE.tmp" | grep -q "^$sum " ||
      die "seccomp: profile on node $n does not match sha256 $sum"
    docker exec "$n" mv -f "$SECCOMP_DIR/.$SECCOMP_FILE.tmp" "$SECCOMP_DIR/$SECCOMP_FILE" ||
      die "seccomp: cannot move the profile into place on node $n"
    log "seccomp: wrote $SECCOMP_DIR/$SECCOMP_FILE on $n"
  done
  log "seccomp: sha256 $sum; the one changed rule: clone3, SCMP_ACT_ERRNO (errnoRet 38) -> SCMP_ACT_ALLOW"
}

apply_all() {
  seccomp_profile
  apply_params
  apply_fake_model
  "$SEED" keys
  apply_remote
  apply_agents
  apply_kagent
}

pods() {
  local ns
  for ns in "$PLATFORM_NS" "$AGENTS_NS" "$REMOTE_NS"; do
    kctl get pods -n "$ns" -o wide
  done
  kctl get sandbox -A
}

up() {
  local start=$SECONDS
  "$P5" up
  build
  load
  apply_all
  pods
  log "up done in $((SECONDS - start)) s"
}

# The kind tests, selected by file path; a missing file fails the verb by name, so the job never
# goes green on an empty selection. The context must exist, or every test would skip.
run_tests() {
  local f missing=()
  for f in "${KIND_TESTS[@]}"; do
    [[ -f $ROOT/$f ]] || missing+=("$f")
  done
  ((${#missing[@]} == 0)) || die "test: kind test files missing, nothing run: ${missing[*]}"
  kctl get namespace "$AGENTS_NS" >/dev/null || die "test: no context $CONTEXT; run: $0 up"
  (cd "$ROOT" && env POC06_KIND=1 uv run pytest -m kind "${KIND_TESTS[@]}" -q -rsx)
}

usage() { sed -n '2,26p' "$0"; }

(($# > 0)) || { usage; exit 0; }
for cmd in "$@"; do
  case $cmd in
    up) up ;;
    build) build ;;
    load) load ;;
    seed) "$SEED" keys ;;
    seccomp) seccomp_profile ;;
    apply) apply_all ;;
    test) run_tests ;;
    pods) pods ;;
    logs) "$P5" logs ;;
    redact) "$P5" redact ;;
    delete | down) "$P5" delete ;;
    *) usage; exit 2 ;;
  esac
done
