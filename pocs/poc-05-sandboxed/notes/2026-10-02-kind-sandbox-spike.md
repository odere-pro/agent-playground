# PoC-5 spike: sandboxing on kind (2026-10-02)

Feasibility spike for PoC-5. Every answer below comes from a run on this host. The files that worked are in `notes/spike/`; lift them from there.

Host: macOS arm64, Docker Desktop 29.7.2 (VM 7.75 GiB, 14 CPUs, aarch64, kernel 6.12.76-linuxkit), kind v0.33.0, one-node cluster `poc05-spike`. The cluster was deleted at the end.

## Pinned versions

| What | Pin |
| ---- | --- |
| Node image | `kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5` (same as `deploy/kind/cluster.yaml`) |
| containerd in the node | v2.3.4; `/etc/containerd/config.toml` is still `version = 2` |
| CNI | kindnet `docker.io/kindest/kindnetd:v20260820-69b56db7` (runs kube-network-policies) |
| gVisor | release `20260928.0`, `aarch64/gvisor.tar.zstd`, sha512 `31519c2c8476c6eb24f51aa1378e555b4e86883dc1c8d81fb82df1af1900f38b10149affaa05bcc2418e57989bd04a33603a10ac73fee92e0fe20aef44e402e3` (`runsc version release-20260928.0`, spec 1.2.1) |
| agent-sandbox | v1.0.5 (2026-10-01), `sandbox.yaml` sha256 `e89fd95c0aa57609fa24be4112bd52ce67fe8939ecf6f3c17edf2f1e8f1eb860` |
| agent-sandbox controller image | `registry.k8s.io/agent-sandbox/agent-sandbox-controller:v1.0.5`, resolved digest `sha256:28a9cbdbfd6ac0a4e5c7e9261ace1aa30ee2da681cb640dccdfed98e8dd9d98b` |
| Test image | `busybox:1.37` |

## Files

- `spike/up.sh`: create the cluster, install gVisor, apply the RuntimeClass, smoke test (`up.sh down` deletes it). About 28 s with images cached.
- `spike/cluster.yaml`: kind config with the `runsc` handler and kubelet `podPidsLimit: 256`.
- `spike/install-gvisor.sh`: downloads, verifies, and installs gVisor into each node.
- `spike/q1-gvisor.yaml` to `spike/q6-hardened.yaml`: one manifest per question.
- `spike/q6-pids.sh`, `spike/q7-overhead.sh`: the pids test and the overhead measure.
- `spike/agent-sandbox-v1.0.5.yaml`: the upstream release manifest, unmodified.

## 1. gVisor on kind: works, platform systrap

Answer: yes. A kind node on this host runs pods with RuntimeClass `gvisor` (handler `runsc`). systrap works and is the default. ptrace also works. KVM fails: there is no `/dev/kvm` in the Docker Desktop VM.

What had to be done:

1. The containerd patch in `cluster.yaml` uses the v2 plugin path, because the node config is `version = 2` (containerd 2.3.4 migrates it on load). The patch sets `runtime_type = "io.containerd.runsc.v1"` and `options.ConfigPath = "/etc/containerd/runsc.toml"` so runsc flags can be set in a file.
2. The gVisor bucket changed layout. Releases after about June 2026 ship one tarball per arch (`gvisor.tar.zstd` or `gvisor.tar.bz2`, about 120 to 150 MB). There are no separate `runsc` and shim downloads any more (`20260601.0` still had them). The node has neither zstd nor bzip2, so the script installs Debian's `zstd` package in the node. A real setup should bake a node image instead.
3. runsc needs the `gvisor-bin/` folder next to it. Without it the pod sandbox fails with `sidecar "gvisor_sentry" not usable (stat /usr/local/bin/gvisor-bin/gvisor_sentry: no such file or directory) and --sidecar-usage-policy is set to STRICT`.
4. Set `oci-seccomp = "true"` in `runsc.toml`. Without it runsc ignores the pod `seccompProfile` (see question 6).

