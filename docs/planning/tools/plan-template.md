# SLM agent platform: backlog plan

Prioritized decomposition of the [SLM agent platform epic](../slm-agent-platform-epic-v3.md) into GitHub issues.
For: Oleksandr (epic owner) and the delivery team. Date: 2026-09-28.

## How to read this backlog

- **Start with the Agent MVP PoC track** in [../poc/000-plan.md](../poc/000-plan.md). It proves the chassis first, in 9 short iterations. This backlog is the long-term reference. Its chassis waves (W2 and W5) follow [ADR-001](../adr/001-chassis-delivery-model.md), and get updated with the PoC results.
- **Reuse first.** The mature tech picks are in [../poc/010-reuse-analysis.md](../poc/010-reuse-analysis.md). Each affected issue has a `## Reuse` section: what to use, what is left to build, and any scope change.
- One file per issue. The number prefix (`001`–`119`) is the **global execution order**: work top to bottom.
- Each issue keeps its epic story ID (`H-1`, `G-2`, ...), so every issue traces back to the epic.
- Frontmatter is shaped for GitHub Issues: `title`, `labels`, `milestone`. `depends_on` and `blocks` use `NNN ID` until the issues get real numbers.
- A dependency always points to a lower number. Nothing in the list waits on something below it.
- The epic is the source of truth for design. Issues link to it and do not repeat the appendices.

## Layers: chassis, agent profile, platform

The agent server is now a **service chassis**: the standard every microservice is built with, with the agent as its first profile. Each issue carries a `layer:` label:

| Layer | Meaning | Issues |
| ----- | ------- | ------ |
| `chassis` | The standard for every service, whatever runs inside | {{N_CHASSIS}} |
| `agent-profile` | Modules only agent workloads need (model and tool proxies, LLM guardrails, evaluator gate, AI marking, A2A) | {{N_AGENT}} |
| `platform` | Services and infrastructure built on the chassis (router, SLMs, data agents, registry, orchestrator, production, governance) | {{N_PLATFORM}} |

Some issues are both `chassis` and `agent-profile`. For example, H-2 has the native REST adapter (chassis) and the OpenAI and Anthropic adapters (agent profile). Split them when the work starts.

The epic calls the chassis the "harness". Issues keep that word where they quote the epic, and the `phase:0-harness` label keeps the epic's phase name.

## How the chassis runs: ADR-001

[ADR-001](../adr/001-chassis-delivery-model.md) is accepted. The chassis always runs in front of the service's code, and every request passes through it. The workload attaches through one of three lanes, picked by `spec.engine.connector`:

| Lane | For | Shape |
| ---- | --- | ----- |
| `sidecar` (default) | Every trusted service, in any language | Two containers in one pod. The chassis holds the scoped key. The workload serves A2A on localhost only |
| `remote` | Untrusted workloads, third-party images, managed runtimes, other teams' services | The workload runs in its own sandboxed pod, or at its own endpoint, reached over A2A |
| `inprocess` | The chassis's own tests and local runs only | The chassis loads the template A2A server in its own process and calls it over A2A in memory, with fakes. A workload's own tests run the chassis as a separate process in the `fake` profile |

A2A is the one contract to workloads. There is no local API of our own, no SDK, and no single-container mode. Two hard requirements gate every release:

1. Every internal service requires a credential that only the chassis holds, and each pod may send traffic only to what its chassis needs. The workload holds no service account token, and cannot reach the cloud metadata service, the Kubernetes API, or the chassis's public port on localhost.
2. Every lane is tested on every commit: each contract case runs over A2A on localhost and over A2A in memory, and CI runs a fake workload through the `remote` lane. Until {{L:CH-6}} ships, the `remote` lane is not supported.

New chassis issues from the ADR:

- {{L:CH-1}}: the `inprocess` and `sidecar` connectors, and the template A2A server that wraps `handle`. Where that server lives is decided in [ADR-002](../adr/002-template-a2a-server-placement.md).
- {{L:CH-2}}: the outbound model proxy. The tool proxy is part of {{L:H-16}}.
- {{L:CH-3}}: the shared Helm library chart that adds the chassis container.
- {{L:CH-4}}: chassis-only credentials and default-deny egress (hard requirement 1).
- {{L:CH-5}}: service operations, declared in config and routed by the chassis.
- {{L:CH-6}}: the `remote` lane and the trust rule.
- {{L:CH-7}}: chassis release in rings, with a minimum-version rule.
- {{L:CH-8}}: framework event mappings and one non-Python workload.

