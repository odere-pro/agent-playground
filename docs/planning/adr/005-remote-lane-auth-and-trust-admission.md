# ADR-005: The remote lane's credential and the trust rule's admission check

- **Date:** 2026-10-02
- **Deciders:** Oleksandr (epic owner) and the delivery team
- **Format:** Michael Nygard's template: Status, Context, Decision, Consequences
- **Related:** [ADR-001](001-chassis-delivery-model.md) (items 4 to 8, hard requirements 1 and 2), [ADR-002](002-template-a2a-server-placement.md), [PoC plan](../poc/000-plan.md), [PoC-5](../poc/005-PoC-5-sandboxed.md), [022 H-6](../issues/022-H-6-security-middleware.md), [054 H-16](../issues/054-H-16-tool-port.md), [026 CH-4](../issues/026-CH-4-chassis-only-credentials-egress.md), [055 CH-6](../issues/055-CH-6-remote-lane-trust-rule.md), the PoC-5 design `docs/plans/2026-10-02-poc-05-sandboxed.md` (sections 2.2, 2.3, 2.5, 2.8, 2.10, and 9), and the threat model `pocs/poc-05-sandboxed/notes/2026-10-02-threat-model.md`

## Status

Proposed, 2026-10-02. Written before PoC-5 is built, from the design. Nothing below is measured yet. The cluster work (task T30) updates it with what the spike and the run showed, and the epic owner accepts it. Decision 6 is open until then.

## Context

ADR-001 sends untrusted code to the `remote` lane and says each remote must authenticate to the chassis's proxies (hard requirement 1). It leaves two choices open: how the remote proves who it is, and how the cluster enforces the trust rule when the submitter of a pod writes its labels.

Forces:

- The kind VM has 7.75 GiB. Every controller costs memory that the hostile suites need.
- The managed runtimes of PoC-6b can send a bearer header. Many cannot present a client certificate.
- NetworkPolicy already limits who can reach a port by pod label. It is one control. A second, independent one is wanted.
- The chassis is not the boundary against hostile code. The hard limits live in shared services: the scoped LiteLLM key, the MCP gateway's allow-list, and network policy.
- Code written by a model must run somewhere. ADR-001 routes it through a tool, so the agent stays in its lane.

Options considered:

| # | Question | Option | In short |
| - | -------- | ------ | -------- |
| A1 | The remote's credential | One bearer token per remote, used both ways | No controller, no CA; works for managed runtimes; a bearer, so it must never be logged |
| A2 | | mTLS through cert-manager | Strong identity; a controller (suggested: 150 to 250 MiB), a CA, rotation, and a client certificate in every framework's HTTP client |
| A3 | | A projected service account token with an audience | Needs the Kubernetes API from the chassis, which is in the deny list; the remote would hold a service account token |
| B1 | Where the remote's calls land | A separate listener on the pod IP, with its own auth | The loopback listener and the `sidecar` lane do not change |
| B2 | | Bind the existing proxy listener to the pod IP and add the check | Every `sidecar` workload would need the token, or the check would tell callers apart by source address, which is fragile |
| C1 | Admission engine | ValidatingAdmissionPolicy | Built into Kubernetes; no controller, no memory; rules in CEL |
| C2 | | Kyverno | A controller to run and pin; better for signatures and mutation |
| D1 | Where the trust signal comes from | The submitter's label, taken as given | The submitter could label anything `trusted` |
| D2 | | A one-way label plus a platform-owned list | `untrusted` is always believed; `trusted` counts only if the list agrees |
| E1 | Code from a model | An MCP server in a gVisor sandbox, behind the gateway's allow-list | The agent stays in its lane; the allow-list applies |
| E2 | | Run the code in the remote lane | Every coding agent becomes remote; the outcome the scope wants to avoid |

## Decision

**We choose A1, B1, C1, D2, and E1.**

1. **One random bearer token per remote, used both ways.** The chassis sends it on every A2A call to the remote. The remote sends it on every model and tool call to the chassis's remote listener on the pod IP (port 8091, suggested). A previous token is accepted for rotation without downtime. No mTLS and no cert-manager in PoC-5. Value: `openssl rand -hex 32` (suggested), held in one Secret per remote, mounted as one env var in the chassis container and the remote's workload container only.
2. **The remote listener requires a run.** Every call needs the token and a `traceparent` that names a run in flight. Otherwise 401 `remote_unauthenticated` or 403 `run_required`. A remote spends tokens and calls tools only inside a run's budget. The listener is built from an explicit route list, so a request on it cannot reach a loopback-only route.
3. **The connector never follows the agent card's URL.** It sends to the configured `spec.engine.url` and rewrites the card's interface URLs to it, so a card cannot send the token to another host.
4. **The trust rule is checked twice.** In the chassis config, `spec.trust: untrusted` needs `spec.engine.connector: remote`, and `cloud` must name the field. At admission, a ValidatingAdmissionPolicy (no Kyverno) rejects an `untrusted` pod in the `sidecar` lane, a pod with no trust label, and an image outside the registry prefix in the `sidecar` lane. It finds the lane from the pod's shape, not from a label the submitter writes.
5. **The trust signal is one-way.** `untrusted` is always believed. `trusted` counts only when a platform-owned list agrees: a ConfigMap in `agent-platform-system` that the submitter's role cannot write, listing the repositories CI builds from this repo. In PoC-5 that list is kept by hand.
6. **Code from a model runs in `code-runner`.** It is an MCP server in a gVisor sandbox with no network, behind the MCP gateway's per-key allow-list. It does not make an agent untrusted.
7. **The CNI is kindnet if it enforces egress and link-local denial, else Calico** (suggested fallback, about 200 MiB more). T30 fills this in from the spike and the cluster run.