Commands and output:

```
$ ./install-gvisor.sh
gvisor.tar.zstd: OK          # published .sha512
gvisor.tar.zstd: OK          # pinned sum
runsc version release-20260928.0
$ kubectl --context kind-poc05-spike apply -f q1-gvisor.yaml
$ kubectl --context kind-poc05-spike logs gv-hello
Linux version 4.19.0-gvisor #1 SMP Sun Jan 10 15:06:54 PST 2016
[   0.000000] Starting gVisor...
$ kubectl --context kind-poc05-spike logs runc-hello
Linux version 6.12.76-linuxkit ...
```

Platform test (rewrite `runsc.toml`, recreate the pod):

```
ls: cannot access '/dev/kvm': No such file or directory
kvm: NOT ready: ... opening device file for platform "kvm": error opening KVM device file (/dev/kvm): no such file or directory
ptrace: Ready, Linux version 4.19.0-gvisor #1
systrap: Ready, Linux version 4.19.0-gvisor #1
```

Caveat: the install runs `apt-get` inside the node and downloads about 120 MB per node. Each `kind create` repeats it.

## 2. NetworkPolicy on kind: kindnet enforces it, no Calico needed

Answer: yes. kindnet in this release runs kube-network-policies (`"Starting controller" name="kube-network-policies"` in its log) and enforces ingress and egress policy. It holds for gVisor pods too. Calico was not needed and was not tried.

Ingress (`q2-netpol.yaml`: default-deny ingress, allow `role=client` to the server on 8080):

```
allowed     -> server:8080 ok
denied      -> server:8080 wget: download timed out
gv-allowed  -> server:8080 ok
gv-denied   -> server:8080 wget: download timed out
```

Egress (`q2-egress.yaml`: allow DNS and `0.0.0.0/0` except `169.254.169.254/32`, `10.96.0.1/32`, `172.25.0.2/32`), same for runc and gVisor:

```
                 before policy   after policy
server.np 8080   OPEN            OPEN
10.96.0.1 443    OPEN            blocked
172.25.0.2 6443  OPEN            blocked
169.254.169.254  closed          closed
1.1.1.1 443      OPEN            OPEN
```

Which `except` entry blocks the API server:

```
== except only ClusterIP 10.96.0.1
10.96.0.1 443        OPEN
172.25.0.2 6443      OPEN
== except only node IP 172.25.0.2
10.96.0.1 443        blocked
172.25.0.2 6443      blocked
```

Caveats:

- Policy sees the address after the Service rewrite. Blocking the ClusterIP `10.96.0.1` does nothing. Block the API server endpoint (on kind, the node IP, port 6443) instead. Get it from `kubectl get endpointslices -l kubernetes.io/service-name=kubernetes`. The node IP changes per Docker network, so the builder must read it, not hard-code it.
- `169.254.169.254` is not reachable on Docker Desktop even without a policy, so the block of the metadata IP cannot be shown here. The rule is accepted; it is proven only on a cloud node.
- The spike policy allows the internet (`1.1.1.1` open). The real untrusted policy should allow only the pod CIDR (`10.244.0.0/16` on kind) and the proxy, not `0.0.0.0/0`.
- gVisor pods were tested as policy targets (egress) and as clients of an ingress policy. A gVisor pod as an ingress target was not tested separately; kindnet enforces on the host side of the veth, so it should behave the same.

## 3. Native sidecars: work on runc and gVisor

Answer: yes. An init container with `restartPolicy: Always` starts before the main container in both runtimes, and the main container reaches it on `127.0.0.1`.

```
$ kubectl --context kind-poc05-spike apply -f q3-native-sidecar.yaml
sidecar-runc     2/2     Running   0          2s
sidecar-gvisor   2/2     Running   0          2s
sidecar-runc main log: from-sidecar
sidecar-gvisor main log: from-sidecar
```

Caveat: in a gVisor pod the sidecar runs inside the same sandbox as the main container. It is not isolated from it.

## 4. kubernetes-sigs/agent-sandbox: works with gVisor

