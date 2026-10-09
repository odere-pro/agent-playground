#!/usr/bin/env bash
# Install gVisor (runsc, containerd-shim-runsc-v1, gvisor-bin/) into every node of a kind cluster.
# Lifted from the PoC-5 spike (pocs/poc-05-sandboxed/notes/spike/install-gvisor.sh).
#
# Downloads inside the node (the node image ships curl), checks the tarball against the release's
# published .sha512 file AND against the sum pinned here, and only then installs. The runtime
# handler itself comes from cluster.yaml (containerdConfigPatches). Restarts containerd at the end.
#
# Since mid-2026 the release bucket ships one tarball per arch (gvisor.tar.zstd), not separate
# runsc and shim binaries. The kind node has no zstd, so this installs Debian's zstd package in
# the node (spike shortcut; a real setup would bake a node image).
#
# Arch: read from the node (`uname -m`). aarch64 and x86_64 each have a sum pinned below for the
# same release, taken from the release's published file:
#   https://storage.googleapis.com/gvisor/releases/release/<release>/<arch>/gvisor.tar.zstd.sha512
# Any other arch fails closed.
#
# Usage: install-gvisor.sh [cluster-name]   (default poc05)
set -euo pipefail

CLUSTER="${1:-poc05}"
# Pinned dated release from https://storage.googleapis.com/gvisor/releases/release/
# Trust (security review F13, recorded): each pinned sum below was read once from the same channel
# as the published sum, so the channel was trusted once per arch. zstd comes from the node's
# apt with signature checks but no version pin. The later fix is a node image with runsc baked in.
GVISOR_RELEASE=20260928.0
# From .../20260928.0/aarch64/gvisor.tar.zstd.sha512, read by the spike (2026-10-02).
GVISOR_SHA512_AARCH64=31519c2c8476c6eb24f51aa1378e555b4e86883dc1c8d81fb82df1af1900f38b10149affaa05bcc2418e57989bd04a33603a10ac73fee92e0fe20aef44e402e3
# From .../20260928.0/x86_64/gvisor.tar.zstd.sha512, read by the user for T24 (2026-10-09).
GVISOR_SHA512_X86_64=4ce35ca83aef7f96b06cde668e0b23aa98b05aa1829508e974196c2a1e02786c95f5bf79315fd7ddcfd88fe7a00f083ed8053e25eff7673d28d5256440caae8b
# systrap is the default; KVM is not available inside Docker Desktop's VM (spike, question 1).
RUNSC_PLATFORM=systrap

log() { printf '[install-gvisor] %s\n' "$*" >&2; }
die() { log "$*"; exit 1; }

nodes=$(kind get nodes --name "$CLUSTER")
[[ -n $nodes ]] || die "no nodes in kind cluster $CLUSTER"

for node in $nodes; do
  arch=$(docker exec "$node" uname -m)
  case $arch in
    aarch64) sum=$GVISOR_SHA512_AARCH64 ;;
    x86_64) sum=$GVISOR_SHA512_X86_64 ;;
    *) die "$node: unsupported arch $arch" ;;
  esac
  [[ $sum =~ ^[0-9a-f]{128}$ ]] ||
    die "$node: no pinned sha512 for gVisor $GVISOR_RELEASE on $arch; refusing to install"
  log "$node ($arch): gVisor $GVISOR_RELEASE"
  docker exec -e REL="$GVISOR_RELEASE" -e SUM="$sum" -e PLATFORM="$RUNSC_PLATFORM" -e ARCH="$arch" \
    -e DEBIAN_FRONTEND=noninteractive \
    "$node" bash -euo pipefail -c '
    base="https://storage.googleapis.com/gvisor/releases/release/${REL}/${ARCH}"
    work=$(mktemp -d)
    cd "$work"
    curl -fsSLO "${base}/gvisor.tar.zstd"
    curl -fsSLO "${base}/gvisor.tar.zstd.sha512"
    sha512sum -c gvisor.tar.zstd.sha512             # the published sum
    echo "${SUM}  gvisor.tar.zstd" | sha512sum -c -  # the pinned sum
    command -v zstd >/dev/null || { apt-get update -qq && apt-get install -y -qq zstd >/dev/null; }
    mkdir gv && tar --zstd -xf gvisor.tar.zstd -C gv
    install -m 0755 gv/runsc gv/containerd-shim-runsc-v1 /usr/local/bin/
    # runsc execs its helpers (gvisor_sentry etc.) from gvisor-bin/ next to itself; without it the
    # sandbox fails: sidecar "gvisor_sentry" not usable ... --sidecar-usage-policy is set to STRICT
    rm -rf /usr/local/bin/gvisor-bin && cp -a gv/gvisor-bin /usr/local/bin/gvisor-bin
    cd / && rm -rf "$work"
    # runsc flags for the shim; cluster.yaml points ConfigPath here.
    # oci-seccomp: without it runsc ignores the pod seccompProfile (Seccomp: 0 inside the pod);
    # with it, RuntimeDefault is applied inside the sandbox (Seccomp: 2).
    cat > /etc/containerd/runsc.toml <<EOF
[runsc_config]
  platform = "${PLATFORM}"
  oci-seccomp = "true"
EOF
    systemctl restart containerd
    for _ in $(seq 30); do crictl info >/dev/null 2>&1 && break; sleep 1; done
    runsc --version
  '
done