Why not the others:

- **A2** costs a controller and a CA on a small VM, and breaks for runtimes that cannot present a certificate. NetworkPolicy already limits the port by label; the token adds the remote's own credential.
- **A3** needs the Kubernetes API from the chassis and gives the remote a service account token. Exit criterion 4 says the remote holds none.
- **B2** breaks every PoC-2 workload or relies on source addresses behind a CNI and a Service.
- **C2** stays the likely tool for image signatures (H-10) and the minimum-version rule (CH-7). The PoC-5 rules are narrow and fit in CEL.
- **D1** lets the submitter of a pod grant trust to itself.
- **E2** makes every coding agent a remote workload.

## Consequences

Pros:

- No controller and no CA to run. The memory budget keeps room for LiteLLM, gVisor, and the hostile suites.
- A leaked token opens one chassis's proxies only, from the remote's pod label, inside a run, under that run's budget.
- The `sidecar` lane keeps its PoC-2 to PoC-4 behavior and tests. The remote lane is the only one with a listener that faces another pod.
- Two independent checks on who may reach the remote: NetworkPolicy by label and the token. A pod that wrongly carries the label still gets 401.
- The trust rule holds without trusting the submitter's labels.

Cons:

- The token is a bearer. It must never enter a log, a span, an error message, or `repr`. A test greps for it.
- One token for both directions: a remote that holds it can call the chassis and a caller that holds it can call the remote. Both are scoped to one remote.
- The trust list is a hand-kept ConfigMap until signed images land.
- The template A2A server and the chassis each carry a bearer check of about 30 lines. They are not shared, on purpose (ADR-002).
- Only the Python template gets `--require-token-env` in PoC-5. The TypeScript template does not.
- The managed-runtime auth schemes (`sigv4`, `google`) are not built. `spec.engine.auth.scheme` leaves room.
- A code-runner restart forgets its cached results (suggested: the last 256 by key).

Contracts touched: additive only. `spec.trust`, `spec.engine.url`, `spec.engine.auth`, the proxy codes `remote_unauthenticated` and `run_required`, and the `ToolPort` changes go into `contract-v4.md`. The wire contract does not change, and `schema_version` stays `"0"`.

Costs across lanes: `inprocess` none (`untrusted` is refused). `sidecar` none beyond the PoC-5 bind-order change. `remote` one hop each way, one constant-time compare per request, gVisor's overhead (measured in PoC-5), and one Secret per remote.

Found while building PoC-5 (2026-10-02; this does not accept the ADR):

- **Pod create reads Secrets.** Any principal that may create a pod in a namespace can read every Secret there through that pod (env or volume). PoC-5 removed pod create from the submitter in `poc05-agents`, where the chassis Secrets live (`deploy/kind/poc05/admission/rbac.yaml`, header; PoC-5 handoff note). The submitter may submit only in `poc05-remote` and `poc05-tools`.
- **The deployer is inside the credential boundary.** The deployer (`agent-platform-system/deployer`, the stand-in for the chart or pipeline) keeps pod create and NetworkPolicy write in `poc05-agents` (`admission/rbac.yaml`). So it can read the chassis Secrets through a pod, even though RBAC gives it no access to Secrets. It must be trusted like the cluster admin for that namespace.
- **The control on the deployer is admission rules 6a to 6c.** 6a: at most one chassis container per pod. 6b: the chassis container sets no `command`. 6c: outside the remote lane, only the chassis container references a Secret. Rule 6b forbids `command`, not `args`, so the deployer can still pass the chassis any CLI flag. Fixtures: `deploy/kind/poc05/admission/fixtures/rule6b-chassis-command` and `rule6c-secret-in-workload`. The per-rule kind test has not run yet.

## Revisit

Reopen decision 1 when a cloud mesh gives mTLS with no added controller (then replace the token), or a managed runtime needs another auth scheme (PoC-6b, `auth.scheme`).

Reopen decisions 4 and 5 when image signatures land (H-10: the trust list then comes from the signature), when more than a few admission rules are needed, or when a mutation is needed (Kyverno).

Reopen decision 7 if the spike shows kindnet does not enforce egress or link-local denial, or if it enforces `AdminNetworkPolicy` and a named deny object becomes the better form.
