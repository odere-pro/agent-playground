# ADR-001: How the service chassis runs next to a service

- **Date:** 2026-09-28
- **Deciders:** Oleksandr (epic owner) and the delivery team
- **Format:** Michael Nygard's template: Status, Context, Decision, Consequences
- **Related:** [PoC plan](../poc/000-plan.md), [reuse analysis](../poc/010-reuse-analysis.md), [PoC-5 sandbox](../poc/005-PoC-5-sandboxed.md), [backlog plan](../issues/000-plan.md), [open decisions](../issues/001-DEC-1-resolve-open-decisions.md)
- **Implemented by:** [009 CH-1](../issues/009-CH-1-engine-connectors-a2a.md), [013 CH-2](../issues/013-CH-2-outbound-model-proxy.md), [024 CH-3](../issues/024-CH-3-helm-library-chart.md), [026 CH-4](../issues/026-CH-4-chassis-only-credentials-egress.md), [050 CH-5](../issues/050-CH-5-service-operations.md), [055 CH-6](../issues/055-CH-6-remote-lane-trust-rule.md), [056 CH-7](../issues/056-CH-7-chassis-release-rings.md), [058 CH-8](../issues/058-CH-8-framework-event-mappings.md). Follow-ups the ADR does not settle are listed in the [backlog plan](../issues/000-plan.md#adr-001-follow-ups).

## Status

Accepted, 2026-09-29.

**Conclusion: option I5.**

- The chassis always runs in front of the service's code. Every request passes through it.
- By default, the chassis runs as a sidecar container in the same pod, and calls the code over A2A.
- Untrusted code and managed runtimes are reached through the `remote` lane. Tests run everything in one process.

**Hard requirements.** The decision holds only if both of these are met:

1. **Every internal service requires a credential that only the chassis holds.** This covers LiteLLM, the MCP gateway, the broker, the state store, and the config store. Each pod may send traffic only to what its chassis needs. The workload shares the pod's network, so this rule is what stops it from reaching anything on its own. Three more paths in the pod must be closed too: the pod's service account token is not mounted into the workload (`automountServiceAccountToken: false`, or the token projected into the chassis container only); the cloud metadata service and the Kubernetes API are in the egress deny list; and the chassis's public port binds to the pod IP, so localhost carries only the model and tool proxies.
2. **Every lane is tested on every commit.** The contract suite runs each case two ways: over A2A on localhost, as in production, and over A2A in memory, as in the chassis's own tests. The wire contract is the same both ways. CI also runs a fake workload through the `remote` lane. A lane that is not tested this way is not supported.

History:

- **Draft 1** proposed a standard sidecar plus a thin SDK.
- **Draft 2** came from a two-round debate, with one advocate per option, judged against the owner's criteria. It chose I5.
- **Draft 3** records the owner's review. The sidecar is the default lane, A2A is the one contract, and the trust rule is set.
- **Accepted** on 2026-09-29, as Draft 3.
- **Amended** on 2026-09-29, after a review of the PoC track. Hard requirement 1 names the service account token, the metadata service, and the chassis's localhost ports. Item 6 keys every outbound call to its inbound request. Item 8 decides how a `remote` workload reaches the proxies. The `inprocess` lane runs A2A in memory instead of a direct call. The Revisit list gains an A2A overhead trigger.

## Context

The agent server has grown into a **service chassis**: one standard that every microservice is built with. The agent is its first profile. The chassis owns every standard functional requirement:

- inbound adapters that map native REST, OpenAI, Anthropic, MCP, A2A, and CloudEvents to one canonical request;
- a pipeline that runs auth, limits, guardrails, idempotency, config, budgets, the evaluator gate, AI marking, events, and telemetry;
- an engine connector (`inprocess`, `sidecar`, or `remote`) that runs the workload;
- outbound proxies for model, tool, and event calls.

The service keeps its business logic, and it may add operations of its own. This ADR decides **where the chassis runs relative to the service**.

The forces at play:

- **Run any software.** Most services will be Python agents, on the epic's [stack](../slm-agent-platform-epic-v3.md#app-h). The MVP must also front one agent in another language and one remote solution. Third-party images and managed runtimes must fit too.
- **Untrusted code.** Untrusted or generated code must not reach secrets, the internet, the disk, or tools that are not allowed.
- **Swappable and testable from day 0.** Every dependency sits behind a port with a fake. `make test` passes offline with no keys.
- **Stateless.** It scales by adding replicas.
- **Small team.** About one engineer for the PoC track. Every extra artifact, version, and failure mode costs real time.
- **Many services later.** A chassis fix must reach every service quickly and safely.

Facts that shape the choice:

- Containers in one Kubernetes pod share localhost. They do not share file systems, processes, secrets, or resource limits. One exception: the pod's service account token is projected into every container unless the pod says otherwise.
- The sandbox runtime (`runtimeClassName`: gVisor, Kata) and network policy are set per pod, not per container. To sandbox a workload but not the chassis, they must run in different pods. A workload beside a chassis sidecar can reach whatever the pod can reach.
- Kubernetes native sidecars are stable (v1.33). Cloud Run supports sidecar containers. AWS Bedrock AgentCore Runtime and Lambda take one container.
- LiteLLM holds the provider keys. A LiteLLM virtual key can be limited to certain models, budgets, rate limits, MCP servers, and tools. So a service needs only a scoped key.
- Frameworks call models over HTTP with their own clients, pointed at a base URL. Rich framework events (token deltas, graph steps, handoffs) can be read only in the framework's own process.
- Code in the same process as the controls can get around them.
- In one Python environment, the chassis and a framework share dependency versions, and these can clash.
- A sidecar adds one local hop and some memory per replica. Dapr measures about 1.4 ms at p90 for two sidecars. Model latency is far larger. A Python sidecar's memory is suggested: 80–200 MB, not yet measured.
- An in-process chassis upgrade means rebuilding each service. Renovate can automate those pull requests. A sidecar upgrade needs no rebuild, but it restarts every pod, and a bad release reaches every service at once.

Options considered: the four that scored highest. The IDs match the debate record.

| # | Option | In short |
| - | ------ | -------- |
| I3 | Sidecar only | A standard chassis sidecar in every pod, holding the credentials; plain HTTP to the service |
| I4 | Sidecar plus a thin SDK | I3, plus an optional SDK per language, an entrypoint mode, and a separate pod for untrusted code (Draft 1) |
| I5 | Chassis in front, workload by connector | One package and one generic image; the chassis is always the front process; the workload attaches `inprocess`, as a `sidecar` container, or `remote` |
| I6 | Shared chassis tier | The chassis as its own shared deployment, in front of workloads in their own pods |

## Decision

**We choose I5: the chassis always runs in front, and the workload plugs in behind it. By default, the chassis runs as a sidecar container next to each service.**

![The chassis in front, and the three lanes behind it](../diagrams/diagram-4-chassis-lanes.png)

In plain terms, the chassis is the program that takes every request. The service's code sits behind it and can be reached only through it, in one of three ways:

- **Trusted code in any language, including Python:** in its own container, next to the chassis. This is the default.
- **Untrusted code, third-party images, and managed runtimes:** in their own sandboxed pod, or at their own endpoint.
- **Tests and local runs:** inside the chassis process, so `make test` stays offline and fast.

The details, including rules taken from the other options:

1. **One codebase, one image.** The `chassis` package holds the inbound adapters, the pipeline, the ports, the connectors, and the outbound proxies. The chassis image is that package plus the `chassis serve` launcher, with no business logic. Every service runs the same image. Both come from one repo, one CI pipeline, and one version number.
2. **One `handle` contract, two transports.** A service implements `handle(input, ctx)`, which yields events in the chassis's JSON event schema. The service maps its framework's events to that schema itself, and a contract test checks the mapping. In the `sidecar` lane, a small A2A server from the service template wraps `handle` on localhost. In the chassis's own tests, the same server runs inside the chassis process and is called over A2A in memory. A service in another language serves A2A itself and emits the same schema.
3. **The chassis owns the public port.** Every request, including the service's own operations, enters through the chassis and passes the pipeline. A service declares its own operations, and the chassis routes them. The workload listens only on localhost, or on a private address in the `remote` lane.
4. **Three lanes, picked in config** (`spec.engine.connector`):

   | Lane | For | Shape |
   | ---- | --- | ----- |
   | `sidecar` (default) | Every trusted service, in any language | Two containers in one pod. The chassis is in front and holds the scoped key. The workload serves A2A on localhost only, and its model and MCP URLs point at the chassis. Its dependencies never mix with the chassis's |
   | `remote` | Untrusted workloads (see the trust rule), managed runtimes, and other teams' services | The workload runs in its own pod, with gVisor and egress denied by default, or at its own endpoint. The chassis pod stays outside the sandbox |
   | `inprocess` | The chassis's own tests and local runs only | The chassis loads the template A2A server in its own process and calls it over A2A in memory (an ASGI transport, no socket), with fakes: no network, no keys, one debugger. A workload's own tests do not use it: they run the chassis as a separate process in the `fake` profile, so the workload never installs the chassis package |

5. **The trust rule decides what goes to `remote`.** A workload is trusted only if both of these hold:
   - **Source:** the owning team wrote and reviewed it, and its image is built in our registry. Third-party images fail this test.
   - **Behavior:** it does not run code, shell commands, or file writes itself. Frameworks that do, such as smolagents or the Claude Agent SDK with its shell and file tools switched on, fail this test.

   Everything else is untrusted and runs in the `remote` lane. Two things do not move the line:

   - **Generated code** does not make an agent untrusted when it runs through the code-execution tool (agent-sandbox, or E2B) behind `ToolPort`.
   - **Prompt injection** does not either. A hijacked agent still reaches models and tools only through its scoped key and the gateway allow-lists.

   The agent config declares `spec.trust: trusted | untrusted`. An admission check enforces it: a workload that fails either test cannot deploy in the `sidecar` lane.
6. **Hard limits live in shared services, not in the chassis.** LiteLLM holds the provider keys, and each service gets its own scoped virtual key: models, budgets, rate limits, and guardrails. The MCP gateway holds a tool allow-list per key. Keys, tool grants, identities, and network labels belong to one service each, and are never shared. Network policy denies egress by default for every workload pod. RuntimeClass sandboxes `remote` pods. The chassis's model and tool proxies are thin pass-throughs: they count, trace, and apply per-call budgets. The proxies key every outbound call to its inbound request by `traceparent`, which the workload's HTTP client propagates (suggested: through OpenTelemetry httpx instrumentation). Without that, two concurrent requests in one replica could not get separate budgets. A call with no trace id counts against a per-replica budget and is logged. The pipeline runs every stage in every lane, but it is not the security boundary against hostile code. Hostile code never shares the chassis's process or pod.
7. **A2A is the one contract to workloads, and there is no SDK.** The `sidecar` and `remote` lanes use one A2A client with a different URL. AWS Bedrock AgentCore and Google Vertex AI Agent Engine both support A2A natively. Each cloud's auth (SigV4 or OAuth 2.0 on AWS, Google auth on GCP) is a small adapter behind the connector. An OpenAI-compatible adapter is added only for a workload that cannot speak A2A.
8. **Managed runtimes are reached, not hosted.** Agents on AgentCore, Lambda, or Vertex AI Agent Engine are called through the `remote` lane, and the chassis stays in our cluster. A `remote` workload gets models and tools the way a `sidecar` workload does: through the chassis's proxies. For that lane the proxies also listen on the pod IP and authenticate each remote (suggested: mTLS through cert-manager, or a per-remote token set in the chassis config). A managed runtime that cannot be pointed at the proxies holds its own scoped LiteLLM key and MCP gateway grant instead. That is the one allowed relaxation of hard requirement 1. It is recorded per service in `spec.engine`, and the key is scoped like the chassis's own. To reach other MCP servers, tools, or services, we add a connector to the chassis.
9. **Updates are one image, rolled out in rings.** The chassis tag is set once, in a shared Helm library chart that every service includes. A release rolls out in rings (canary, then 10%, then all) with no service rebuild. A service can pin the previous tag to roll back. `/manifest` reports the version. An admission rule (Kyverno or ValidatingAdmissionPolicy) rejects pods below a minimum version: it warns first, then enforces.
10. **Urgent fixes are one build.** First, change the central limits if that helps: key, allow-list, or network policy. They take effect at once. Then build one chassis image and roll it out in fast rings. No service is rebuilt.
11. **The hard requirements are release gates.** The two rules in the conclusion, under Status, gate every chassis release and every new service. A release or a service that fails either one does not ship.

### Why this option

Each option was scored from 1 (poor) to 5 (strong) on the owner's four criteria, equally weighted. Scalability and ease of update count under simplicity and effectiveness.

| Option | Simple to maintain | Effective | Easy to extend | Aligned with the goal | Total /20 |
| ------ | ------------------ | --------- | -------------- | --------------------- | --------- |
| I3 Sidecar only | 3 | 3 | 4 | 3 | 13 |
| I4 Sidecar plus SDK | 2 | 4 | 4 | 4 | 14 |
| **I5 Chassis in front** | **4** | **4** | **5** | **5** | **18** |
| I6 Shared tier | 3 | 3 | 4 | 3 | 13 |

The scores compare the options as debated, with `inprocess` as I5's default. The owner's review then made `sidecar` the default. That change stays within I5, and the reasons follow.

The deciding points:

- **I5 covers the goal with the fewest parts.** It is the only option that meets the MVP's three engine kinds with one codebase and one contract suite. The debate converged on it. After rebuttals, the I6 advocate named a variant of I5 as its fallback. The I3 and I4 advocates named I5's shape with the sidecar as the default.
- **The default lane is `sidecar`.** The owner chose it over `inprocess` for three reasons:
  - **One update path.** A chassis fix is one image and a ring rollout, with no rebuild of any service.
  - **Isolation.** The workload holds no key, and its dependencies never clash with the chassis's.
  - **No lost depth.** Workloads map their framework events to our JSON schema and stream them over A2A, so no SDK is needed.

  The price is a second container in every pod: about $1–2 more per replica per month (see Consequences). It is accepted.

Adopted from the other options:

- **I3 and I4:** the sidecar as the default lane; no local API of our own, so A2A goes to sidecars and remotes.
- **I6:** ring rollouts, and the contract suite as a gate before a service goes live.

Rejected:

- **I3**, after its rebuttal, is close to this decision. I5 adds the connector model, which keeps one contract for every lane, and the in-process lane for tests.
- **I4** adds an SDK per language and an N−1 compatibility rule. The shared event schema makes the SDK unnecessary.
- **I6** puts every service behind one shared tier, so they share its failures. It adds a network hop to every call. It keeps the chassis in front only through network policy, not by sharing the pod.

## Consequences

Cost estimate, per month, at on-demand list prices:

| Cloud and fleet | Sidecar | One container | Sidecar costs more by |
| --------------- | ------- | ------------- | --------------------- |
| GKE, 1 replica | $2.07–4.14 | $0.87–2.07 | $1.20–2.07 |
| GKE, 30 replicas (10 services × 3) | $62–124 | $26–62 | $36–62 |
| GKE, 90 replicas (30 services × 3) | $186–373 | $78–186 | $108–186 |
| AWS, 1 replica | $1.88–3.77 | $0.79–1.88 | $1.09–1.89 |
| AWS, 30 replicas | $56–113 | $24–56 | $33–57 |
| AWS, 90 replicas | $169–339 | $71–169 | $98–170 |

At 90 replicas, the sidecar costs about $1.2–2.2k more a year on either cloud.

- **Assumptions (suggested, not measured):**
  - The sidecar reserves 0.05–0.1 vCPU and 128–256 MiB.
  - In one container, the chassis adds 0.02–0.05 vCPU and 64–128 MiB to the service container.
  - The chassis holds no named-entity model. Its own PII redaction is regex only. Presidio runs in LiteLLM and in the OpenTelemetry Collector. Presidio inside the chassis would add about 0.5–1 GiB per replica and break this estimate.
  - Dapr is not in this table. If it is kept (decided in PoC-4), it is a third container in every pod, with its own reservation.
- **Prices:** GKE Autopilot in us-central1, and AWS Fargate in us-east-1, for 730 hours a month.
- **Not included:** cluster fees, the gateways, and model tokens. Every option pays them equally.
- **What drives the bill:** the sidecar's reserved CPU times the number of replicas, paid around the clock, even when idle. CPU is about 85–90% of it; memory barely matters. Traffic per replica, and the work done inside the sidecar, set how much CPU it needs.

How to run it cheaper, in order of impact:

1. **Right-size the CPU reservation** from a load test, at 95th-percentile usage. Let it burst where the platform allows.
2. **Cut idle replicas.** Autoscale on load with HPA or KEDA. Use fewer minimum replicas for non-critical agents. Scale rarely used agents to zero, which costs a cold start.
3. **Keep the sidecar thin.** Guardrails and PII checks stay in LiteLLM.
4. **Use cheaper compute.** Arm is about 20% cheaper on both clouds. Spot is often 60–70% cheaper or more; use it for dev and staging. Use commitment discounts for production. These figures are approximate, so check current prices.
5. **Turn dev and staging off at night.**
6. **Later, only if the bill still matters:** rewrite the sidecar in Go or Rust. The price is a second codebase.

Pros:

- **One update path.** A chassis release is one image, rolled out in rings, with no service rebuild. A bad release stops at the first ring, and a service can pin the previous tag.
- **One chassis for every language.** One image, one codebase, and one test suite serve Python, TypeScript, or any other language. This saves artifacts to build, patch, and test. It does not save running containers: each replica still runs two.
- **One event standard.** Every workload reports its progress in the same JSON event schema, whatever its framework or language. So streaming, traces, and tests look the same everywhere. This takes some integration work: each service maps its framework's events once. The service template ships the mappings for the supported frameworks, and a contract test checks them.
- **Flexible.** The workload's image, language, and dependencies change without touching the chassis, and the reverse. Each container has its own resource limits, so a leaking workload cannot starve the chassis.
- **Isolated by default.** The workload holds no key, and its dependencies never clash with the chassis's. Security holds even if the pipeline is bypassed, because the hard limits live outside the chassis.
- **One service can be cut off alone.** Revoking its key, tool grants, and network label isolates it without touching the others. The playbook for a compromised agent is out of scope here.
- **Easy to extend.** Any language, managed runtime, or untrusted workload fits with no change to the chassis core. A new MCP server, tool, or service needs only a new connector.
- **Easy to test and debug.**
  - `make test` runs the whole chassis in-process with fakes, offline, with one debugger.
  - `docker compose up` runs both containers on a shared network, with a debugger attached to each.
  - One trace ID links the logs of both containers.
  - A2A is JSON over HTTP, so a workload can be called with `curl` or an A2A inspector.
- **Less to keep in step than Draft 1.** Draft 1 had three parts with their own versions: the sidecar, an SDK inside every service, and a local API between them. Now there is one: the chassis image. Workloads talk to it over A2A, a public standard that we do not version ourselves.
- **The sidecar is added in plain sight.** Every service's deployment includes a shared Helm chart that adds the chassis container. A cluster webhook could add it automatically, but that is hidden and harder to debug.

Cons:

- **A second container in every pod.** It costs about $1–2 more per replica per month than one container. See the cost estimate above.
- **One local hop per call.** Suggested: 1–3 ms, under 1% of a model call. A2A does not lower it. To keep it small, stream events straight through, keep connections open, and parse each message once. Streaming multiplies the hop: every token delta is one A2A event, parsed on both sides. PoC-2 measures the overhead per streamed delta and the time to first token, because that sets the sidecar's CPU reservation.
- **No separate scaling.** The chassis and the workload share a pod, so they add replicas together. Scaling is the same as with one container.
- **One bad release reaches every service.** Rings, the canary, and per-service pins limit this, but do not remove it.
- **The workload shares the pod's network.** It can reach every address the chassis can reach. Hard requirement 1 closes this. Without the chassis's credentials it gets nowhere, and it can use models and tools only through the chassis.
- **Two transports to keep equal.** `handle` is served over A2A on localhost in production and over A2A in memory in the chassis's tests. The wire contract is the same, but the transport differs, so a bug can still hide in the difference. The `remote` lane may also break unnoticed if few services use it. Hard requirement 2 closes both.
- **Services can expose only what the chassis can carry.** Only the chassis listens to the outside world. So a service can offer only HTTP calls that return one answer or a stream, each declared with a name, a path, and an input schema. The chassis's size limits and timeouts apply to every call. Until the chassis gains an adapter for them, this rules out:
  - two-way connections, such as WebSockets;
  - other protocols, such as raw TCP;
  - very large uploads or downloads, and calls that run longer than the chassis timeout.

  This is bad because a team that needs one of these must wait for an adapter, so the chassis can become a bottleneck. The chassis does not aim to support every protocol: HTTP covers almost every case, and gRPC, if needed, comes through A2A's gRPC transport. It is still worth it: every call gets auth, limits, and telemetry, with no way around them.

## Revisit

Revisit this decision when:

- **The sidecar costs too much.** If its measured CPU, memory, or latency is far above the numbers in the cost estimate, allow `inprocess` in production for small, trusted Python services.
- **The A2A hop costs too much per token.** If the A2A mapping drops events, or the measured overhead per streamed delta is far above the estimate (suggested: 0.5 ms), replace the localhost transport with plain HTTP and NDJSON of chassis events. The event schema stays. Only the wrapper changes, and A2A stays the contract for the `remote` lane and the public endpoint.

## Sources

- Microservice chassis pattern: <https://microservices.io/patterns/microservice-chassis.html>
- Sidecar pattern: <https://learn.microsoft.com/en-us/azure/architecture/patterns/sidecar>
- Kubernetes sidecar containers: <https://kubernetes.io/docs/concepts/workloads/pods/sidecar-containers/>
- Kubernetes RuntimeClass: <https://kubernetes.io/docs/concepts/containers/runtime-class/>
- Cloud Run multi-container deployments: <https://cloud.google.com/blog/products/serverless/cloud-run-now-supports-multi-container-deployments>
- AgentCore A2A contract: <https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-a2a-protocol-contract.html>
- Vertex AI Agent Engine A2A: <https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/use/a2a>
- LiteLLM virtual keys and MCP permissions: <https://docs.litellm.ai/docs/proxy/virtual_keys> and <https://docs.litellm.ai/docs/mcp_control>
- GKE pricing: <https://cloud.google.com/kubernetes-engine/pricing>
- AWS Fargate pricing: <https://aws.amazon.com/fargate/pricing/>
- Dapr service invocation performance: <https://docs.dapr.io/operations/performance-and-scalability/perf-service-invocation/>
- Agent sandbox: <https://github.com/kubernetes-sigs/agent-sandbox>
- Michael Nygard, "Documenting Architecture Decisions": <https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions>