Answer: yes. v1.0.5 installs from the release `sandbox.yaml` with `kubectl apply`, and a `Sandbox` with `runtimeClassName: gvisor` gives a running gVisor pod.

- API: group `agents.x-k8s.io`, version `v1beta1`, kind `Sandbox` (namespaced, short name `sandbox`). Spec fields: `podTemplate` (required), `operatingMode` (Running, Suspended), `service`, `shutdownPolicy` (Delete, Retain), `shutdownTime`, `volumeClaimTemplates`.
- The core manifest has only the `Sandbox` CRD. `extensions.yaml` (not installed) adds the extension CRDs.
- Controller: namespace `agent-sandbox-system`, Deployment `agent-sandbox-controller`, image tag `v1.0.5`, digest `sha256:28a9cbdbfd6ac0a4e5c7e9261ace1aa30ee2da681cb640dccdfed98e8dd9d98b`.
- Controller memory: 13.02 MB (`crictl stats`, idle, one Sandbox).

```
$ gh release download v1.0.5 -R kubernetes-sigs/agent-sandbox -p sandbox.yaml -O agent-sandbox-v1.0.5.yaml
$ shasum -a 256 agent-sandbox-v1.0.5.yaml
e89fd95c0aa57609fa24be4112bd52ce67fe8939ecf6f3c17edf2f1e8f1eb860
$ kubectl --context kind-poc05-spike apply -f q4-sandbox.yaml
sb-gvisor   True    DependenciesReady   0s
pod/sb-gvisor   1/1     Running
Linux version 4.19.0-gvisor #1 SMP Sun Jan 10 15:06:54 PST 2016
gvisor owner=Sandbox
$ crictl stats --label io.kubernetes.container.name=agent-sandbox-controller
agent-sandbox-controller   0.30   13.02MB
```

Caveats: the manifest pins the image by tag, not digest, and sets no resources and no securityContext on the controller. Pin the digest and add limits when lifting it. The pod gets the Sandbox's name, and the Sandbox owns it.

## 5. ValidatingAdmissionPolicy: works

Answer: yes. `q5-vap.yaml` (policy, binding, namespace `vap` labeled `platform/admission=enforced`) rejects both cases and accepts good Deployments. Allowed prefixes are `suggested:` `registry.k8s.io/` and `docker.io/library/`.

```
== (a) untrusted+sidecar
Error from server (Forbidden): ... denied request: untrusted workloads may not run in the sidecar lane
== (b) image busybox:1.37
Error from server (Forbidden): ... denied request: image not from an allowed registry: busybox:1.37
== (b) image evil.example/registry.k8s.io/pause
Error from server (Forbidden): ... denied request: image not from an allowed registry: evil.example/registry.k8s.io/pause:3.10
== accepted: untrusted+remote, allowed image
deployment.apps/ok1 created
== accepted: trusted+sidecar, docker.io/library
deployment.apps/ok2 created
== unlabeled ns default: untrusted+sidecar, busybox
deployment.apps/ok3 created
```

Caveats:

- CEL optional syntax for a label key with a slash is `labels[?'platform/trust'].orValue('')`. The form `labels.?'platform/trust'` does not compile.
- A binding whose policy failed to compile enforces nothing, and the apply of the bad policy fails while the binding and namespace are still created. Apply the policy first and check it.
- The policy covers Deployments only. Bare Pods, Jobs, and agent-sandbox `Sandbox` objects get past it. The real policy should also match `pods` (the last hop for every controller).
- Short image names (`busybox:1.37`) are rejected by a prefix rule. Manifests must use full names.

## 6. Hardened gVisor pod: limits hold, two surprises

Pod (`q6-hardened.yaml`): `runAsNonRoot`, uid 65534, `readOnlyRootFilesystem`, drop ALL, `allowPrivilegeEscalation: false`, `seccompProfile: RuntimeDefault`, cpu 500m and memory 128Mi limits, `automountServiceAccountToken: false`.

