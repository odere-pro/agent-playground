# PoC-5: a sandbox per code-runner call

Status: in progress

Approved by the user on 2026-10-09.
Date: 2026-10-09
Author: `chassis-architect`. The orchestrator assigns the tasks in "Tasks".
Source: [security review of the cluster work](../../pocs/poc-05-sandboxed/notes/2026-10-09-review-security-cluster.md) (HIGH, first MEDIUM), and the user's decision of 2026-10-09 to build a sandbox per call in PoC-5.
Parent plan: [PoC-5 plan](2026-10-02-poc-05-sandboxed.md), sections 2.8, 2.10, 2.11. Criterion moved: 8 (code runs sandboxed), and the code-runner part of B12.

Values the epic does not give are marked `suggested:`. Nothing here was run on the cluster; items marked **cluster proves** wait for a kind run.

Review: `platform-security` approved with four required changes (same note, section "Review of the per-call sandbox design, 2026-10-09"). They are folded in below: admission for templates and claims, the `can-i` refusals, the two HTTP clients, and the tenant fix.

## Problem

The code runner is one shared gVisor `Sandbox`. One call, within `RLIMIT_NPROC`, can push it to its 256Mi limit; the liveness probe fails and the kubelet restarts it for every caller (bring-up note, 2026-10-09). Files a call leaves in `/tmp` or `/dev/shm` reach the next caller, since every call runs as uid 10003. Both go away if each call gets a fresh sandbox that is deleted after the call.

## agent-sandbox v1.0.5 can do it

The v1.0.5 release has three assets: `sandbox.yaml` (what we install today: the `Sandbox` CRD only), `extensions.yaml`, and `sandbox-with-extensions.yaml` (sha256 `b150cb058c577c59c42b060ff7f22e31b5311ca80430db98129f1280a0e85970`). The extensions add, in group `extensions.agents.x-k8s.io/v1beta1`:

- `SandboxTemplate`: a `podTemplate`, plus `networkPolicyManagement` (`Managed` default, or `Unmanaged`), `envVarsInjectionPolicy` (default `Disallowed`), `volumeClaimTemplatesPolicy` (default `Disallowed`), `service`.
- `SandboxWarmPool`: `replicas` (default 1), `sandboxTemplateRef`, `updateStrategy` (`OnReplenish` default).
- `SandboxClaim`: `warmPoolRef` (required), `lifecycle` (`shutdownTime`, `shutdownPolicy` `Delete | DeleteForeground | Retain`, default `Retain`), `env`, `additionalPodMetadata`. Status gives `sandbox.name` and `sandbox.podIPs`.

From the v1.0.5 source (`extensions/controllers/sandboxclaim_controller.go`):

- A claim adopts a ready pool sandbox and becomes its controller owner, so deleting the claim deletes the `Sandbox` and its pod.
- If no warm sandbox has an IP within `warmCandidateGracePeriod` (2 s), the claim cold-starts a sandbox from the pool's template. An empty pool is slow, not a failure.
- At `shutdownTime` the controller deletes the claim's resources; with `Delete` it deletes the claim too. This is our backstop for orphans.
- `Managed` network policy with no rules is "ingress from the sandbox router, egress to the public internet". That is wrong for us. We set `Unmanaged` and ship our own policy.

So no fallback is needed. The controller binary is the same image (`--extensions` flag); the install switches from `sandbox.yaml` to `sandbox-with-extensions.yaml`.

## Decision: a thin dispatcher in front of a warm pool

**Choice:** the code-runner MCP server gains a dispatch mode. The dispatcher is the only MCP server LiteLLM registers as `code_runner`. Per call it creates a `SandboxClaim`, waits for it, calls `run_python` on the claimed pod, and deletes the claim. It never runs code itself.

Why the dispatcher and not the others:

- **Chassis: forbidden.** The Kubernetes API is in every chassis pod's egress deny list (ADR-001 hard requirement 1), and a cluster API client is a product SDK the chassis must not carry. It would also differ per lane: a `remote` chassis may not even run in our cluster.
- **LiteLLM: no seam.** It has no per-call hook to create a Kubernetes object, and we do not patch it.
- **Dispatcher: the right place.** It sits behind the MCP gateway, so the per-key allow-list, the chassis's `ToolPort`, and every lane stay as they are. Its one credential is a Kubernetes service account token with rights only on `SandboxClaim` in one namespace. It holds no model or tool key. The untrusted code never runs in its pod.

Shape:

- Same package and image (`packages/code-runner`), new module `code_runner/dispatch.py`, started as `code-runner dispatch --pool code-runner --namespace poc05-tools`. The per-call pod runs the image's default entrypoint, as today.
- The idempotency `ResultCache` moves in front: the dispatcher keeps it. A repeated key returns the first result without a claim. A failed call is forgotten, so a retry with the same key runs again in a fresh sandbox; that is safe because the sandbox has no egress and nothing survives it.
- Two separate `httpx` clients, never shared. No Kubernetes SDK.
  - **API client:** base URL fixed at start from `KUBERNETES_SERVICE_HOST` and `KUBERNETES_SERVICE_PORT`; `verify` is the mounted cluster CA; `follow_redirects=False`; `trust_env=False`. It is the only client that sends the token, read from the projected file on each request (the token rotates). It sends only `create`, `get`, and `delete` on `sandboxclaims` in `poc05-tools`.
  - **Sandbox client:** no `Authorization` header and no auth of any kind; `follow_redirects=False`; `trust_env=False`. The URL is built only from `status.sandbox.podIPs[0]`, parsed as an IP address (anything else is `sandbox_unavailable`), port 8000, path `/mcp/`. The response body is capped at 256 KiB (`suggested:`; the runner caps each stream at 64 KiB); over the cap the call is `sandbox_lost`.
- Nothing from the caller goes into a Kubernetes object. The claim has a generated name, the pool name, and `lifecycle` only; no `env`, no labels.

Per call:

1. Take a slot (`suggested:` 2 concurrent claims). Wait for one up to the claim timeout.
2. `POST` a `SandboxClaim` (`generateName: call-`, `warmPoolRef: code-runner`, `shutdownTime: now + 60 s` `suggested:`, `shutdownPolicy: Delete`).
3. `GET` it every 100 ms (`suggested:`) until `Ready` is `True` and `status.sandbox.podIPs` is set; claim timeout 20 s (`suggested:`, covers a cold start under host load).
4. Open an MCP session to `http://<podIP>:8000/mcp/` and call `run_python` with the same code, timeout, and key. Client timeout: `timeout_s + 5 s` (`suggested:`). Retry the connect for up to 2 s (`suggested:`), in case `Ready` comes before the server listens (**cluster proves**).
5. `DELETE` the claim (background propagation) in a `finally`, on every path. A failed delete is logged as a count; `shutdownTime` removes it later.

The dispatcher runs on `runc` in `poc05-platform`, not in `poc05-tools`: admission rule 7d refuses any tool-shape pod that mounts a token, and that rule should stay strict. `poc05-platform` is not bound to the trust policy; the dispatcher is a platform component like LiteLLM.

## RBAC

