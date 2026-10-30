# deploy/helm

The shared Helm library chart that adds the chassis to every agent's pod (backlog [024 CH-3](../../docs/planning/issues/024-CH-3-helm-library-chart.md), [ADR-001](../../docs/planning/adr/001-chassis-delivery-model.md) item 9), and one example chart that uses it. PoC-9, exit criterion 5: "The library chart renders a pod with both containers, secrets in the chassis container only, and the Service on the chassis port."

| Folder | What |
| ------ | ---- |
| `agent-lib/` | The library chart (`type: library`). Named templates, no objects of its own |
| `example-agent/` | An application chart that depends on `agent-lib` (`file://../agent-lib`) and renders a sidecar-lane agent: the chassis and `echo-python`. The service template copies it |

```bash
helm dependency update --skip-refresh deploy/helm/example-agent   # file:// only, no network
helm lint deploy/helm/agent-lib deploy/helm/example-agent
helm template example-agent deploy/helm/example-agent --namespace agents
make test-poc POC=09                                               # the chart test, if helm is on PATH
```

Kubernetes 1.33 or later (native sidecars, stable). `charts/` and `Chart.lock` are built, not committed.

## Named templates

A service chart includes one per file in its `templates/`, as `example-agent` does, or `agent-lib.all` for every object.

| Template | Renders |
| -------- | ------- |
| `agent-lib.serviceaccount` | One ServiceAccount per pod, `automountServiceAccountToken: false` |
| `agent-lib.configmap` | `<name>-chassis`: the chassis config at `/etc/chassis/config.yaml`, with `spec.engine.url` (and, remote, `spec.engine.auth`) filled when empty |
| `agent-lib.deployment` | The agent's pod. Remote lane: also `<name>-workload` under gVisor |
| `agent-lib.service` | `<name>`: the chassis port only. Remote lane: also `<name>-remote-proxy` (fixed ClusterIP) and `<name>-workload` |
| `agent-lib.networkpolicy` | Ingress and egress for each pod |
| `agent-lib.hpa` | An `autoscaling/v2` HPA on CPU, only when `hpa.enabled` |
| `agent-lib.all` | All of the above |

## The two lanes

The lane and the trust level are read from the chassis config, so the chart and the chassis read one value: `chassis.config.spec.engine.connector` (`sidecar` or `remote`) and `chassis.config.spec.trust` (`trusted` or `untrusted`). `untrusted` needs `remote`; the render fails otherwise (ADR-005 decision 4). The chart does not run the `inprocess` lane.

| | `sidecar` | `remote` |
| - | --------- | -------- |
| Pods | One: the chassis and the workload | Two: the chassis alone, and the workload in `remote.namespace` |
| Container roles | The chassis is the native sidecar (an init container with `restartPolicy: Always`), the workload the main container, as PoC-5 (`kind/poc05/agents/agent-echo.yaml`): the chassis binds its proxies before the workload starts (contract v4, "Bind order"). PoC-4 measured the reverse for drain; 024 CH-3 leaves the pick to the owner | The chassis is the only container in its pod |
| Runtime | runc | The workload pod under `runtimeClassName: gvisor`; the chassis pod outside the sandbox |
| Workload listens on | `127.0.0.1:9000` | The pod IP, `:9000`, token required on every path (`--require-token-env CHASSIS_API_TOKEN`, the Python template) |
| Workload reaches the chassis | `127.0.0.1:8090` | `<remote.proxyClusterIP>:8091`, a fixed ClusterIP: the remote pod has no DNS |
| Secrets in the workload | None | One: its own bearer token, `CHASSIS_API_TOKEN` from `remote.tokenSecret` (ADR-005 decision 1) |
| Replicas | `replicas`, or the HPA | One, and no HPA: the remote listener answers only for a run in its own process (`run_required`) |

The remote lane renders a plain Deployment, not an agent-sandbox `Sandbox` as PoC-5 did; the pod spec is the same. PoC-5 kept the remote in its own namespace (`poc05-remote`), so a tenant who may create pods there cannot read the chassis's Secrets. Set `remote.namespace` to do the same; the token Secret must then exist in both namespaces.

## What the chart enforces

`agent-lib/templates/_validate.tpl` fails the render, with a message that starts `agent-lib:`, when a value would break the boundary. The test renders each case.