| Check | runc | gVisor |
| ----- | ---- | ------ |
| `id` | uid 65534 | uid 65534 |
| write `/` | `Read-only file system` | `Permission denied` as non-root; `Read-only file system` as root |
| write `/tmp` | `Read-only file system` | works: runsc mounts its own tmpfs on `/tmp` |
| token path | absent | absent |
| CapEff | 0 | 0 |
| Seccomp | 2 | 0 without `oci-seccomp`, 2 with it |
| 400 processes, podPidsLimit 256 | `can't fork: Resource temporarily unavailable`, pod cgroup pids.current=255, pod lives | whole sandbox dies, pod restarts (`SandboxChanged`) |

Pids test (`q6-pids.sh`, run twice on two clusters, same result):

```
== hard-runc
   1 sh: can't fork: Resource temporarily unavailable
host pod cgroup pids.max=256 pids.current=255
== hard-gvisor
   1 waiting on PID 3 in sandbox "...": urpc method "containerManager.WaitPID" failed: EOF
host pod cgroup pids.max=256 pids.current=0
node still answers: ok
hard-gvisor   1/1     Running   1 (8s ago)
SandboxChanged   Pod sandbox changed, it will be killed and re-created.
```

Step test in the gVisor pod, 50 sleeps at a time: 53 sleeps used 156 host pids; the next 50 hit 256 and the sandbox died.

Answers:

- Pids lever: yes. `KubeletConfiguration.podPidsLimit` through kind `kubeadmConfigPatches` sets the pod cgroup `pids.max` (256 seen on the node). It holds under runsc too: the host never goes past 256 and the node stays healthy.
- Under systrap each guest process costs about three host pids (sentry stubs). So the guest gets roughly a third of the limit, and hitting it kills the whole sandbox instead of failing one `fork`. That is a denial of service on the pod itself, not on the node. Size `podPidsLimit` with this in mind.
- Write to `/`: blocked in both runtimes. gVisor adds a writable tmpfs on `/tmp` even with `readOnlyRootFilesystem`. It is backed by sandbox memory, so the memory limit caps it.
- Seccomp: runsc ignores the pod `seccompProfile` unless `oci-seccomp = "true"` is set in `runsc.toml`. With it, `Seccomp: 2` and `unshare -U` fails with `Operation not permitted`. `install-gvisor.sh` now sets it.
- Token: with `automountServiceAccountToken: false` the path `/var/run/secrets/kubernetes.io/serviceaccount` does not exist in either runtime.

## 7. Overhead: small startup cost, about 40 MiB more memory per pod

Answer: gVisor adds about 35 ms to pod startup and about 38 MiB of node memory per pod, compared with runc.

Startup (`q7-overhead.sh`, apply to Ready, image cached, 5 runs):

```
runc startup ms:   226 215 218 214 218
gvisor startup ms: 258 276 255 250 251
```

Memory:

```
pod cgroup memory.current:   runc 540 KiB    gVisor 25.5 MiB
node memory, 10 idle pods:   runc +7 MiB/pod  gVisor +45 MiB/pod
```

The node figure is the node container's `memory.current` before and after 10 idle `busybox sleep` pods. It includes the shim, gofer, and sentry, which sit partly outside the pod cgroup. Per-process RSS double-counts shared pages, so it is not used.

Caveats: one node, idle pods, no metrics-server. A busy workload will show more. 10 gVisor pods cost about 450 MiB on a 7.75 GiB VM, so the PoC should keep the gVisor pod count small.

## What did not work

- KVM platform: no `/dev/kvm` in the Docker Desktop VM.
- The old gVisor install recipe (download `runsc` and `containerd-shim-runsc-v1` alone): the bucket no longer ships them for recent releases.
- runsc without `gvisor-bin/`: the sandbox does not start.
- Blocking the API server by its ClusterIP in a NetworkPolicy: no effect.
- Proving the `169.254.169.254` block: nothing answers there on Docker Desktop.
- CEL `labels.?'key/with-slash'`: does not compile.
