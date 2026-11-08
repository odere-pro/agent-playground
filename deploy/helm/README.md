# deploy/helm

The shared Helm library chart that adds the chassis to every agent's pod (backlog [024 CH-3](../../docs/planning/issues/024-CH-3-helm-library-chart.md), [ADR-001](../../docs/planning/adr/001-chassis-delivery-model.md) item 9), and one example chart that uses it. PoC-9, exit criterion 5: "The library chart renders a pod with both containers, secrets in the chassis container only, and the Service on the chassis port."

| Folder | What |
| ------ | ---- |
| `agent-lib/` | The library chart (`type: library`). Named templates, no objects of its own |
| `example-agent/` | An application chart that depends on `agent-lib` (`file://../agent-lib`) and renders a sidecar-lane agent: the chassis and `echo-python`. The service template copies it |
| `agent-admission/` | The platform's chart: the admission rules from `agent-lib.admission`, installed once per cluster in the platform's namespace, never by a service ([the admission rules](#the-admission-rules)) |

```bash
helm dependency update --skip-refresh deploy/helm/example-agent   # file:// only, no network
helm dependency update --skip-refresh deploy/helm/agent-admission
helm lint deploy/helm/agent-lib deploy/helm/example-agent deploy/helm/agent-admission
helm template example-agent deploy/helm/example-agent --namespace agents
helm template agent-admission deploy/helm/agent-admission --namespace agent-platform-system
make test-poc POC=09                                               # the chart tests, if helm is on PATH
```

Kubernetes 1.33 or later (native sidecars, stable). `charts/` and `Chart.lock` are built, not committed.

## Named templates

A service chart includes one per file in its `templates/`, as `example-agent` does, or `agent-lib.all` for every object. Name those files `<object>.tpl`, no leading underscore: Helm renders them as manifests, and the suffix keeps raw templates out of the repo's YAML scans of `deploy/`.

| Template | Renders |
| -------- | ------- |
| `agent-lib.serviceaccount` | One ServiceAccount per pod, `automountServiceAccountToken: false` |
| `agent-lib.configmap` | `<name>-chassis`: the chassis config at `/etc/chassis/config.yaml`, with `spec.engine.url` (and, remote, `spec.engine.auth`) filled when empty |
| `agent-lib.deployment` | The agent's pod. Remote lane: also `<name>-workload` under gVisor |
| `agent-lib.service` | `<name>`: the chassis port only. Remote lane: also `<name>-remote-proxy` (fixed ClusterIP) and `<name>-workload` |
| `agent-lib.networkpolicy` | Ingress and egress for each pod, and `<name>-default-deny` for each namespace the pods are in |
| `agent-lib.hpa` | An `autoscaling/v2` HPA on CPU, only when `hpa.enabled` |
| `agent-lib.all` | All of the above |
| `agent-lib.admission` | The platform's, not a service's: the trust-rule `ValidatingAdmissionPolicy`, its binding, and its params ConfigMap (`agent-lib.admissionPolicy`, `.admissionBinding`, `.admissionParams`) |

## The two lanes

The lane and the trust level are read from the chassis config, so the chart and the chassis read one value: `chassis.config.spec.engine.connector` (`sidecar` or `remote`) and `chassis.config.spec.trust` (`trusted` or `untrusted`). `untrusted` needs `remote`; the render fails otherwise (ADR-005 decision 4). The chart does not run the `inprocess` lane.

| | `sidecar` | `remote` |
| - | --------- | -------- |
| Pods | One: the chassis and the workload | Two: the chassis alone, and the workload in `remote.namespace` |
| Container roles | The workload is the native sidecar (an init container with `restartPolicy: Always`), the chassis the main container ([ADR-012](../../docs/planning/adr/012-drain-order-on-rollout.md)): the kubelet stops the chassis first, so it drains before the workload stops (contract, "Container order"). The workload starts first; the chassis starts after the workload's `startupProbe` passes. The chassis's own startupProbe checks the file `--ready-marker /tmp/chassis-ready` writes once every listener has bound (in `chassis-tmp`, the chassis's alone), so a taken public or proxy port leaves the pod never started and never Ready. `terminationGracePeriodSeconds` (50) must be at least `chassis.drainDelaySeconds + chassis.drainTimeoutSeconds + 5`; the render fails otherwise. PoC-5's manifests keep the old order as its record | The chassis is the only container in its pod |
| Runtime | runc | The workload pod under `runtimeClassName: gvisor`; the chassis pod outside the sandbox |
| Workload listens on | `127.0.0.1:9000` | The pod IP, `:9000`, token required on every path (`--require-token-env CHASSIS_API_TOKEN`, the Python template) |
| Workload reaches the chassis | `127.0.0.1:8090` | `<remote.proxyClusterIP>:8091`, a fixed ClusterIP: the remote pod has no DNS |
| Secrets in the workload | None | One: its own bearer token, `CHASSIS_API_TOKEN` from the Secret `<name>-workload-token` (ADR-005 decision 1; admission rule 7c names it after the pod) |
| Replicas | `replicas`, or the HPA | One, and no HPA: the remote listener answers only for a run in its own process (`run_required`) |

The remote lane renders a plain Deployment, not an agent-sandbox `Sandbox` as PoC-5 did; the pod spec is the same. PoC-5 kept the remote in its own namespace (`poc05-remote`), so a tenant who may create pods there cannot read the chassis's Secrets. Set `remote.namespace` to do the same; the token Secret `<name>-workload-token` must then exist in both namespaces.

## What the chart enforces

`agent-lib/templates/_validate.tpl` fails the render, with a message that starts `agent-lib:`, when a value would break the boundary. The test renders each case.

- The workload takes an allow-list of keys: `image`, `pullPolicy`, `port`, `args`, `env`, `probeCommand`, `resources`, `tmpSizeLimit`. There is no key for a Secret, `envFrom`, a volume, or a security context, and any other key fails. `workload.env` is plain `name`/`value` pairs: no `valueFrom`, no credential-looking name (`*_KEY`, `*_TOKEN`, `*PASSWORD*`, `*SECRET*`, `*CREDENTIAL*`), none of the names the chart sets (`HOST`, `PORT`, `POD_IP`, `CHASSIS_*`).
- The chassis gets Secrets only through `chassis.secretEnv`, `chassis.envFromSecrets`, and `chassis.secretVolumes`. `chassis.env` is plain too. No `command`: the image's ENTRYPOINT runs (PoC-5 admission rule 6b).
- Images are pinned: the chassis by a tag from the library's supported list (`chassis.image.supportedTags`, the current tag and the previous one), never `latest` and never a digest alone; a digest next to the tag only on a digest-mode cluster, where admission rule 9 reads the digest (on a tag-loaded cluster it refuses a digest beside the tag; ADR-001 item 9); the workload by tag or digest.
- Default-deny egress (026 CH-4): every pod's policy names both directions; every egress rule names a peer by `namespaceSelector.matchLabels` and `podSelector.matchLabels` and its `ports`; no `ipBlock`, no rule without a `to`, no empty selector (an empty selector is every pod). The cloud metadata service and the API server are not pods, so no rule can reach them. Each namespace the chart renders pods into also gets `<name>-default-deny`; it leaves with the release, so the platform's own namespace deny stays required and this one is belt and braces. In the sidecar lane the workload shares the pod's network namespace, so the chassis's allow-list is its egress too; a remote workload pod reaches only its chassis's remote proxy.
- The remote lane needs `remote.proxyClusterIP` and a RuntimeClass, and runs one replica with no HPA. Its token Secret is named by the chart, `<name>-workload-token`; `remote.tokenSecret.name` is refused.
- A key the chart does not read is refused, so no value can look like it switches a rule off: `admission` (the platform's, below), `chassis.image.supportedTags` (the library's), `remote.tokenSecret.name`.

Fixed by the chart, not values: the uids (chassis 10001, workload 10002, `deploy/README.md`), a read-only root, no privilege escalation, all capabilities dropped, seccomp `RuntimeDefault` on each pod and container, `automountServiceAccountToken: false`, `enableServiceLinks: false`, a memory-backed `/tmp` per container with a size cap, the chassis's public port on the pod IP, its probes (`/health` startup and liveness, `/ready` readiness), and the workload's exec probes on the agent card (liveness grace 10 s).

## The admission rules

The second check on the boundary (ADR-005 decision 4: the trust rule is checked twice, at render and at admission) is a `ValidatingAdmissionPolicy` on the API server, rendered by `agent-lib.admission` and installed by the platform through `agent-admission/`, once per cluster, in the platform's namespace. A service cannot switch it off: it has no value for it (the render refuses `admission`), no file, and no RBAC on cluster-scoped admission objects.

- `agent-trust-rule`, the policy: rules 0 to 8 are PoC-5's (`deploy/kind/poc05/admission/policy.yaml`, whose header explains each: the trust label, no `untrusted` pod in the sidecar lane, the registry prefix, the trusted repositories, the remote pod's shape, one chassis with no `command` and the only Secrets, the namespace shape, the image store). The spec in the chart is that file's, the same parsed spec; `tests/test_poc09_chart_boundary.py` holds the two equal, so the kind admission suite (`test_poc05_kind_admission.py`, every rule with a rejected fixture and an admitted twin) proves the CEL the chart ships.
- `agent-trust-rule`, the binding: `Deny` and `Audit` in every namespace labeled `agents.platform/admission: enforce`, with `parameterNotFoundAction: Deny`. The platform labels namespaces, with `agents.platform/pod-shape: chassis` on a service's namespace and `remote` on a remote namespace (rule 7a); no tenant role may.
- `agent-chassis-version`, the policy: rule 9, the minimum chassis version (ADR-001 item 9; 026 CH-4), read the way the node resolves the image: with `imageSource: pull-never` every chassis image carries a tag in `supportedChassisTags` and no digest; with `imageSource: digest` every chassis image carries a digest in `supportedChassisDigests`, because the runtime ignores a tag beside a digest. It is its own policy because a binding carries one action list for the whole policy, and this is the one rule that warns first. Its spec is `deploy/kind/poc05/admission/chassis-version-policy.yaml`'s, held equal by the same test file.
- `agent-chassis-version`, the binding: the trust rule's namespaces and params, with `validationActions` from `admission.chassisVersionAction`: `Deny` (the default) renders `[Deny, Audit]`; `Warn` renders `[Warn, Audit]`, and a pod below the minimum is admitted with rule 9's message as a warning on the request and an audit annotation. Warn-first (ADR-001 item 9: "it warns first, then enforces"): after a chassis release raises the minimum, the platform sets `Warn` for one ring cycle (suggested), then `Deny`. No other value renders, so the rule cannot be switched off. The kind test `test_poc05_kind_admission.py::test_a_chassis_below_the_minimum_is_admitted_with_a_warning_in_the_warn_ring` shows the warning and the return to `Deny`.
- `agent-trust-params`, the ConfigMap in the platform release's namespace: `registryPrefix`, `imageSource` (`pull-never` on kind, `digest` on a cloud), `trustedRepositories`, and `supportedChassisDigests` (digest mode; required there) from `agent-admission/values.yaml`; `supportedChassisTags` from the library's own `chassis.image.supportedTags`, so a chassis release that bumps the tag in `agent-lib` bumps the minimum version on a tag-loaded cluster. Both policies read it.

The platform's objects and PoC-5's kustomize set share the names `agent-trust-rule`, `agent-chassis-version`, and `agent-trust-params`, so a cluster carries one or the other: install the chart on a cluster without the kustomize set, or delete that set first. Not in the chart: the agent-sandbox extension rules (T1 to T5, C1 to C5) and the RBAC, which are the PoC-5 code runner's (`deploy/kind/poc05/admission/`), and the trust list's source (hand-kept until signatures, 025 H-10).

## Values

Defaults are in `agent-lib.defaults` (`agent-lib/templates/_values.tpl`); a service sets only what differs. All suggested unless a source is named.

| Key | Default | What |
| --- | ------- | ---- |
| `chassis.image.repository`, `.tag`, `.digest` | `kind.local/agent-platform/chassis`, `poc09`, empty | The chassis image, set once here. A service holds the previous tag with `chassis.image.tag`; the tag must be in `chassis.image.supportedTags` (`[poc09, poc08]`, the library's, not a value) |
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
| `remote.namespace`, `.runtimeClassName`, `.proxyClusterIP`, `.tokenSecret.key` | release namespace, `gvisor`, required, `token` | Remote lane only. The token Secret is `<name>-workload-token`, not a value |
| `networkPolicy.ingressFrom` | `[]` (any pod, chassis port only) | Peers that may call the chassis port |
| `networkPolicy.chassisEgress` | `[]` | Egress rules for the chassis pod besides DNS: each with `to` (peers by `namespaceSelector.matchLabels` and `podSelector.matchLabels`) and `ports` |
| `networkPolicy.dns` | `kube-system`, `k8s-app: kube-dns` | The DNS peer; `podLabels` may not be empty |
| `hpa.enabled`, `.minReplicas`, `.maxReplicas`, `.targetCPUUtilizationPercentage` | false, 2, 5, 70 | |

`agent-admission/values.yaml` holds `admission.params.registryPrefix`, `.imageSource`, `.trustedRepositories`, `.supportedChassisDigests`, and `admission.chassisVersionAction` (`Deny` or `Warn`, rule 9's binding); any other key under `admission` is refused.

## Not here

- The namespace labels (`agents.platform/admission`, `agents.platform/pod-shape`), the Pod Security labels, and the namespace's own default deny: the platform's, on the namespace (`kind/poc05/base/`).
- A deny of the cloud metadata service and the Kubernetes API by name: a NetworkPolicy can only allow, so the chart allows named pod peers and nothing else (026 CH-4).
- Making Secrets: outside the chart, through a pipe, as `kind/poc05/platform/seed.sh`.
- A kind run with this chart beyond the HPA drill (PoC-9, optional; `../../pocs/poc-09-agent-mvp-template/notes/2026-11-05-kind-hpa-drill.md`).