- The workload takes an allow-list of keys: `image`, `pullPolicy`, `port`, `args`, `env`, `probeCommand`, `resources`, `tmpSizeLimit`. There is no key for a Secret, `envFrom`, a volume, or a security context, and any other key fails. `workload.env` is plain `name`/`value` pairs: no `valueFrom`, no credential-looking name (`*_KEY`, `*_TOKEN`, `*PASSWORD*`, `*SECRET*`, `*CREDENTIAL*`), none of the names the chart sets (`HOST`, `PORT`, `POD_IP`, `CHASSIS_*`).
- The chassis gets Secrets only through `chassis.secretEnv`, `chassis.envFromSecrets`, and `chassis.secretVolumes`. `chassis.env` is plain too. No `command`: the image's ENTRYPOINT runs (PoC-5 admission rule 6b).
- Images are pinned: the chassis by tag or digest, never `latest`; the workload by tag or digest.
- Egress peers are labels: no `ipBlock`, no rule without a `to`.
- The remote lane needs `remote.tokenSecret.name`, `remote.proxyClusterIP`, and a RuntimeClass, and runs one replica with no HPA.

Fixed by the chart, not values: the uids (chassis 10001, workload 10002, `deploy/README.md`), a read-only root, no privilege escalation, all capabilities dropped, seccomp `RuntimeDefault` on each pod and container, `automountServiceAccountToken: false`, `enableServiceLinks: false`, a memory-backed `/tmp` per container with a size cap, the chassis's public port on the pod IP, its probes (`/health` startup and liveness, `/ready` readiness), and the workload's exec probes on the agent card (liveness grace 10 s).

## Values

Defaults are in `agent-lib.defaults` (`agent-lib/templates/_values.tpl`); a service sets only what differs. All suggested unless a source is named.

| Key | Default | What |
| --- | ------- | ---- |
| `chassis.image.repository`, `.tag`, `.digest` | `kind.local/agent-platform/chassis`, `poc09`, empty | The chassis image, set once here. A service holds the previous tag with `chassis.image.tag` |
| `chassis.port`, `.proxyPort`, `.remoteProxyPort` | 8080, 8090, 8091 | Public port, loopback proxies, remote proxy listener |
| `chassis.startupWaitSeconds`, `.drainDelaySeconds`, `.drainTimeoutSeconds` | 120, 10, 30 | 024 CH-3, PoC-4 status |
| `chassis.env` | `[]` | Plain `name`/`value` pairs |
| `chassis.secretEnv` | `[]` | `name`, `secretName`, `key`: one env var from a Secret |
| `chassis.envFromSecrets`, `chassis.secretVolumes` | `[]` | Secret names; `name`, `secretName`, `mountPath` |
| `chassis.config` | `{}` | The chassis config document; `spec.engine.connector` and `spec.trust` are required |
| `chassis.extraArgs` | `[]` | More `chassis serve` flags |
| `workload.image` | required | The workload image, pinned |
| `workload.port`, `.args`, `.env`, `.probeCommand` | 9000, `["--drain-timeout-s", "30"]`, `[]`, a python GET on the agent card | |
| `chassis.resources`, `workload.resources` | 100m / 128Mi requests, 500m / 256Mi limits | PoC-5; right-sizing is 122 X-5 |
| `chassis.tmpSizeLimit`, `workload.tmpSizeLimit` | 16Mi, 32Mi | PoC-5 (H22) |
| `replicas`, `terminationGracePeriodSeconds` | 1, 50 | 50 = drain delay 10 + drain timeout 30 + 10 |
| `service.port` | 8080 | The Service port; its `targetPort` is always `chassis.port` |
| `remote.namespace`, `.runtimeClassName`, `.proxyClusterIP`, `.tokenSecret.name`, `.tokenSecret.key` | release namespace, `gvisor`, required, required, `token` | Remote lane only |
| `networkPolicy.ingressFrom` | `[]` (any pod, chassis port only) | Peers that may call the chassis port |
| `networkPolicy.chassisEgress` | `[]` | Egress rules for the chassis pod besides DNS |
| `networkPolicy.dns` | `kube-system`, `k8s-app: kube-dns` | The DNS peer |
| `hpa.enabled`, `.minReplicas`, `.maxReplicas`, `.targetCPUUtilizationPercentage` | false, 2, 5, 70 | |

## Not here

- The PoC-5 admission rules (`kind/poc05/admission/`) and the trust list: the chart is checked by them, it does not carry them.
- The namespace's default deny and Pod Security labels (`kind/poc05/base/`).
- Default-deny egress to the cloud metadata service and the Kubernetes API by name: a NetworkPolicy can only allow, so the chart allows named peers and nothing else (026 CH-4).
- Making Secrets: outside the chart, through a pipe, as `kind/poc05/platform/seed.sh`.
- A kind run with this chart (PoC-9, optional).