- ServiceAccount `code-runner-dispatch` in `poc05-platform`, `automountServiceAccountToken: false`. The dispatcher pod mounts a projected token (`expirationSeconds: 3600`, `suggested:`) in its one container.
- Role `code-runner-claims` in `poc05-tools`: apiGroup `extensions.agents.x-k8s.io`, resource `sandboxclaims`, verbs `create`, `get`, `delete`. No `list` or `watch`: orphans are the controller's job through `shutdownTime`.
- RoleBinding in `poc05-tools` to `system:serviceaccount:poc05-platform:code-runner-dispatch`.
- ResourceQuota in `poc05-tools` (`suggested:`): `count/sandboxclaims.extensions.agents.x-k8s.io: 4`, `pods: 6`, `limits.memory: 1536Mi`. A stolen dispatcher token can then start at most a handful of sandboxes.
- The Role, RoleBinding, and ResourceQuota are applied by the cluster admin with the admission objects, not by the deployer: the deployer has no RBAC rights. The deployer's ClusterRole gains `sandboxtemplates` and `sandboxwarmpools` (group `extensions.agents.x-k8s.io`) so `run.sh workloads` can apply `tools/`; it gets no `sandboxclaims`.
- The controller's own `agent-sandbox-controller-extensions` ClusterRole includes cluster-wide `networkpolicies` write. With `Unmanaged` it creates none, and admission now refuses any template that is not `Unmanaged`. We keep the upstream role (its informers may need it) and record the breadth in the blind-spots note. The with-extensions manifest has no `aggregate-to-` label (grep on the v1.0.5 file: no match), so none of its roles flows into `admin`, `edit`, or `view`.

What a stolen dispatcher token can and cannot do. It cannot pick or change a template or pool, cannot change a claim after creation, and cannot reach another namespace. It can delete other callers' claims, which ends their calls with `sandbox_lost`: a denial of service, recorded in the blind-spots note. The kind test proves the refusals with `kubectl auth can-i --as=system:serviceaccount:poc05-platform:code-runner-dispatch`:

- Allowed (the control): `create`, `get`, `delete` `sandboxclaims -n poc05-tools`.
- Refused: `list`, `watch`, `update`, `patch` `sandboxclaims -n poc05-tools`; `create sandboxclaims -n poc05-remote`; `create sandboxtemplates` and `create sandboxwarmpools -n poc05-tools`; `create pods` and `get secrets -n poc05-tools`; `get secrets -n poc05-platform`.

**Tenant fix: drop the submitter's binding in `poc05-tools`.** Today `poc05-tenant/submitter` may create and patch pods there (`admission/rbac.yaml`, RoleBinding `agent-submitter` in `poc05-tools`), so a tenant could fill the shared quota or patch a warm pod. We drop that RoleBinding rather than move the pool to its own namespace, because:

- `poc05-tools` holds platform tools, not tenant workloads. A tenant reaches a tool only through the gateway. No PoC-5 flow needs a tenant to create a pod there.
- A new namespace adds a default-deny policy, a shape label, an admission binding, a quota, and run.sh steps, and still leaves the submitter able to start tool-shape pods next to it.

Cost: `test_poc05_kind_admission.py` sends tool-shape fixtures in `poc05-tools` as the submitter today (`Fixture.user`). They move to the deployer, like the `poc05-agents` fixtures. Kind checks: the submitter gets `no` for `create pods` in `poc05-tools`, and for `create` on `sandboxclaims`, `sandboxtemplates`, and `sandboxwarmpools` in every `poc05-*` namespace; `create pods -n poc05-remote` stays `yes` (the control).

## Pool, limits, and memory

The `SandboxTemplate` `code-runner` in `poc05-tools` is today's pod template with three changes: no liveness probe (a per-call pod lives seconds; a restart mid-call must not happen silently), an `emptyDir` (medium `Memory`, `sizeLimit: 8Mi`, `suggested:`) on `/dev/shm` (the review's LOW item), and memory request equal to the limit. It sets `networkPolicyManagement: Unmanaged`, `envVarsInjectionPolicy: Disallowed`, `volumeClaimTemplatesPolicy: Disallowed`, `service: false`.

Per-call pod (`suggested:`): cpu 100m request, 500m limit; memory 256Mi request and limit; `/tmp` 64Mi as today. The runner's own limits (`RLIMIT_AS` 256Mi, `NPROC_ALLOWANCE` 32) stay. They are hygiene now: a call that breaks them hurts only its own sandbox.