How the OpenAI, Anthropic, and MCP formats map onto the one canonical request ({{L:H-2}}, {{L:H-13}}) is proposed in [ADR-003](../adr/003-chat-formats-onto-the-canonical-request.md), from PoC-3.

That result events leave through a broker client in the chassis, not Dapr ({{L:H-17}}), is decided in [ADR-004](../adr/004-events-through-a-broker-client.md), from PoC-4, accepted 2026-10-08. The broker product stays open in {{L:DEC-1}}.

How a remote workload authenticates to the chassis (one bearer token per remote, a separate listener) and how admission enforces the trust rule ({{L:CH-4}}, {{L:CH-6}}, a ValidatingAdmissionPolicy) is proposed in [ADR-005](../adr/005-remote-lane-auth-and-trust-admission.md), from PoC-5.

## Definition of done for every issue

The platform must be swappable and testable from day 0. So every issue, on top of its own acceptance criteria, is done only when:

- [ ] Every external dependency it touches sits behind a port, with a fake and a real adapter.
- [ ] The fake and the real adapter pass the same port contract suite.
- [ ] The adapter is picked by `spec.adapters` in config, not in code.
- [ ] No product SDK is imported outside its adapter package (import-lint passes).
- [ ] `make test` passes offline with the `fake` profile: no network, no keys. Real dependencies are tested with testcontainers.
- [ ] The workload holds no credential. Only the chassis reaches internal services (ADR-001 hard requirement 1).
- [ ] For chassis issues: the contract suite passes over A2A on localhost and in memory (ADR-001 hard requirement 2).

