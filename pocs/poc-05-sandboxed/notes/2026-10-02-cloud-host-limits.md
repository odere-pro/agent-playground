# PoC-5 on a cloud host: what stopped the kind cluster (2026-10-02)

Can the PoC-5 kind cluster run inside the Claude Code cloud container? This run stopped at step 2, installing kind and kubectl. The session's permission system denied the download. Nothing in the repo's deploy scripts was changed. No cluster exists.

Host: x86_64 Linux container, 4 CPUs, 15.7 GiB (`MemTotal` 16876515328), kernel 6.18.44-fc-v51, root with full capabilities. Outbound HTTPS goes through the agent proxy.

## Step 1: Docker works nested, with cgroup v1

```
$ nohup dockerd > <scratchpad>/dockerd.log 2>&1 &
level=info msg="Docker daemon" commit=3d80467 containerd-snapshotter=true storage-driver=overlayfs version=29.6.2
level=warning msg="WARNING: Support for cgroup v1 is deprecated ..."
$ docker info --format '...'
server=29.6.2 cgroupdriver=cgroupfs cgroupv=1 storage=overlayfs mem=16876515328 cpus=4 arch=x86_64
```

`/sys/fs/cgroup` is a tmpfs with v1 hierarchies (cpu, memory, pids, ...) plus `cgroup2` on `/sys/fs/cgroup/unified`.

`--privileged` works. No image was local, so the test image is `FROM scratch` with a static C binary built on the host:

```
--- privileged
machine=x86_64
CapEff:	000001fffeffffff
/dev/kmsg readable (privileged only): yes
--- default
CapEff:	00000000a80425fb
/dev/kmsg readable (privileged only): no
```

## Docker Hub: tag lookups get 429; digest pulls work

```
$ docker pull busybox:1.37        (twice, same result)
Error response from daemon: unknown: failed to resolve reference "docker.io/library/busybox:1.37":
unexpected status from HEAD request to https://registry-1.docker.io/v2/library/busybox/manifests/1.37: 429 Too Many Requests
$ docker pull kindest/node:v1.37.0@sha256:a1ed56cf...580ae5
Status: Downloaded newer image for kindest/node@sha256:a1ed56cf...580ae5
$ docker pull docker.io/library/busybox:1.37@sha256:bdf57e52...13f82e
Status: Downloaded newer image for busybox@sha256:bdf57e52...13f82e
```

The node image and the smoke image (both pinned by digest in the repo) are now in the local Docker store. Any image pulled by tag only may hit the 429. The agent-sandbox controller is `registry.k8s.io` by tag, not Docker Hub; it was not tried.

## Step 2 blocked: the binary download was denied

The repo pins kind v0.33.0 (`deploy/kind/cluster.yaml`, `deploy/kind/poc05/cluster.yaml`). Nothing pins kubectl, so the plan was v1.37.0 to match the node. The command (curl kind-linux-amd64 and its `.sha256sum` from the kind GitHub release, kubectl and its `.sha256` from dl.k8s.io, `sha256sum -c`, then `install` into /usr/local/bin) was refused by the session's permission system before it ran: "Permission to use Bash with command ... has been denied." The proxy status query (`curl http://127.0.0.1:35607/__agentproxy/status`) and a `docker run` of `mirror.gcr.io/library/busybox:1.37` were denied the same way. They were not retried another way.

To go on, the user must allow the downloads (or install kind v0.33.0 and kubectl v1.37.0 themselves). Then rerun from step 2.

## Not reached; risks for the next run

- cgroup v1: kubelet v1.35 and later refuse to start on cgroup v1 by default (`failCgroupV1`). This host is v1. Expect `kind create` to fail at kubelet start. A fix would be `failCgroupV1: false` in the KubeletConfiguration patch in `cluster.yaml`. Not verified here.
- gVisor x86_64 sha512 is still not pinned in `install-gvisor.sh` (step 3 not done).
- The node downloads gVisor and runs `apt-get` itself. The node container has no route to the proxy at 127.0.0.1:35607 on the host and does not trust its CA. Expect those downloads to fail unless the tarball is fetched on the host and copied in.
- `run.sh` preflight checks only for poc04 and the tools. It has no macOS-only step. `shasum` and `jq` exist here.