`SandboxWarmPool` `code-runner`: `replicas: 2` (`suggested:`). One warm pod would cold-start every second call in a burst; three adds 120 MiB for no test that needs it.

Memory, from the 2026-10-08 measurements (a gVisor Python MCP server: about 120 MiB working set including the sentry; a FastMCP runc server: about 70 MiB):

| Part | Idle | Peak (2 calls in flight, pool refilling) |
| ---- | ---- | ---------------------------------------- |
| shared code runner (removed) | -120 MiB | -120 MiB |
| warm pool, 2 sandboxes | +240 MiB | +240 MiB |
| claimed sandboxes, at most 2 | 0 | +512Mi of limits |
| dispatcher, runc, 128Mi limit (`suggested:`) | +70 MiB | +128Mi |
| controller with extensions | re-measure (34 MiB today, 64Mi limit) | same |
| **Net** | **about +190 MiB** | **about +760 MiB of limits** |

The node's working set was 3.3 GiB of 7.75 GiB. Peak stays under about 4.1 GiB. The quota caps the worst case.

Latency targets (`suggested:`, measured in task 5, not gates): a warm claim to `Ready` at p50 under 1 s; a cold start under 8 s on an idle host. Under host load (bring-up note) gVisor starts slow; the 20 s claim timeout covers it.

## NetworkPolicy

| Pod | Ingress | Egress |
| --- | ------- | ------ |
| per-call `code-runner` (tools), gVisor | 8000 only from `code-runner-dispatch` in `poc05-platform` | none, not even DNS |
| `code-runner-dispatch` (platform), runc | 8000 only from `litellm` | 8000 to `code-runner` pods in `poc05-tools`; 6443 to the API server endpoint |
| `litellm` (platform) | unchanged | `code-runner` in tools replaced by `code-runner-dispatch` on 8000 |

- The per-call pods keep the label `app.kubernetes.io/name: code-runner`, set in the template. The controller adds its own labels and keeps ours (`sandboxclaim_controller.go`, the pod-metadata merge; **cluster proves** for an adopted warm pod).
- A sandbox cannot reach another sandbox, the dispatcher, LiteLLM, or the API. LiteLLM no longer reaches a sandbox pod.
- The dispatcher needs no DNS: it uses `KUBERNETES_SERVICE_HOST` and pod IPs.
- The API server rule is the one `ipBlock` in PoC-5: the endpoint's node IP `/32`, port 6443. The spike showed kindnet sees the address after the Service rewrite, so the ClusterIP does not work. The file in git holds the sentinel `__API_SERVER_IP__/32`. `run.sh` reads the IP from the `kubernetes` EndpointSlice, fills it in, and refuses to apply if the sentinel is still there or the IP is in `169.254.0.0/16`, the pod range `10.244.0.0/16`, or the service range `10.96.0.0/16`.

`test_poc05_netpol_static.py` asserts, offline:

- exactly one `ipBlock` across every PoC-5 policy;
- it is in policy `code-runner-dispatch` in `poc05-platform`, whose `podSelector` selects only the dispatcher (`app.kubernetes.io/name: code-runner-dispatch`, and no other pod in the model carries it);
- that egress rule holds only that block, as a `/32`, with no `except`, and its ports are exactly TCP 6443;
- the file holds the sentinel, and `run.sh`'s fill step refuses the sentinel and an address in `169.254.0.0/16`, the pod range, or the service range;
- every other policy still has no `ipBlock`, as today.

