#!/usr/bin/env bash
# Install gVisor (runsc + containerd-shim-runsc-v1) into every node of a kind cluster.
# Downloads inside the node (the node image ships curl), checks the published sha512, then
# restarts containerd. The runtime handler itself comes from cluster.yaml (containerdConfigPatches).
#
# Since mid-2026 the release bucket ships one tarball per arch (gvisor.tar.zstd / .tar.bz2), not
# the separate runsc and shim binaries. The kind node has neither zstd nor bzip2, so this installs
# the Debian zstd package into the node (spike shortcut; a real build would bake a node image).
# Usage: install-gvisor.sh [cluster-name]
set -euo pipefail

CLUSTER="${1:-poc05-spike}"
# Pinned dated release from https://storage.googleapis.com/gvisor/releases/release/
GVISOR_RELEASE="${GVISOR_RELEASE:-20260928.0}"
# Published sha512 of aarch64/gvisor.tar.zstd for that release (checked against the .sha512 file).
GVISOR_SHA512="${GVISOR_SHA512:-31519c2c8476c6eb24f51aa1378e555b4e86883dc1c8d81fb82df1af1900f38b10149affaa05bcc2418e57989bd04a33603a10ac73fee92e0fe20aef44e402e3}"
# systrap is the default; KVM is not available inside Docker Desktop's VM.
RUNSC_PLATFORM="${RUNSC_PLATFORM:-systrap}"

for node in $(kind get nodes --name "$CLUSTER"); do
  echo "== $node"
  docker exec -e REL="$GVISOR_RELEASE" -e SUM="$GVISOR_SHA512" -e PLATFORM="$RUNSC_PLATFORM" \
    "$node" bash -euo pipefail -c '
    arch=$(uname -m)   # aarch64 on Apple silicon
    base="https://storage.googleapis.com/gvisor/releases/release/${REL}/${arch}"
    cd /tmp
    curl -fsSLO "${base}/gvisor.tar.zstd"
    curl -fsSLO "${base}/gvisor.tar.zstd.sha512"
    sha512sum -c gvisor.tar.zstd.sha512          # the published sum
    echo "${SUM}  gvisor.tar.zstd" | sha512sum -c - # the pinned sum
    command -v zstd >/dev/null || { apt-get update -qq && apt-get install -y -qq zstd >/dev/null; }
    mkdir -p gv && tar --zstd -xf gvisor.tar.zstd -C gv
    install -m 0755 gv/runsc gv/containerd-shim-runsc-v1 /usr/local/bin/
    # runsc execs its helpers (gvisor_sentry etc.) from gvisor-bin/ next to itself; without it the
    # sandbox fails: sidecar "gvisor_sentry" not usable ... --sidecar-usage-policy is set to STRICT
    rm -rf /usr/local/bin/gvisor-bin && cp -a gv/gvisor-bin /usr/local/bin/gvisor-bin
    rm -rf gv gvisor.tar.zstd*
    # runsc flags for the shim; the runtime options in cluster.yaml point ConfigPath here.
    # oci-seccomp: without it runsc ignores the pod seccompProfile (Seccomp: 0 inside the pod);
    # with it, RuntimeDefault is applied inside the sandbox (Seccomp: 2).
    cat > /etc/containerd/runsc.toml <<EOF
[runsc_config]
  platform = "${PLATFORM}"
  oci-seccomp = "true"
EOF
    systemctl restart containerd
    for i in $(seq 30); do crictl info >/dev/null 2>&1 && break; sleep 1; done
    runsc --version
  '
done