The ports, fakes, and test layers are described in the PoC plan: [../poc/000-plan.md](../poc/000-plan.md#swappable-and-testable-from-day-0).

## Priorities

| Priority | Meaning | Count |
| -------- | ------- | ----- |
| P0 | Critical path to the first measured savings: router and baseline, harness MVP, simplifier SLM, simplifier agent live with the recorder | {{P0}} |
| P1 | Needed to meet the epic acceptance criteria | {{P1}} |
| P2 | Optional; can slip without breaking an acceptance criterion | {{P2}} |
| P3 | Later phases (10 and 11): plan only | {{P3}} |

Sizes: `S` is up to 1 day, `M` is 2–3 days, `L` is 4–5 days. Nothing is bigger than a week; split it if it grows.

## Waves

Waves are GitHub milestones. Each one ends in something that can be shown.

| Wave | Issues | Ends with |
| ---- | ------ | --------- |
| W0 Decisions | {{R:W0}} | Broker, first cloud, public A2A endpoint, banned words, and metric targets decided |
| W1 Router and baseline | {{R:W1}} | Every model call goes through the router; token spend is counted and on a dashboard; the baseline clock is running |
| W2 Chassis MVP | {{R:W2}} | An echo workload runs behind the chassis: in the `sidecar` lane from the shared library chart, locally and in Kubernetes, and over A2A in memory in the chassis's tests. It answers through native, OpenAI, and Anthropic APIs (streaming and complete), is started by events and emits result events, is traced, holds no key, and passes the contract tests over both transports |
| W3 Simplifier SLM | {{R:W3}} | Break-even is known; the SLM meets its targets on the held-out set, is served on vLLM, and ships only through the eval gate; the first cloud is up |
| W4 Simplifier agent live | {{R:W4}} | **First measured savings** (Phase 3 done-when): the agent is called through any API, recorded by the recorder, falls back when needed, and shows on the dashboards |
| W5 Chassis completion | {{R:W5}} | Phase 0 done-when fully met (MCP tools from OpenAPI, AsyncAPI, reliable events); service operations, the `remote` lane and trust rule, ring rollouts, framework event mappings, and one non-Python workload; tool port and agent factory ready for the registry and orchestrator |
| W6 Golden set and evaluator SLM | {{R:W6}} | Phase 4 done-when; the evaluator SLM replaces the big-model judge; the audit log runs |
| W7 Registry and governance gate | {{R:W7}} | Phase 5 done-when; no agent goes live without a governance block |
| W8 Platform MCP server | {{R:W8}} | Phase 6 done-when: an MCP client searches the registry, runs an agent, and exports a golden set version |
| W9 Orchestrator | {{R:W9}} | Phase 7 done-when |
| W10 Production readiness | {{R:W10}} | Phase 8 done-when: both clouds from Terraform, restore test passes, SLO alerts fire |
| W11 Compliance evidence | {{R:W11}} | Phase 9 done-when: documentation pack and control mapping for any agent version |
| W12 Later | {{R:W12}} | Plans for Phases 10 and 11 |

With one engineer, the waves run one after another in index order. With more people, W1 runs next to W2, and the W3 data work (S-1 to S-4) starts during W2, as the epic says.

## Why the order differs from the phase order

1. **Start the baseline clock first.** The baseline (G-3) needs at least two weeks of data, so the router and token counting come first ({{R:W1}}).
2. **Break-even before GPU spend.** The cost model ({{L:G-5}}) sits right before training, so the top risk is checked before money goes into GPUs.
3. **Chassis MVP before chassis completion.** Only what the simplifier needs is in W2: the `sidecar` lane with A2A, the model proxy, the library chart, and chassis-only credentials. The tool port, service operations, the `remote` lane, ring rollouts, OpenAPI and MCP generation, AsyncAPI, the factory CLI, and the public A2A endpoint move to W5, still before the registry and orchestrator need them.
4. **Pull forward what later stories depend on.** The recorder (D-0) comes before result events (A-3), because the Phase 3 done-when needs it. The broker (X-8) comes with the event port (H-17). First-cloud Terraform (X-1a) and GPU setup (X-2) come before the agent ships.
5. **Compliance by design, pulled forward where it is cheap or dated.** AI-generated marking (C-5) goes into the harness wave, because AI Act Art. 50(2) marking applies to systems already on the market from 2 December 2026. Compute logging (C-8) goes with the training pipeline. The audit log (C-3) goes with the data agents. The governance gate (C-1, C-2) goes right after the registry API. Oversight controls (C-6) go with the orchestrator's approval steps.
6. **Evaluator SLM before the registry**, as in the epic. Until E-3, every simplifier call is judged by a big model, which costs money.
7. **Test set before search.** The search test set (R-13) comes before hybrid search (R-11).
8. **Security before exposure.** MCP scopes and audit (P-4) come right after the MCP server base (P-1).

## Critical path

- **To first savings (A-3):** {{MVP_PATH}}. About {{MVP_DAYS}} working days, plus the two weeks of baseline data that G-5 needs before S-6. The chassis MVP ({{R:W2}}) must finish before A-1.
- **Longest chain overall:** {{ALL_PATH}}. The registry search and the orchestrator engine are the long pole.

## Open decisions and what they block

Tracked in {{L:DEC-1}}.

| Decision | Blocks |
| -------- | ------ |
| Event broker: NATS JetStream or the company's Kafka | {{L:H-17}}, {{L:X-8}}, {{L:H-23}} |
| First cloud: AWS or GCP | {{L:X-1a}}, then {{L:X-2}} and {{L:X-1b}} |
| Whether the public A2A endpoint ships in version 1. A2A to workloads ships per ADR-001 | {{L:H-5}} |
| Banned-word list and final success-metric targets | {{L:S-1}}, {{L:S-3}}, {{L:G-3}} |

## Changes from the epic

The epic itself is unchanged. The backlog differs from it in these ways:

- **New issues:** DEC-1 (the epic's open decisions as one issue), L-1 (Phase 10), L-2 and L-3 (Phase 11), and CH-1 to CH-8 (from ADR-001, listed above).
- **Re-scoped by ADR-001:** H-11 was a separately published adapter package. It is now reuse through the stock chassis image in front of any A2A workload.
- **Deployment shape (ADR-001):** G.4 says one container per agent locally. Each agent now runs as two containers: the chassis and the workload. F.1's `handle` is always served over A2A: on localhost in production, and in memory in the chassis's tests.
- **Split:** X-1 into X-1a (first cloud) and X-1b (second cloud, same inputs and outputs). P-2 into P-2a (golden set and metrics tools, needed for the Phase 6 done-when) and P-2b (run, memory, config, and workflow tools, after the orchestrator; also covers the Config and Workflows tools in F.4).
- **Scope made clear where stories overlap:** H-4 does not include idempotency (H-18 owns it). H-12 adds the governance fields to the config schema; C-1 adds enforcement. The registration step in the template CI (H-10) is a stub until R-2. H-17 ships the first broker adapter; H-23 adds the rest.
- **Moved across phases:** see "Why the order differs" above. Each moved issue says why in its own Why section.
- **More ports and a config addition:** the harness has one port per external dependency (13 ports, up from the five in F.1), each with a fake. `spec.adapters` in the agent config picks the adapter per port. Both come from the "swappable and testable from day 0" rule.
- **Tech beyond Appendix H:** a broker client in the chassis for events (ADR-004), FastMCP and a2a-sdk for interfaces, OpenInference for tracing, Presidio and Llama Prompt Guard 2 as LiteLLM guardrails, Langfuse as the golden set workspace, and KServe for canary serving. lakeFS changed license, so DVC is an option. From ADR-001: Kubernetes native sidecars (1.33 or later), a gVisor RuntimeClass, NetworkPolicy, Kyverno or ValidatingAdmissionPolicy, and one LiteLLM virtual key per service. Details in the [reuse analysis](../poc/010-reuse-analysis.md).

## Gaps found while writing the issues

These came up while writing the issues. Each one needs an owner decision. None of them change the order.

- **mTLS between services** (G.3) has no story. Suggested home: {{L:X-1a}}, where cert-manager is installed. The service identity it uses is held only by the chassis ({{L:CH-4}}).
- **No cluster before the first cloud.** W2 issues that deploy to Kubernetes ({{L:X-8}}, {{L:CH-3}}, {{L:H-10}}, {{L:CH-4}}) use a local test cluster (kind or k3d) until {{L:X-1a}}. It needs native sidecars, a CNI that enforces NetworkPolicy, and, for the `remote` lane, a gVisor RuntimeClass.
- **Where the router runs before the cloud exists.** The baseline must count real traffic, so {{L:G-1}} needs a home that today's services can reach. Now part of {{L:DEC-1}}.
- **History swap vs statelessness.** {{L:G-4}} reads stored simplified answers, but B.4 says an agent never reads records back. Decide whether the history swap is an allowed exception.
- **What A-4 switches on.** The simplifier has no multi-turn history, so {{L:A-4}} is written as a pilot agent that sends chat history to a big model. The title may need rewording.
- **Missing owners, placed where they fit:** the Temporal server setup is in {{L:O-2}}. The `merger` agent from the C.2 workflow is in {{L:O-4}}.
- **The AI Act Art. 9 risk register** (E.5) has no story. It is flagged in {{L:C-9}}.
- **Epic inconsistency:** G.1 says `context_ref` stays empty "until Phase 9", but shared memory is Phase 10. The issues use Phase 10.
- **{{L:H-10}} was bigger than size L.** The library chart moved to {{L:CH-3}}, and the credentials and egress rules to {{L:CH-4}}.
- **New event types** that are not in the D.3 catalog: `mcp.tool.called.v1` ({{L:P-4}}) and `agents.feedback.received.v1` (PoC-7 in the PoC track).
- **No epic story for the engine connectors or the sandbox.** ADR-001 makes them part of the chassis, so they are now {{L:CH-1}} and {{L:CH-6}}. The PoC track proves them first (PoC-2 and PoC-5).
- **Values marked "suggested"** inside the issues, such as pass bars, sample sizes, tools, and trust policies, need sign-off in planning.

### ADR-001 follow-ups

ADR-001 does not settle these. Each has a suggested default and a home issue, and needs owner sign-off.

- **(a) Services that write their own store:** {{L:D-0}}, {{L:D-1}}, {{L:C-3}}, {{L:R-1}}, {{L:O-9}}. Only the chassis may hold a store credential. Suggested: the chassis holds it and exposes the service's own store to its workload as MCP tools through the tool proxy, the way ADR-001 item 8 adds connectors.
- **(b) The orchestrator's Temporal worker:** {{L:O-1}}, {{L:O-2}}, {{L:O-3}}, {{L:O-16}}. The framework Temporal integrations run in the workload and need a Temporal credential. Suggested: the worker runs in the chassis and calls the workload once per step through the connector.
- **(c) Dapr:** {{L:H-17}}, {{L:H-20}}, {{L:H-22}}, {{L:H-23}}, {{L:C-3}}. Dapr is a third container in the pod, which the ADR's cost table leaves out, and the workload can reach its localhost API. Suggested: a broker client in the chassis. The other choice is Dapr with API-token auth and the token in the chassis container only. The PoC track's PoC-4 tries both, and {{L:DEC-1}} records the result. PoC-4's result: [ADR-004](../adr/004-events-through-a-broker-client.md) picks the broker client; the owner accepted it on 2026-10-08.
- **(d) Guardrails per key:** ADR-001 item 6 gives each scoped key its own guardrails, but LiteLLM makes that Enterprise-only ({{L:H-6}}). Buy Enterprise, or set guardrails per route.
- **(e) The framework owns the model loop in the `sidecar` lane:** {{L:H-4}}, {{L:H-16}}, {{L:G-4}}, {{L:M-3}}. The chassis no longer drives the model calls. Suggested: a retry calls `handle` again. A fallback retries with `ctx.model.route` set to the fallback route, which the model proxy enforces for that request. The model proxy enforces the budget. History swap and shadow calls are decided in their own issues.
- **(f) Workload logs:** the workload's logs do not pass through the chassis, so the chassis cannot redact them. Suggested: redaction in the OpenTelemetry Collector log pipeline ({{L:H-7}}).
- **(g) Shutdown order with two containers:** Kubernetes stops native sidecars after the main container. Suggested: the workload is the native sidecar and the chassis the main container, so the chassis gets SIGTERM first and drains; the workload finishes its in-flight `handle` calls before it exits, and a rolling-restart test proves no request fails. PoC-4 tries this against a preStop delay ({{L:H-2}}, {{L:CH-3}}).
- **(h) Outbound calls keyed to the inbound request:** the model and tool proxies can apply a request's budget and route only if the workload's HTTP client propagates `traceparent`. Suggested: OpenTelemetry httpx instrumentation, switched on by the service template; a call without a trace id counts against a per-replica budget ({{L:CH-2}}, {{L:H-4}}).
- **(i) The `remote` lane's path to models and tools:** the proxies also listen on the pod IP for that lane, with a per-remote credential. A managed runtime that cannot be pointed at them holds its own scoped key, the one relaxation of hard requirement 1 ({{L:CH-2}}, {{L:CH-6}}).
- **Two meanings of "trust":** the registry's trust levels ({{L:R-7}}) are not `spec.trust`. `external` and `manual` entries are third-party, so they are `untrusted` and go to the `remote` lane. `internal-signed` is evidence for the ADR's source test, but not for its behavior test.

## Estimate check

The sizes add up to about {{TOTAL_DAYS}} engineer-days ({{TOTAL_WEEKS}} weeks) for W0–W11, after the reuse picks. Before them it was about 305 days (61 weeks). The epic says 29–33 weeks for one engineer. Either the epic assumes a lot of reuse, or the phase estimates need a second look with the team. The sizes here are first guesses; re-size in planning.

## Backlog

{{TABLE}}

## Coverage by epic phase

{{COVERAGE}}

All 113 epic stories are covered exactly once (X-1 and P-2 as two issues each).

## Importing into GitHub

1. Create the labels (`story`, `decision`, `later`, `priority:*`, `phase:*`, `area:*`, `size:*`) and the milestones (W0–W12).
2. Create issues in index order with `gh issue create --title ... --label ... --milestone ... --body-file ...`, using the body below the frontmatter.
3. Replace each `NNN ID` reference with the real issue number, and link each issue to the epic.