Kind checks (task 6): the live block equals the `kubernetes` EndpointSlice address; from the dispatcher pod (`kubectl exec`, the image's Python) node:6443 connects and node:10250 times out; H03 and H04 still refuse for the workload, the remote, and a sandbox; H01 still finds no rule that allows `169.254.169.254`.

## Admission

The per-call pods pass rules 0 to 8 as they are. The pod template is today's code-runner template, which `up` admits: label `agents.platform/trust: trusted`, image under the registry prefix with `imagePullPolicy: Never` (rules 3 and 8), no Secret (6c), shape `tool` in a `tool` namespace (7a), `runtimeClassName: gvisor` and `automountServiceAccountToken: false` (7d). The pool creates `Sandbox` objects, which the policy already matches through `spec.podTemplate`, and then pods, which it matches too. The removed liveness probe and the new `/dev/shm` volume touch no rule.

**Templates and claims must be matched too (required, task 4).** Today the policy does not see them. A template without `networkPolicyManagement` gets `Managed`, and the controller then writes a NetworkPolicy that allows internet egress (B2), through its cluster-wide role. So:

- `agent-trust-rule` also matches `sandboxtemplates` (group `extensions.agents.x-k8s.io`, CREATE and UPDATE). Its `template` variable reads `object.spec.podTemplate` for a `SandboxTemplate`, so rules 0 to 8 apply to the template's pod at apply time, not only when the pool makes its first `Sandbox`.
- A second policy, `sandbox-extension-rule` (`suggested:` name), with its binding on the same `agents.platform/admission: enforce` namespaces, `failurePolicy: Fail`, one message per rule:
  - template rule T1: `networkPolicyManagement` is `Unmanaged` (absent counts as `Managed` and is refused);
  - template rule T2: `envVarsInjectionPolicy` is `Disallowed`;
  - template rule T3: `volumeClaimTemplatesPolicy` is `Disallowed`;
  - claim rule C1: no `spec.env`;
  - claim rule C2: no `spec.additionalPodMetadata`;
  - claim rule C3: `spec.warmPoolRef.name` is `code-runner` (`suggested:` a literal; a params key if a second pool comes);
  - claim rule C4: `spec.lifecycle.shutdownPolicy` is `Delete` and `spec.lifecycle.shutdownTime` is set;
  - claim rule C5: no `spec.volumeClaimTemplates`.
- Every `has()` is guarded, as in the trust rule, so a missing field fails for its own reason.
- Offline: one rejected fixture per rule with its admitted twin, in `test_poc05_admission_static.py`. Kind: the same fixtures with `--dry-run=server`; and no NetworkPolicy owned by a `SandboxTemplate` in any `poc05-*` namespace (`kubectl get networkpolicies -A -o json`, filter on `ownerReferences`).

The dispatcher pod is in `poc05-platform` and is not bound. It still sets the full section 2.11 hardening and `imagePullPolicy: Never`, and `test_poc05_hardening_static.py` checks it like every other pod.

## Failure modes

The dispatcher reports its errors as tool error results whose text starts with a stable code, like `run_failed` today. The chassis passes a tool error result through as `ToolResult(is_error=True)`, so no `ToolPort` code changes.

| Case | What happens | What the caller sees |
| ---- | ------------ | -------------------- |
| Pool empty | The claim cold-starts after 2 s | a slower success |
| No slot or no `Ready` within 20 s (pool empty and slow, controller down, API down) | Claim deleted; nothing ran | `sandbox_unavailable`; safe to retry with the same key |
| Sandbox dies mid-call (server OOM-killed, the pids cap ends the sandbox, eviction) | Connection error or EOF; claim deleted; key forgotten | `sandbox_lost`; a retry runs fresh |
| The child breaks its own limits or the call times out | The server in the sandbox answers as today | a normal result (`exit_code`, `timed_out`) |
| No answer within `timeout_s + 5 s`, or a response over 256 KiB | Claim deleted | `sandbox_lost` |
| Dispatcher restarts | In-flight calls drop at the gateway; their claims expire at `shutdownTime`; the key cache is lost (a recorded limit, as today) | `tool_unavailable` (retryable), from the chassis adapter |
| Delete fails | Logged as a count; `shutdownTime` deletes it | nothing |

The dispatcher's liveness probe is HTTP `/health` with `timeoutSeconds: 5`, `failureThreshold: 6` (`suggested:`, from the review). `/health` never calls the API, so a slow API server cannot restart it. The dispatcher runs streamable HTTP in stateless mode, so a restart does not strand a LiteLLM session.

## Contract and lanes

- Contracts touched: none of the envelope, the event schema, `handle`, a port, the A2A mapping, or `spec.*`. No `schema_version` bump. The code runner's own error strings gain `sandbox_unavailable` and `sandbox_lost` (its README).
- Lanes: no cost difference. `inprocess`, `sidecar`, and `remote` all reach the tool through the chassis `ToolPort`, then LiteLLM, then the dispatcher. The `remote` chassis needs nothing new.
- No framework or SDK enters the chassis. The new client code lives in `packages/code-runner`, which never imports `chassis`.
- Write down: ADR-005 gains a consequence at T30 (one platform service account with claim rights in `poc05-tools`; the one `ipBlock`; the submitter loses `poc05-tools`; the extension admission rules). No new ADR: the user already decided per call, and nothing here changes a contract.
- Threat model (task 7): B4 gets one recorded platform exception, the dispatcher's egress to the API server, with the static and kind checks above. B12 is closed for the code runner (a fresh sandbox per call, proven by the cross-call file test); the shared Valkey part stays open (PoC-8).

## Tests

Existing, to change:

- `test_code_runs_on_gvisor_with_no_egress` reads `pod(TOOLS_NS, "code-runner")`, which no longer exists. It reads `runtimeClassName` from the `SandboxTemplate` and from every `code-runner` pod instead. The code also tries the dispatcher's pod IP and the API endpoint (refused) next to its own server on 127.0.0.1:8000 (the control).
- `test_process_limit_refuses_forks_past_the_allowance` stays as it is (`/bin/cat` children). Each run is in its own sandbox now.
- `test_poc05_kind_remote_controls.py` H30 targets the `code-runner` Service, which goes away. It targets the dispatcher Service, with a same-call control that the target answers from an allowed peer (the review's LOW item).
- Static: `test_poc05_netpol_static.py` (new edges; the `ipBlock` assertions above), `test_poc05_hardening_static.py` (`test_the_code_runner_has_no_egress_and_only_litellm_in` becomes "only the dispatcher in"; the dispatcher pod added to the section 2.11 checks: non-root uid, read-only root, drop ALL, seccomp, limits, projected token in its one container only), `test_poc05_seed_static.py` (server names unchanged; URL check), `test_poc05_admission_static.py` (the template and claim rules, a rejected fixture and twin each).

New, offline (`packages/code-runner/tests/test_code_runner_dispatch.py`, a fake API and a fake sandbox on `httpx.MockTransport`, the runner server in process):

- create, get, run, delete in that order; delete on every error path; a repeated key makes no second claim;
- claim timeout gives `sandbox_unavailable`; a dropped session gives `sandbox_lost` and frees the key; a sandbox response over the cap gives `sandbox_lost`;
- the claim body holds no caller data; only `create`, `get`, and `delete` on `sandboxclaims` are ever sent;
- the fake sandbox records every request header, and none carries `Authorization` (the token never reaches a sandbox); the control: every fake API request carries it;
- the API client does not follow a redirect from the fake API; a `podIPs` value that is not an IP gives `sandbox_unavailable` and no sandbox request.

New, on kind (in `test_poc05_kind_code_runner.py`, so `run.sh test-remote` selects them with no wiring change):

- A 31-Python-child burst (the original `FORKS` without `exec`) returns a result or `sandbox_lost`. Then a 5-child call works, a call run concurrently with the burst works, and the dispatcher's `restartCount` is unchanged.
- Call A writes `/tmp/x` and `/dev/shm/x` and reads them back (the control) and prints its hostname. Call B sees neither file, and its hostname differs.
- After a call its claim and pod are gone within 10 s (`suggested:`).
- The dispatcher's and the submitter's `can-i` lists in "RBAC", each `no` next to its `yes` control.
- The template and claim admission fixtures, and no `SandboxTemplate`-owned NetworkPolicy (section "Admission").
- The `ipBlock` kind checks (section "NetworkPolicy").

## Tasks

One agent at a time on kind. Every task ends with `make quick` and pastes its output.

| # | Task | Agent | Files | Done check | Cluster |
| - | ---- | ----- | ----- | ---------- | ------- |
| 1 | Dispatcher | `developer` | `packages/code-runner/src/code_runner/dispatch.py`, `cli.py`, `README.md`, `tests/test_code_runner_dispatch.py`. Dependency change, T01's files, the orchestrator approves: add `"httpx>=0.28,<0.29"` to `dependencies` in `packages/code-runner/pyproject.toml` (today only fastmcp and uvicorn; httpx 0.28.1 is already in `uv.lock` through fastmcp), then `uv lock` | `uv run pytest packages/code-runner -q`; `make lint` (no `chassis` import) | n |
| 2 | Manifests | `developer` (`platform-security` reviews) | `deploy/kind/poc05/base/agent-sandbox/` (the with-extensions manifest, its sha256 in `run.sh`), `tools/code-runner.yaml` (template, pool; the `Sandbox` and Service removed), `tools/network-policy.yaml`, new `platform/code-runner-dispatch.yaml`, `platform/network-policy.yaml` (with the sentinel), `platform/litellm/config.yaml`, `run.sh` (node IP fill and its refusals, wait on pool `readyReplicas` instead of `wait_sandbox code-runner`) | `kubectl kustomize` of each folder renders; task 3 green | n |
| 3 | Static tests | `tester` | the four static files under "Tests" | `make test-poc POC=05` | n |
| 4 | Admission and RBAC for the extensions (required) | `developer` (`platform-security` reviews) | `deploy/kind/poc05/admission/policy.yaml` (match `sandboxtemplates`), new `admission/extension-policy.yaml` and its binding, `admission/rbac.yaml` (drop the submitter's `poc05-tools` binding; deployer gains templates and pools; the dispatcher's Role, RoleBinding, and the quota), `admission/fixtures/` (a rejected fixture and twin per rule T1 to T3, C1 to C5) | `make test-poc POC=05` | n |
| 5 | Bring-up | `tester` | `notes/2026-10-02-bring-up.md`, new section | `run.sh up` green; pool `readyReplicas` 2; the `can-i` lines; memory per pod; warm and cold claim latency | **y** |
| 6 | Kind tests | `tester` | `tests/test_poc05_kind_code_runner.py`, `tests/test_poc05_kind_remote_controls.py`, `tests/test_poc05_kind_admission.py` (tool fixtures as the deployer; the extension fixtures; the `can-i` lists; no template-owned NetworkPolicy) | `run.sh test-remote` green on an idle host; the kind tier green | **y** |
| 7 | Close the loop | `docs-editor` | blind-spots note (claim deletion as denial of service; the controller's cluster-wide NetworkPolicy role), threat model (B4 exception, B12 for the code runner), ADR-005 consequence, the review's disposition, link from the PoC-5 plan section 2.8 | `make planning-check` | n |

Order: 1, then 2, 3, and 4 (4 before any cluster run), then 5, 6, 7.

## Risks (cluster proves)

- `Ready` on a claim may come before the server listens. The connect retry covers it.
- The controller may rewrite labels on an adopted warm pod. If it drops `app.kubernetes.io/name`, every policy misses the pod; task 5 checks the labels on a claimed pod.
- Whether the pool replaces an unclaimed pod that crashed. Task 5 kills one and watches.
- Object-count quota for a CRD (`count/<resource>.<group>`). Task 5 creates a fifth claim and expects it refused.
- The `ipBlock` allow to node IP:6443 on kindnet. The spike proved the deny form, not the allow form.
