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
| `chassis` | The standard for every service, whatever runs inside | 28 |
| `agent-profile` | Modules only agent workloads need (model and tool proxies, LLM guardrails, evaluator gate, AI marking, A2A) | 9 |
| `platform` | Services and infrastructure built on the chassis (router, SLMs, data agents, registry, orchestrator, production, governance) | 94 |

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
2. Every lane is tested on every commit: each contract case runs over A2A on localhost and over A2A in memory, and CI runs a fake workload through the `remote` lane. Until [055 CH-6](055-CH-6-remote-lane-trust-rule.md) ships, the `remote` lane is not supported.

New chassis issues from the ADR:

- [009 CH-1](009-CH-1-engine-connectors-a2a.md): the `inprocess` and `sidecar` connectors, and the template A2A server that wraps `handle`. Where that server lives is decided in [ADR-002](../adr/002-template-a2a-server-placement.md).
- [013 CH-2](013-CH-2-outbound-model-proxy.md): the outbound model proxy. The tool proxy is part of [054 H-16](054-H-16-tool-port.md).
- [024 CH-3](024-CH-3-helm-library-chart.md): the shared Helm library chart that adds the chassis container.
- [026 CH-4](026-CH-4-chassis-only-credentials-egress.md): chassis-only credentials and default-deny egress (hard requirement 1).
- [050 CH-5](050-CH-5-service-operations.md): service operations, declared in config and routed by the chassis.
- [055 CH-6](055-CH-6-remote-lane-trust-rule.md): the `remote` lane and the trust rule.
- [056 CH-7](056-CH-7-chassis-release-rings.md): chassis release in rings, with a minimum-version rule.
- [058 CH-8](058-CH-8-framework-event-mappings.md): framework event mappings and one non-Python workload.

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
| P0 | Critical path to the first measured savings: router and baseline, harness MVP, simplifier SLM, simplifier agent live with the recorder | 40 |
| P1 | Needed to meet the epic acceptance criteria | 77 |
| P2 | Optional; can slip without breaking an acceptance criterion | 7 |
| P3 | Later phases (10 and 11): plan only | 3 |

Sizes: `S` is up to 1 day, `M` is 2–3 days, `L` is 4–5 days. Nothing is bigger than a week; split it if it grows.

## Waves

Waves are GitHub milestones. Each one ends in something that can be shown.

| Wave | Issues | Ends with |
| ---- | ------ | --------- |
| W0 Decisions | 001 | Broker, first cloud, public A2A endpoint, banned words, and metric targets decided |
| W1 Router and baseline | 002–006 | Every model call goes through the router; token spend is counted and on a dashboard; the baseline clock is running |
| W2 Chassis MVP | 007–026 | An echo workload runs behind the chassis: in the `sidecar` lane from the shared library chart, locally and in Kubernetes, and over A2A in memory in the chassis's tests. It answers through native, OpenAI, and Anthropic APIs (streaming and complete), is started by events and emits result events, is traced, holds no key, and passes the contract tests over both transports |
| W3 Simplifier SLM | 027–039 | Break-even is known; the SLM meets its targets on the held-out set, is served on vLLM, and ships only through the eval gate; the first cloud is up |
| W4 Simplifier agent live | 040–049 | **First measured savings** (Phase 3 done-when): the agent is called through any API, recorded by the recorder, falls back when needed, and shows on the dashboards |
| W5 Chassis completion | 050–063 | Phase 0 done-when fully met (MCP tools from OpenAPI, AsyncAPI, reliable events); service operations, the `remote` lane and trust rule, ring rollouts, framework event mappings, and one non-Python workload; tool port and agent factory ready for the registry and orchestrator |
| W6 Golden set and evaluator SLM | 064–075 | Phase 4 done-when; the evaluator SLM replaces the big-model judge; the audit log runs |
| W7 Registry and governance gate | 076–093 | Phase 5 done-when; no agent goes live without a governance block |
| W8 Platform MCP server | 094–097 | Phase 6 done-when: an MCP client searches the registry, runs an agent, and exports a golden set version |
| W9 Orchestrator | 098–115 | Phase 7 done-when |
| W10 Production readiness | 116–122 | Phase 8 done-when: both clouds from Terraform, restore test passes, SLO alerts fire |
| W11 Compliance evidence | 123–124 | Phase 9 done-when: documentation pack and control mapping for any agent version |
| W12 Later | 125–127 | Plans for Phases 10 and 11 |

With one engineer, the waves run one after another in index order. With more people, W1 runs next to W2, and the W3 data work (S-1 to S-4) starts during W2, as the epic says.

## Why the order differs from the phase order

1. **Start the baseline clock first.** The baseline (G-3) needs at least two weeks of data, so the router and token counting come first (002–006).
2. **Break-even before GPU spend.** The cost model ([031 G-5](031-G-5-cost-model-break-even.md)) sits right before training, so the top risk is checked before money goes into GPUs.
3. **Chassis MVP before chassis completion.** Only what the simplifier needs is in W2: the `sidecar` lane with A2A, the model proxy, the library chart, and chassis-only credentials. The tool port, service operations, the `remote` lane, ring rollouts, OpenAPI and MCP generation, AsyncAPI, the factory CLI, and the public A2A endpoint move to W5, still before the registry and orchestrator need them.
4. **Pull forward what later stories depend on.** The recorder (D-0) comes before result events (A-3), because the Phase 3 done-when needs it. The broker (X-8) comes with the event port (H-17). First-cloud Terraform (X-1a) and GPU setup (X-2) come before the agent ships.
5. **Compliance by design, pulled forward where it is cheap or dated.** AI-generated marking (C-5) goes into the harness wave, because AI Act Art. 50(2) marking applies to systems already on the market from 2 December 2026. Compute logging (C-8) goes with the training pipeline. The audit log (C-3) goes with the data agents. The governance gate (C-1, C-2) goes right after the registry API. Oversight controls (C-6) go with the orchestrator's approval steps.
6. **Evaluator SLM before the registry**, as in the epic. Until E-3, every simplifier call is judged by a big model, which costs money.
7. **Test set before search.** The search test set (R-13) comes before hybrid search (R-11).
8. **Security before exposure.** MCP scopes and audit (P-4) come right after the MCP server base (P-1).

## Critical path

- **To first savings (A-3):** 007 H-1 → 008 H-14 → 009 CH-1 → 011 H-2 → 015 H-8 → 025 H-10 → 026 CH-4 → 041 A-1 → 043 A-3. About 31 working days, plus the two weeks of baseline data that G-5 needs before S-6. The chassis MVP (007–026) must finish before A-1.
- **Longest chain overall:** 007 H-1 → 008 H-14 → 009 CH-1 → 011 H-2 → 016 H-9 → 018 H-18 → 022 H-6 → 076 R-1 → 090 R-13 → 091 R-11 → 092 R-12 → 099 O-1 → 100 O-2 → 103 O-11 → 108 O-12 → 112 O-16. The registry search and the orchestrator engine are the long pole.

## Open decisions and what they block

Tracked in [001 DEC-1](001-DEC-1-resolve-open-decisions.md).

| Decision | Blocks |
| -------- | ------ |
| Event broker: NATS JetStream or the company's Kafka | [019 H-17](019-H-17-event-port.md), [020 X-8](020-X-8-event-broker.md), [060 H-23](060-H-23-broker-adapters.md) |
| First cloud: AWS or GCP | [038 X-1a](038-X-1a-terraform-first-cloud.md), then [039 X-2](039-X-2-gpu-setup.md) and [116 X-1b](116-X-1b-terraform-second-cloud.md) |
| Whether the public A2A endpoint ships in version 1. A2A to workloads ships per ADR-001 | [061 H-5](061-H-5-a2a-adapter.md) |
| Banned-word list and final success-metric targets | [027 S-1](027-S-1-rewrite-rules.md), [028 S-3](028-S-3-simplifier-metrics.md), [005 G-3](005-G-3-baseline-report.md) |

## Changes from the epic

The epic itself is unchanged. The backlog differs from it in these ways:

- **New issues:** DEC-1 (the epic's open decisions as one issue), L-1 (Phase 10), L-2 and L-3 (Phase 11), and CH-1 to CH-8 (from ADR-001, listed above).
- **Re-scoped by ADR-001:** H-11 was a separately published adapter package. It is now reuse through the stock chassis image in front of any A2A workload.
- **Deployment shape (ADR-001):** G.4 says one container per agent locally. Each agent now runs as two containers: the chassis and the workload. F.1's `handle` is always served over A2A: on localhost in production, and in memory in the chassis's tests.
- **Split:** X-1 into X-1a (first cloud) and X-1b (second cloud, same inputs and outputs). P-2 into P-2a (golden set and metrics tools, needed for the Phase 6 done-when) and P-2b (run, memory, config, and workflow tools, after the orchestrator; also covers the Config and Workflows tools in F.4).
- **Scope made clear where stories overlap:** H-4 does not include idempotency (H-18 owns it). H-12 adds the governance fields to the config schema; C-1 adds enforcement. The registration step in the template CI (H-10) is a stub until R-2. H-17 ships the first broker adapter; H-23 adds the rest.
- **Moved across phases:** see "Why the order differs" above. Each moved issue says why in its own Why section.
- **More ports and a config addition:** the harness has one port per external dependency (13 ports, up from the five in F.1), each with a fake. `spec.adapters` in the agent config picks the adapter per port. Both come from the "swappable and testable from day 0" rule.
- **Tech beyond Appendix H:** Dapr for events, FastMCP and a2a-sdk for interfaces, OpenInference for tracing, Presidio and Llama Prompt Guard 2 as LiteLLM guardrails, Langfuse as the golden set workspace, and KServe for canary serving. lakeFS changed license, so DVC is an option. From ADR-001: Kubernetes native sidecars (1.33 or later), a gVisor RuntimeClass, NetworkPolicy, Kyverno or ValidatingAdmissionPolicy, and one LiteLLM virtual key per service. Details in the [reuse analysis](../poc/010-reuse-analysis.md).

## Gaps found while writing the issues

These came up while writing the issues. Each one needs an owner decision. None of them change the order.

- **mTLS between services** (G.3) has no story. Suggested home: [038 X-1a](038-X-1a-terraform-first-cloud.md), where cert-manager is installed. The service identity it uses is held only by the chassis ([026 CH-4](026-CH-4-chassis-only-credentials-egress.md)).
- **No cluster before the first cloud.** W2 issues that deploy to Kubernetes ([020 X-8](020-X-8-event-broker.md), [024 CH-3](024-CH-3-helm-library-chart.md), [025 H-10](025-H-10-template-repo.md), [026 CH-4](026-CH-4-chassis-only-credentials-egress.md)) use a local test cluster (kind or k3d) until [038 X-1a](038-X-1a-terraform-first-cloud.md). It needs native sidecars, a CNI that enforces NetworkPolicy, and, for the `remote` lane, a gVisor RuntimeClass.
- **Where the router runs before the cloud exists.** The baseline must count real traffic, so [002 G-1](002-G-1-litellm-router.md) needs a home that today's services can reach. Now part of [001 DEC-1](001-DEC-1-resolve-open-decisions.md).
- **History swap vs statelessness.** [044 G-4](044-G-4-history-swap.md) reads stored simplified answers, but B.4 says an agent never reads records back. Decide whether the history swap is an allowed exception.
- **What A-4 switches on.** The simplifier has no multi-turn history, so [045 A-4](045-A-4-history-swap-on.md) is written as a pilot agent that sends chat history to a big model. The title may need rewording.
- **Missing owners, placed where they fit:** the Temporal server setup is in [100 O-2](100-O-2-temporal-checkpoints.md). The `merger` agent from the C.2 workflow is in [105 O-4](105-O-4-fan-out-fan-in.md).
- **The AI Act Art. 9 risk register** (E.5) has no story. It is flagged in [124 C-9](124-C-9-control-mapping-report.md).
- **Epic inconsistency:** G.1 says `context_ref` stays empty "until Phase 9", but shared memory is Phase 10. The issues use Phase 10.
- **[025 H-10](025-H-10-template-repo.md) was bigger than size L.** The library chart moved to [024 CH-3](024-CH-3-helm-library-chart.md), and the credentials and egress rules to [026 CH-4](026-CH-4-chassis-only-credentials-egress.md).
- **New event types** that are not in the D.3 catalog: `mcp.tool.called.v1` ([095 P-4](095-P-4-mcp-scopes-audit.md)) and `agents.feedback.received.v1` (PoC-7 in the PoC track).
- **No epic story for the engine connectors or the sandbox.** ADR-001 makes them part of the chassis, so they are now [009 CH-1](009-CH-1-engine-connectors-a2a.md) and [055 CH-6](055-CH-6-remote-lane-trust-rule.md). The PoC track proves them first (PoC-2 and PoC-5).
- **Values marked "suggested"** inside the issues, such as pass bars, sample sizes, tools, and trust policies, need sign-off in planning.

### ADR-001 follow-ups

ADR-001 does not settle these. Each has a suggested default and a home issue, and needs owner sign-off.

- **(a) Services that write their own store:** [040 D-0](040-D-0-recorder-agent.md), [064 D-1](064-D-1-golden-set-agent.md), [072 C-3](072-C-3-audit-log-agent.md), [076 R-1](076-R-1-registry-data-model-api.md), [113 O-9](113-O-9-orchestrator-memory.md). Only the chassis may hold a store credential. Suggested: the chassis holds it and exposes the service's own store to its workload as MCP tools through the tool proxy, the way ADR-001 item 8 adds connectors.
- **(b) The orchestrator's Temporal worker:** [099 O-1](099-O-1-orchestrator-agent.md), [100 O-2](100-O-2-temporal-checkpoints.md), [104 O-3](104-O-3-agent-pools-keda.md), [112 O-16](112-O-16-event-triggers.md). The framework Temporal integrations run in the workload and need a Temporal credential. Suggested: the worker runs in the chassis and calls the workload once per step through the connector.
- **(c) Dapr:** [019 H-17](019-H-17-event-port.md), [021 H-20](021-H-20-event-consumer-adapter.md), [053 H-22](053-H-22-event-reliability.md), [060 H-23](060-H-23-broker-adapters.md), [072 C-3](072-C-3-audit-log-agent.md). Dapr is a third container in the pod, which the ADR's cost table leaves out, and the workload can reach its localhost API. Suggested: a broker client in the chassis. The other choice is Dapr with API-token auth and the token in the chassis container only. The PoC track's PoC-4 tries both, and [001 DEC-1](001-DEC-1-resolve-open-decisions.md) records the result.
- **(d) Guardrails per key:** ADR-001 item 6 gives each scoped key its own guardrails, but LiteLLM makes that Enterprise-only ([022 H-6](022-H-6-security-middleware.md)). Buy Enterprise, or set guardrails per route.
- **(e) The framework owns the model loop in the `sidecar` lane:** [017 H-4](017-H-4-harness-features.md), [054 H-16](054-H-16-tool-port.md), [044 G-4](044-G-4-history-swap.md), [047 M-3](047-M-3-shadow-mode.md). The chassis no longer drives the model calls. Suggested: a retry calls `handle` again. A fallback retries with `ctx.model.route` set to the fallback route, which the model proxy enforces for that request. The model proxy enforces the budget. History swap and shadow calls are decided in their own issues.
- **(f) Workload logs:** the workload's logs do not pass through the chassis, so the chassis cannot redact them. Suggested: redaction in the OpenTelemetry Collector log pipeline ([014 H-7](014-H-7-observability.md)).
- **(g) Shutdown order with two containers:** Kubernetes stops native sidecars after the main container. Suggested: the workload is the native sidecar and the chassis the main container, so the chassis gets SIGTERM first and drains; the workload finishes its in-flight `handle` calls before it exits, and a rolling-restart test proves no request fails. PoC-4 tries this against a preStop delay ([011 H-2](011-H-2-inbound-adapters.md), [024 CH-3](024-CH-3-helm-library-chart.md)).
- **(h) Outbound calls keyed to the inbound request:** the model and tool proxies can apply a request's budget and route only if the workload's HTTP client propagates `traceparent`. Suggested: OpenTelemetry httpx instrumentation, switched on by the service template; a call without a trace id counts against a per-replica budget ([013 CH-2](013-CH-2-outbound-model-proxy.md), [017 H-4](017-H-4-harness-features.md)).
- **(i) The `remote` lane's path to models and tools:** the proxies also listen on the pod IP for that lane, with a per-remote credential. A managed runtime that cannot be pointed at them holds its own scoped key, the one relaxation of hard requirement 1 ([013 CH-2](013-CH-2-outbound-model-proxy.md), [055 CH-6](055-CH-6-remote-lane-trust-rule.md)).
- **Two meanings of "trust":** the registry's trust levels ([080 R-7](080-R-7-trust-levels.md)) are not `spec.trust`. `external` and `manual` entries are third-party, so they are `untrusted` and go to the `remote` lane. `internal-signed` is evidence for the ADR's source test, but not for its behavior test.

## Estimate check

The sizes add up to about 283.5 engineer-days (57 weeks) for W0–W11, after the reuse picks. Before them it was about 305 days (61 weeks). The epic says 29–33 weeks for one engineer. Either the epic assumes a lot of reuse, or the phase estimates need a second look with the team. The sizes here are first guesses; re-size in planning.

## Backlog

### W0 Decisions

| # | ID | Issue | Layer | Priority | Size | Depends on |
| - | -- | ----- | ----- | -------- | ---- | ---------- |
| 001 | DEC-1 | [Resolve the open decisions that block the backlog](001-DEC-1-resolve-open-decisions.md) | platform | P0 | S | — |

### W1 Router and baseline

| # | ID | Issue | Layer | Priority | Size | Depends on |
| - | -- | ----- | ----- | -------- | ---- | ---------- |
| 002 | G-1 | [LiteLLM router in front of all model calls](002-G-1-litellm-router.md) | platform | P0 | M | — |
| 003 | G-1b | [Named model routes, swapped in router config only](003-G-1b-named-model-routes.md) | platform | P0 | S | [002](002-G-1-litellm-router.md) |
| 004 | G-2 | [Token and cost counting per request, user, and agent](004-G-2-token-cost-counting.md) | platform | P0 | M | [002](002-G-1-litellm-router.md) |
| 005 | G-3 | [Baseline report: token spend and cost over at least two weeks](005-G-3-baseline-report.md) | platform | P0 | M | [004](004-G-2-token-cost-counting.md) |
| 006 | G-6 | [Savings dashboard with live token spend](006-G-6-savings-dashboard.md) | platform | P0 | M | [004](004-G-2-token-cost-counting.md) |

### W2 Chassis MVP

| # | ID | Issue | Layer | Priority | Size | Depends on |
| - | -- | ----- | ----- | -------- | ---- | ---------- |
| 007 | H-1 | [Chassis package and image: ports, `handle` contract, JSON event schema, envelope](007-H-1-harness-library-ports-envelope.md) | chassis | P0 | L | — |
| 008 | H-14 | [One agent interface for all classes (stateless, orchestrator, data)](008-H-14-one-agent-interface.md) | chassis | P0 | M | [007](007-H-1-harness-library-ports-envelope.md) |
| 009 | CH-1 | [Engine connectors `inprocess` and `sidecar` over A2A, plus the template A2A server that wraps `handle`](009-CH-1-engine-connectors-a2a.md) | chassis | P0 | M | [007](007-H-1-harness-library-ports-envelope.md), [008](008-H-14-one-agent-interface.md) |
| 010 | H-12 | [Config loader: object storage, JSON Schema check, reload on change](010-H-12-config-loader.md) | chassis | P0 | M | [007](007-H-1-harness-library-ports-envelope.md) |
| 011 | H-2 | [Inbound adapters: native, OpenAI-compatible, Anthropic-compatible, with streaming](011-H-2-inbound-adapters.md) | chassis, agent-profile | P0 | L | [007](007-H-1-harness-library-ports-envelope.md), [008](008-H-14-one-agent-interface.md), [009](009-CH-1-engine-connectors-a2a.md) |
| 012 | H-3 | [Model port to the LLM router, plus direct vLLM and llama.cpp adapters](012-H-3-model-port.md) | agent-profile | P0 | M | [003](003-G-1b-named-model-routes.md), [007](007-H-1-harness-library-ports-envelope.md), [004](004-G-2-token-cost-counting.md) |
| 013 | CH-2 | [Outbound model proxy: an OpenAI- and Anthropic-compatible URL for workloads, forwarded with the service's scoped key](013-CH-2-outbound-model-proxy.md) | chassis, agent-profile | P0 | M | [012](012-H-3-model-port.md), [009](009-CH-1-engine-connectors-a2a.md) |
| 014 | H-7 | [Observability: logs, traces, metrics, Langfuse](014-H-7-observability.md) | chassis | P0 | S | [007](007-H-1-harness-library-ports-envelope.md), [012](012-H-3-model-port.md), [009](009-CH-1-engine-connectors-a2a.md) |
| 015 | H-8 | [Testing kit: fake adapters, contract tests per API, record and replay](015-H-8-testing-kit.md) | chassis | P0 | L | [011](011-H-2-inbound-adapters.md), [012](012-H-3-model-port.md), [009](009-CH-1-engine-connectors-a2a.md) |
| 016 | H-9 | [Local debug profile: Docker Compose, hot reload, debugger port](016-H-9-local-debug-profile.md) | chassis | P0 | S | [010](010-H-12-config-loader.md), [011](011-H-2-inbound-adapters.md), [014](014-H-7-observability.md), [009](009-CH-1-engine-connectors-a2a.md) |
| 017 | H-4 | [Harness features: schema check, evaluator gate, retry, fallback, timeout, budget](017-H-4-harness-features.md) | chassis, agent-profile | P0 | L | [010](010-H-12-config-loader.md), [012](012-H-3-model-port.md), [009](009-CH-1-engine-connectors-a2a.md), [013](013-CH-2-outbound-model-proxy.md) |
| 018 | H-18 | [Idempotency: key check, optional Valkey result cache, key passed to write tools](018-H-18-idempotency.md) | chassis | P0 | M | [007](007-H-1-harness-library-ports-envelope.md), [016](016-H-9-local-debug-profile.md), [009](009-CH-1-engine-connectors-a2a.md) |
| 019 | H-17 | [Event port: one-way CloudEvents result events to the broker](019-H-17-event-port.md) | chassis | P0 | M | [001](001-DEC-1-resolve-open-decisions.md), [007](007-H-1-harness-library-ports-envelope.md), [009](009-CH-1-engine-connectors-a2a.md) |
| 020 | X-8 | [Event broker in Docker Compose and Kubernetes, with retention and consumer-lag alerts](020-X-8-event-broker.md) | platform | P0 | M | [001](001-DEC-1-resolve-open-decisions.md), [016](016-H-9-local-debug-profile.md) |
| 021 | H-20 | [Event consumer adapter: agents started by CloudEvents, same core as REST](021-H-20-event-consumer-adapter.md) | chassis | P0 | S | [011](011-H-2-inbound-adapters.md), [019](019-H-17-event-port.md), [020](020-X-8-event-broker.md) |
| 022 | H-6 | [Security middleware: auth, scopes, rate limits, PII redaction](022-H-6-security-middleware.md) | chassis, agent-profile | P0 | M | [011](011-H-2-inbound-adapters.md), [018](018-H-18-idempotency.md) |
| 023 | C-5 | [AI-generated marking on outputs and a disclosure flag for client apps](023-C-5-ai-generated-marking.md) | agent-profile | P1 | S | [010](010-H-12-config-loader.md), [011](011-H-2-inbound-adapters.md), [019](019-H-17-event-port.md) |
| 024 | CH-3 | [Shared Helm library chart: chassis sidecar with a pinned tag, secrets on the chassis only, Service on the chassis port](024-CH-3-helm-library-chart.md) | chassis | P0 | M | [009](009-CH-1-engine-connectors-a2a.md), [016](016-H-9-local-debug-profile.md) |
| 025 | H-10 | [Service template: workload Dockerfile, Helm chart on the chassis library chart, CI (tests, signing, deploy, registration)](025-H-10-template-repo.md) | chassis | P0 | L | [015](015-H-8-testing-kit.md), [016](016-H-9-local-debug-profile.md), [024](024-CH-3-helm-library-chart.md) |
| 026 | CH-4 | [Chassis-only credentials for every internal service, and default-deny egress per workload pod](026-CH-4-chassis-only-credentials-egress.md) | chassis | P0 | M | [002](002-G-1-litellm-router.md), [020](020-X-8-event-broker.md), [022](022-H-6-security-middleware.md), [025](025-H-10-template-repo.md) |

### W3 Simplifier SLM

| # | ID | Issue | Layer | Priority | Size | Depends on |
| - | -- | ----- | ----- | -------- | ---- | ---------- |
| 027 | S-1 | [Rewrite rules and banned-word list](027-S-1-rewrite-rules.md) | platform | P0 | S | [001](001-DEC-1-resolve-open-decisions.md) |
| 028 | S-3 | [Simplifier metrics: tokens, readability, semantic similarity, facts_kept, banned words](028-S-3-simplifier-metrics.md) | platform | P0 | M | [027](027-S-1-rewrite-rules.md) |
| 029 | S-2 | [Training data generation: 2,000 to 5,000 pairs with an open-weight teacher](029-S-2-training-data-generation.md) | platform | P0 | M | [003](003-G-1b-named-model-routes.md), [027](027-S-1-rewrite-rules.md), [028](028-S-3-simplifier-metrics.md) |
| 030 | S-4 | [Hand review of a sample and a held-out test set of at least 300 records](030-S-4-hand-review-test-set.md) | platform | P0 | M | [029](029-S-2-training-data-generation.md) |
| 031 | G-5 | [Cost model: GPU hosting cost vs API savings, with the break-even point](031-G-5-cost-model-break-even.md) | platform | P0 | S | [005](005-G-3-baseline-report.md) |
| 032 | M-1 | [MLflow: experiments, model registry, links to data version and scores](032-M-1-mlflow.md) | platform | P0 | S | — |
| 033 | S-5 | [Reproducible training pipeline (config, data version, seed) with Unsloth or TRL](033-S-5-training-pipeline.md) | platform | P0 | M | [030](030-S-4-hand-review-test-set.md), [032](032-M-1-mlflow.md) |
| 034 | C-8 | [Training compute logged per fine-tune run, checked against the GPAI threshold](034-C-8-training-compute-log.md) | platform | P1 | S | [033](033-S-5-training-pipeline.md) |
| 035 | S-6 | [Train candidates on Qwen3, Gemma 3, and Llama 3.2, and pick the best](035-S-6-train-candidates.md) | platform | P0 | L | [031](031-G-5-cost-model-break-even.md), [033](033-S-5-training-pipeline.md) |
| 036 | S-7 | [Serve the simplifier SLM with vLLM, with guided decoding for JSON](036-S-7-serve-with-vllm.md) | platform | P0 | S | [003](003-G-1b-named-model-routes.md), [035](035-S-6-train-candidates.md) |
| 037 | M-2 | [Eval gate in CI: a new model ships only if it beats the current one](037-M-2-eval-gate-ci.md) | platform | P0 | M | [028](028-S-3-simplifier-metrics.md), [030](030-S-4-hand-review-test-set.md), [032](032-M-1-mlflow.md), [020](020-X-8-event-broker.md) |
| 038 | X-1a | [Terraform module for the first cloud (AWS or GCP)](038-X-1a-terraform-first-cloud.md) | platform | P0 | L | [001](001-DEC-1-resolve-open-decisions.md), [025](025-H-10-template-repo.md), [026](026-CH-4-chassis-only-credentials-egress.md) |
| 039 | X-2 | [GPU setup on Kubernetes (NVIDIA GPU Operator) and model weight storage with fast cold start](039-X-2-gpu-setup.md) | platform | P1 | M | [036](036-S-7-serve-with-vllm.md), [038](038-X-1a-terraform-first-cloud.md) |

### W4 Simplifier agent live

| # | ID | Issue | Layer | Priority | Size | Depends on |
| - | -- | ----- | ----- | -------- | ---- | ---------- |
| 040 | D-0 | [Recorder agent: reads result events, removes PII, drops duplicates, writes records to Postgres](040-D-0-recorder-agent.md) | platform | P0 | M | [008](008-H-14-one-agent-interface.md), [019](019-H-17-event-port.md), [020](020-X-8-event-broker.md), [021](021-H-20-event-consumer-adapter.md), [022](022-H-6-security-middleware.md) |
| 041 | A-1 | [Simplifier business logic as a workload behind the chassis](041-A-1-simplifier-core.md) | platform | P0 | M | [017](017-H-4-harness-features.md), [025](025-H-10-template-repo.md), [036](036-S-7-serve-with-vllm.md), [013](013-CH-2-outbound-model-proxy.md), [026](026-CH-4-chassis-only-credentials-egress.md) |
| 042 | A-2 | [Evaluator gate on facts_kept, one retry, then fallback to a big model](042-A-2-evaluator-gate-fallback.md) | platform | P0 | S | [017](017-H-4-harness-features.md), [028](028-S-3-simplifier-metrics.md), [041](041-A-1-simplifier-core.md) |
| 043 | A-3 | [Result events with source, stored by the recorder](043-A-3-result-events.md) | platform | P0 | S | [019](019-H-17-event-port.md), [040](040-D-0-recorder-agent.md), [041](041-A-1-simplifier-core.md) |
| 044 | G-4 | [History swap in the chassis: long past answers replaced by simplified versions](044-G-4-history-swap.md) | agent-profile | P1 | M | [012](012-H-3-model-port.md), [040](040-D-0-recorder-agent.md), [013](013-CH-2-outbound-model-proxy.md) |
| 045 | A-4 | [History swap switched on for the simplifier](045-A-4-history-swap-on.md) | platform | P1 | S | [043](043-A-3-result-events.md), [044](044-G-4-history-swap.md) |
| 046 | A-5 | [Simplifier agent dashboard: pass rate, fallback rate, tokens saved, latency](046-A-5-agent-dashboard.md) | platform | P1 | S | [014](014-H-7-observability.md), [042](042-A-2-evaluator-gate-fallback.md) |
| 047 | M-3 | [Shadow mode: new model runs next to the current one without serving users](047-M-3-shadow-mode.md) | platform | P1 | M | [037](037-M-2-eval-gate-ci.md), [041](041-A-1-simplifier-core.md), [042](042-A-2-evaluator-gate-fallback.md), [013](013-CH-2-outbound-model-proxy.md) |
| 048 | M-4 | [Canary rollout and one-step rollback](048-M-4-canary-rollback.md) | platform | P1 | S | [047](047-M-3-shadow-mode.md), [046](046-A-5-agent-dashboard.md) |
| 049 | M-5 | [Quantized 4-bit variants for CPU and edge, each with its own eval](049-M-5-quantized-variants.md) | platform | P2 | M | [037](037-M-2-eval-gate-ci.md) |

### W5 Chassis completion

| # | ID | Issue | Layer | Priority | Size | Depends on |
| - | -- | ----- | ----- | -------- | ---- | ---------- |
| 050 | CH-5 | [Service operations: declared in config, routed by the chassis through the pipeline](050-CH-5-service-operations.md) | chassis | P1 | M | [011](011-H-2-inbound-adapters.md), [010](010-H-12-config-loader.md) |
| 051 | H-13 | [OpenAPI 3.1 spec per agent, with MCP tools generated from it](051-H-13-openapi-mcp-tools.md) | chassis | P1 | S | [011](011-H-2-inbound-adapters.md), [050](050-CH-5-service-operations.md) |
| 052 | H-21 | [AsyncAPI 3.0 spec per agent, generated next to the OpenAPI spec](052-H-21-asyncapi-spec.md) | chassis | P1 | S | [019](019-H-17-event-port.md), [051](051-H-13-openapi-mcp-tools.md) |
| 053 | H-22 | [Event reliability: idempotent consumers, retries with backoff, dead-letter topic](053-H-22-event-reliability.md) | chassis | P1 | S | [018](018-H-18-idempotency.md), [021](021-H-20-event-consumer-adapter.md) |
| 054 | H-16 | [Tool port: native tool calls or JSON via guided decoding; read and write modes](054-H-16-tool-port.md) | agent-profile | P1 | M | [012](012-H-3-model-port.md), [018](018-H-18-idempotency.md), [013](013-CH-2-outbound-model-proxy.md) |
| 055 | CH-6 | [Remote lane and the trust rule: sandboxed pod or managed runtime over A2A, cloud auth adapter, admission check](055-CH-6-remote-lane-trust-rule.md) | chassis | P1 | L | [009](009-CH-1-engine-connectors-a2a.md), [026](026-CH-4-chassis-only-credentials-egress.md), [010](010-H-12-config-loader.md), [038](038-X-1a-terraform-first-cloud.md) |
| 056 | CH-7 | [Chassis release in rings, with a per-service pin and a minimum-version admission rule](056-CH-7-chassis-release-rings.md) | chassis | P1 | M | [024](024-CH-3-helm-library-chart.md), [055](055-CH-6-remote-lane-trust-rule.md), [014](014-H-7-observability.md) |
| 057 | H-19 | [Stateless check in CI: fail the build on local disk writes or kept data](057-H-19-stateless-check-ci.md) | chassis | P1 | S | [025](025-H-10-template-repo.md) |
| 058 | CH-8 | [Framework event mappings and one non-Python workload in the service template](058-CH-8-framework-event-mappings.md) | chassis | P1 | M | [015](015-H-8-testing-kit.md), [025](025-H-10-template-repo.md) |
| 059 | H-15 | [Agent factory CLI: scaffold an agent by class and kind, modules by config](059-H-15-agent-factory-cli.md) | chassis | P1 | M | [008](008-H-14-one-agent-interface.md), [025](025-H-10-template-repo.md), [051](051-H-13-openapi-mcp-tools.md), [057](057-H-19-stateless-check-ci.md), [058](058-CH-8-framework-event-mappings.md), [055](055-CH-6-remote-lane-trust-rule.md) |
| 060 | H-23 | [More broker adapters behind the event port: Kafka, AWS, GCP](060-H-23-broker-adapters.md) | chassis | P2 | S | [021](021-H-20-event-consumer-adapter.md) |
| 061 | H-5 | [A2A adapter: JSON-RPC endpoint, task states, signed agent card](061-H-5-a2a-adapter.md) | agent-profile | P2 | S | [001](001-DEC-1-resolve-open-decisions.md), [011](011-H-2-inbound-adapters.md) |
| 062 | H-11 | [Chassis reuse in other projects: the stock image in front of any A2A workload](062-H-11-adapter-package.md) | chassis | P2 | S | [011](011-H-2-inbound-adapters.md), [012](012-H-3-model-port.md), [058](058-CH-8-framework-event-mappings.md) |
| 063 | S-8 | [Optional: fine-tune an SLM on tool-call examples for tool agents](063-S-8-tool-call-finetune.md) | platform | P2 | L | [033](033-S-5-training-pipeline.md), [054](054-H-16-tool-port.md) |

### W6 Golden set and evaluator SLM

| # | ID | Issue | Layer | Priority | Size | Depends on |
| - | -- | ----- | ----- | -------- | ---- | ---------- |
| 064 | D-1 | [Golden set agent: reads records, scores them with a judge, sends uncertain ones to review](064-D-1-golden-set-agent.md) | platform | P1 | M | [017](017-H-4-harness-features.md), [040](040-D-0-recorder-agent.md), [042](042-A-2-evaluator-gate-fallback.md) |
| 065 | D-2 | [Review UI: approve, reject, edit, with access control and an audit log](065-D-2-review-ui.md) | platform | P1 | M | [022](022-H-6-security-middleware.md), [064](064-D-1-golden-set-agent.md) |
| 066 | D-6 | [Tags and splits (train, test, holdout) per record](066-D-6-tags-splits.md) | platform | P1 | S | [064](064-D-1-golden-set-agent.md) |
| 067 | D-5 | [Schema check and deduplication on import](067-D-5-import-schema-dedup.md) | platform | P1 | S | [064](064-D-1-golden-set-agent.md) |
| 068 | D-3 | [Import from file upload, API call, or bucket watch](068-D-3-import.md) | platform | P1 | S | [067](067-D-5-import-schema-dedup.md), [030](030-S-4-hand-review-test-set.md) |
| 069 | D-7 | [Versioned datasets with lakeFS; each export is an immutable version with an ID](069-D-7-lakefs-versions.md) | platform | P1 | M | [064](064-D-1-golden-set-agent.md) |
| 070 | D-4 | [Export in JSONL, CSV, Parquet, and Hugging Face datasets format](070-D-4-export-formats.md) | platform | P1 | S | [066](066-D-6-tags-splits.md), [069](069-D-7-lakefs-versions.md), [068](068-D-3-import.md) |
| 071 | D-8 | [Data rules: PII removal before storage, retention period, deletion on request](071-D-8-data-rules.md) | platform | P1 | M | [040](040-D-0-recorder-agent.md), [069](069-D-7-lakefs-versions.md), [065](065-D-2-review-ui.md), [068](068-D-3-import.md) |
| 072 | C-3 | [Audit log data agent: append-only, object lock, retention per risk class, export API](072-C-3-audit-log-agent.md) | platform | P1 | L | [008](008-H-14-one-agent-interface.md), [019](019-H-17-event-port.md), [020](020-X-8-event-broker.md), [021](021-H-20-event-consumer-adapter.md), [053](053-H-22-event-reliability.md), [022](022-H-6-security-middleware.md) |
| 073 | E-1 | [First evaluator SLM (ModernBERT or DeBERTa) trained on approved records](073-E-1-evaluator-slm.md) | platform | P1 | L | [033](033-S-5-training-pipeline.md), [065](065-D-2-review-ui.md), [070](070-D-4-export-formats.md) |
| 074 | E-2 | [Evaluator agreement test against human labels](074-E-2-evaluator-agreement-test.md) | platform | P1 | S | [073](073-E-1-evaluator-slm.md), [030](030-S-4-hand-review-test-set.md) |
| 075 | E-3 | [Evaluator SLM replaces the big-model judge in the harness](075-E-3-evaluator-replaces-judge.md) | platform | P1 | S | [042](042-A-2-evaluator-gate-fallback.md), [074](074-E-2-evaluator-agreement-test.md), [047](047-M-3-shadow-mode.md), [046](046-A-5-agent-dashboard.md) |

### W7 Registry and governance gate

| # | ID | Issue | Layer | Priority | Size | Depends on |
| - | -- | ----- | ----- | -------- | ---- | ---------- |
| 076 | R-1 | [Registry data model and API](076-R-1-registry-data-model-api.md) | platform | P1 | L | [008](008-H-14-one-agent-interface.md), [051](051-H-13-openapi-mcp-tools.md), [019](019-H-17-event-port.md), [022](022-H-6-security-middleware.md) |
| 077 | C-1 | [Governance block enforced by the registry on activation](077-C-1-governance-enforcement.md) | platform | P1 | S | [010](010-H-12-config-loader.md), [076](076-R-1-registry-data-model-api.md), [055](055-CH-6-remote-lane-trust-rule.md), [056](056-CH-7-chassis-release-rings.md) |
| 078 | R-16 | [Registry entries carry the governance block and serve as the AI system inventory](078-R-16-ai-system-inventory.md) | platform | P1 | S | [077](077-C-1-governance-enforcement.md) |
| 079 | C-2 | [Prohibited-practice checklist at registration](079-C-2-prohibited-practice-checklist.md) | platform | P1 | S | [077](077-C-1-governance-enforcement.md) |
| 080 | R-7 | [Trust levels: internal signed entries go live, external and manual need approval](080-R-7-trust-levels.md) | platform | P1 | M | [076](076-R-1-registry-data-model-api.md) |
| 081 | R-8 | [Description scanning for hidden instructions on registration](081-R-8-description-scanning.md) | platform | P1 | M | [080](080-R-7-trust-levels.md) |
| 082 | R-9 | [Description hash pinning: a changed description goes back to review](082-R-9-description-hash-pinning.md) | platform | P1 | S | [080](080-R-7-trust-levels.md) |
| 083 | R-2 | [Kubernetes controller (kopf): watches labeled deployments and reads /manifest](083-R-2-kubernetes-controller.md) | platform | P1 | M | [025](025-H-10-template-repo.md), [076](076-R-1-registry-data-model-api.md), [080](080-R-7-trust-levels.md), [056](056-CH-7-chassis-release-rings.md) |
| 084 | R-3 | [Docker watcher for local runs](084-R-3-docker-watcher.md) | platform | P1 | S | [016](016-H-9-local-debug-profile.md), [076](076-R-1-registry-data-model-api.md), [080](080-R-7-trust-levels.md) |
| 085 | R-6 | [Manual registration: API, UI, and YAML import](085-R-6-manual-registration.md) | platform | P1 | M | [080](080-R-7-trust-levels.md) |
| 086 | R-10 | [Health checks: dead entries marked inactive, never deleted](086-R-10-health-checks.md) | platform | P1 | S | [076](076-R-1-registry-data-model-api.md), [083](083-R-2-kubernetes-controller.md), [014](014-H-7-observability.md) |
| 087 | R-4 | [MCP import: call tools/list and register each tool](087-R-4-mcp-import.md) | platform | P1 | S | [081](081-R-8-description-scanning.md), [082](082-R-9-description-hash-pinning.md) |
| 088 | R-5 | [OpenAPI import: register each operation of a REST API](088-R-5-openapi-import.md) | platform | P1 | M | [051](051-H-13-openapi-mcp-tools.md), [081](081-R-8-description-scanning.md) |
| 089 | R-15 | [Event schemas (AsyncAPI) registered next to OpenAPI specs](089-R-15-event-schemas-registered.md) | platform | P1 | S | [052](052-H-21-asyncapi-spec.md), [076](076-R-1-registry-data-model-api.md) |
| 090 | R-13 | [Registry search test set of at least 200 queries with expected results](090-R-13-search-test-set.md) | platform | P1 | M | [076](076-R-1-registry-data-model-api.md) |
| 091 | R-11 | [Hybrid search: SQL filters, keyword, vector, reciprocal rank fusion, reranker](091-R-11-hybrid-search.md) | platform | P1 | L | [076](076-R-1-registry-data-model-api.md), [090](090-R-13-search-test-set.md), [003](003-G-1b-named-model-routes.md), [036](036-S-7-serve-with-vllm.md) |
| 092 | R-12 | [Search results return the main pick plus ranked fallbacks with difference metadata](092-R-12-main-pick-fallbacks.md) | platform | P1 | M | [091](091-R-11-hybrid-search.md) |
| 093 | R-14 | [Registry exposed as an MCP server](093-R-14-registry-mcp-server.md) | platform | P1 | S | [051](051-H-13-openapi-mcp-tools.md), [092](092-R-12-main-pick-fallbacks.md) |

### W8 Platform MCP server

| # | ID | Issue | Layer | Priority | Size | Depends on |
| - | -- | ----- | ----- | -------- | ---- | ---------- |
| 094 | P-1 | [Platform MCP server built from the harness, with registry and agent tools](094-P-1-platform-mcp-server.md) | platform | P1 | S | [051](051-H-13-openapi-mcp-tools.md), [093](093-R-14-registry-mcp-server.md) |
| 095 | P-4 | [MCP read and write scopes, confirmation on destructive tools, audit log on every call](095-P-4-mcp-scopes-audit.md) | platform | P1 | M | [072](072-C-3-audit-log-agent.md), [094](094-P-1-platform-mcp-server.md) |
| 096 | P-2a | [MCP golden set and metrics tools](096-P-2a-mcp-golden-set-metrics-tools.md) | platform | P1 | M | [006](006-G-6-savings-dashboard.md), [070](070-D-4-export-formats.md), [095](095-P-4-mcp-scopes-audit.md), [046](046-A-5-agent-dashboard.md) |
| 097 | P-3 | [MCP resources and prompts](097-P-3-mcp-resources-prompts.md) | platform | P2 | S | [094](094-P-1-platform-mcp-server.md) |

### W9 Orchestrator

| # | ID | Issue | Layer | Priority | Size | Depends on |
| - | -- | ----- | ----- | -------- | ---- | ---------- |
| 098 | O-10 | [Orchestrator config and workflow schemas (JSON Schema) in the config store](098-O-10-orchestrator-schemas.md) | platform | P1 | M | [010](010-H-12-config-loader.md) |
| 099 | O-1 | [Orchestrator agent built from the harness, using registry search](099-O-1-orchestrator-agent.md) | platform | P1 | M | [008](008-H-14-one-agent-interface.md), [092](092-R-12-main-pick-fallbacks.md), [098](098-O-10-orchestrator-schemas.md) |
| 100 | O-2 | [Temporal workflows with a checkpoint per step](100-O-2-temporal-checkpoints.md) | platform | P1 | M | [099](099-O-1-orchestrator-agent.md), [050](050-CH-5-service-operations.md) |
| 101 | O-13 | [Version pinning per run](101-O-13-version-pinning.md) | platform | P1 | S | [100](100-O-2-temporal-checkpoints.md) |
| 102 | O-7 | [Idempotent steps, so a resume never repeats a side effect](102-O-7-idempotent-steps.md) | platform | P1 | M | [018](018-H-18-idempotency.md), [100](100-O-2-temporal-checkpoints.md) |
| 103 | O-11 | [Workflow engine that runs chain, fan_out, and compare patterns from workflow files](103-O-11-workflow-engine.md) | platform | P1 | L | [098](098-O-10-orchestrator-schemas.md), [100](100-O-2-temporal-checkpoints.md) |
| 104 | O-3 | [Agent pools on task queues with KEDA autoscaling](104-O-3-agent-pools-keda.md) | platform | P1 | M | [025](025-H-10-template-repo.md), [100](100-O-2-temporal-checkpoints.md) |
| 105 | O-4 | [Fan-out and fan-in with overlap and a consistency pass](105-O-4-fan-out-fan-in.md) | platform | P1 | M | [103](103-O-11-workflow-engine.md), [104](104-O-3-agent-pools-keda.md) |
| 106 | O-5 | [Compare runs with an evaluator and a tie rule](106-O-5-compare-runs.md) | platform | P1 | M | [103](103-O-11-workflow-engine.md), [068](068-D-3-import.md) |
| 107 | O-6 | [Budgets per run: max tokens, cost, steps, and time](107-O-6-run-budgets.md) | platform | P1 | S | [100](100-O-2-temporal-checkpoints.md), [103](103-O-11-workflow-engine.md) |
| 108 | O-12 | [Routing rules and planner modes (fixed, planned, hybrid)](108-O-12-routing-planner-modes.md) | platform | P1 | L | [103](103-O-11-workflow-engine.md) |
| 109 | O-8 | [Cancel and human approval steps](109-O-8-cancel-approval.md) | platform | P1 | S | [100](100-O-2-temporal-checkpoints.md), [107](107-O-6-run-budgets.md) |
| 110 | C-6 | [Human oversight controls: review queue, approval steps, stop control, all audited](110-C-6-human-oversight.md) | platform | P1 | M | [065](065-D-2-review-ui.md), [072](072-C-3-audit-log-agent.md), [109](109-O-8-cancel-approval.md) |
| 111 | O-14 | [Workflow validation and dry run before activation, and rollback](111-O-14-workflow-validation-rollback.md) | platform | P1 | M | [103](103-O-11-workflow-engine.md) |
| 112 | O-16 | [Event triggers: workflows started by events, run and step events at each stage](112-O-16-event-triggers.md) | platform | P1 | M | [021](021-H-20-event-consumer-adapter.md), [100](100-O-2-temporal-checkpoints.md), [108](108-O-12-routing-planner-modes.md), [103](103-O-11-workflow-engine.md) |
| 113 | O-9 | [Short-term memory (execution state) and long-term memory (settings, gotchas, feedback)](113-O-9-orchestrator-memory.md) | platform | P1 | L | [100](100-O-2-temporal-checkpoints.md), [068](068-D-3-import.md) |
| 114 | P-2b | [MCP run, memory, config, and workflow tools](114-P-2b-mcp-run-memory-config-tools.md) | platform | P1 | M | [095](095-P-4-mcp-scopes-audit.md), [109](109-O-8-cancel-approval.md), [111](111-O-14-workflow-validation-rollback.md), [113](113-O-9-orchestrator-memory.md) |
| 115 | O-15 | [Save a reviewed planned run as a new workflow file](115-O-15-save-planned-run.md) | platform | P2 | S | [108](108-O-12-routing-planner-modes.md), [111](111-O-14-workflow-validation-rollback.md), [110](110-C-6-human-oversight.md) |

### W10 Production readiness

| # | ID | Issue | Layer | Priority | Size | Depends on |
| - | -- | ----- | ----- | -------- | ---- | ---------- |
| 116 | X-1b | [Terraform module for the second cloud, with the same inputs and outputs](116-X-1b-terraform-second-cloud.md) | platform | P1 | L | [038](038-X-1a-terraform-first-cloud.md), [055](055-CH-6-remote-lane-trust-rule.md) |
| 117 | X-3 | [SLOs and alerts for every agent](117-X-3-slos-alerts.md) | platform | P1 | M | [014](014-H-7-observability.md), [038](038-X-1a-terraform-first-cloud.md), [056](056-CH-7-chassis-release-rings.md) |
| 118 | C-7 | [Incident flow: incident events, alert routing, runbook for serious incidents](118-C-7-incident-flow.md) | platform | P1 | M | [072](072-C-3-audit-log-agent.md), [117](117-X-3-slos-alerts.md) |
| 119 | X-6 | [Runbooks for common failures](119-X-6-runbooks.md) | platform | P1 | M | [117](117-X-3-slos-alerts.md) |
| 120 | X-7 | [Cost dashboard per agent and per pool](120-X-7-cost-dashboard.md) | platform | P1 | S | [004](004-G-2-token-cost-counting.md), [104](104-O-3-agent-pools-keda.md) |
| 121 | X-4 | [Backups and a tested restore for Postgres, object storage, and golden sets](121-X-4-backups-restore.md) | platform | P1 | M | [038](038-X-1a-terraform-first-cloud.md), [069](069-D-7-lakefs-versions.md) |
| 122 | X-5 | [Load tests for agent pools and the registry](122-X-5-load-tests.md) | platform | P1 | M | [091](091-R-11-hybrid-search.md), [104](104-O-3-agent-pools-keda.md) |

### W11 Compliance evidence

| # | ID | Issue | Layer | Priority | Size | Depends on |
| - | -- | ----- | ----- | -------- | ---- | ---------- |
| 123 | C-4 | [Documentation pack generator: technical docs, instructions for use, model card per agent version](123-C-4-documentation-pack.md) | platform | P1 | M | [032](032-M-1-mlflow.md), [051](051-H-13-openapi-mcp-tools.md), [052](052-H-21-asyncapi-spec.md), [078](078-R-16-ai-system-inventory.md) |
| 124 | C-9 | [Control mapping report: ISO 42001 Annex A and AI Act articles to platform evidence](124-C-9-control-mapping-report.md) | platform | P1 | S | [072](072-C-3-audit-log-agent.md), [123](123-C-4-documentation-pack.md) |

### W12 Later

| # | ID | Issue | Layer | Priority | Size | Depends on |
| - | -- | ----- | ----- | -------- | ---- | ---------- |
| 125 | L-1 | [Shared memory at scale: session context, long-term memory, context_ref](125-L-1-shared-memory-at-scale.md) | platform | P3 | L | [113](113-O-9-orchestrator-memory.md) |
| 126 | L-2 | [System prompt improver](126-L-2-system-prompt-improver.md) | platform | P3 | L | [070](070-D-4-export-formats.md), [075](075-E-3-evaluator-replaces-judge.md) |
| 127 | L-3 | [Router SLM: classifier that replaces part of the orchestrator's routing](127-L-3-router-slm.md) | platform | P3 | L | [040](040-D-0-recorder-agent.md), [108](108-O-12-routing-planner-modes.md) |

## Coverage by epic phase

| Epic phase | Issues |
| ---------- | ------ |
| [Open decisions](../slm-agent-platform-epic-v3.md#open-decisions) | [001 DEC-1](001-DEC-1-resolve-open-decisions.md) |
| [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness) | [007 H-1](007-H-1-harness-library-ports-envelope.md), [008 H-14](008-H-14-one-agent-interface.md), [009 CH-1](009-CH-1-engine-connectors-a2a.md), [010 H-12](010-H-12-config-loader.md), [011 H-2](011-H-2-inbound-adapters.md), [012 H-3](012-H-3-model-port.md), [013 CH-2](013-CH-2-outbound-model-proxy.md), [014 H-7](014-H-7-observability.md), [015 H-8](015-H-8-testing-kit.md), [016 H-9](016-H-9-local-debug-profile.md), [017 H-4](017-H-4-harness-features.md), [018 H-18](018-H-18-idempotency.md), [019 H-17](019-H-17-event-port.md), [021 H-20](021-H-20-event-consumer-adapter.md), [022 H-6](022-H-6-security-middleware.md), [024 CH-3](024-CH-3-helm-library-chart.md), [025 H-10](025-H-10-template-repo.md), [026 CH-4](026-CH-4-chassis-only-credentials-egress.md), [050 CH-5](050-CH-5-service-operations.md), [051 H-13](051-H-13-openapi-mcp-tools.md), [052 H-21](052-H-21-asyncapi-spec.md), [053 H-22](053-H-22-event-reliability.md), [054 H-16](054-H-16-tool-port.md), [055 CH-6](055-CH-6-remote-lane-trust-rule.md), [056 CH-7](056-CH-7-chassis-release-rings.md), [057 H-19](057-H-19-stateless-check-ci.md), [058 CH-8](058-CH-8-framework-event-mappings.md), [059 H-15](059-H-15-agent-factory-cli.md), [060 H-23](060-H-23-broker-adapters.md), [061 H-5](061-H-5-a2a-adapter.md), [062 H-11](062-H-11-adapter-package.md) |
| [Phase 1](../slm-agent-platform-epic-v3.md#phase-1-baseline-and-llm-router) | [002 G-1](002-G-1-litellm-router.md), [003 G-1b](003-G-1b-named-model-routes.md), [004 G-2](004-G-2-token-cost-counting.md), [005 G-3](005-G-3-baseline-report.md), [006 G-6](006-G-6-savings-dashboard.md), [031 G-5](031-G-5-cost-model-break-even.md), [044 G-4](044-G-4-history-swap.md) |
| [Phase 2](../slm-agent-platform-epic-v3.md#phase-2-simplifier-slm-and-model-lifecycle) | [027 S-1](027-S-1-rewrite-rules.md), [028 S-3](028-S-3-simplifier-metrics.md), [029 S-2](029-S-2-training-data-generation.md), [030 S-4](030-S-4-hand-review-test-set.md), [032 M-1](032-M-1-mlflow.md), [033 S-5](033-S-5-training-pipeline.md), [035 S-6](035-S-6-train-candidates.md), [036 S-7](036-S-7-serve-with-vllm.md), [037 M-2](037-M-2-eval-gate-ci.md), [047 M-3](047-M-3-shadow-mode.md), [048 M-4](048-M-4-canary-rollback.md), [049 M-5](049-M-5-quantized-variants.md), [063 S-8](063-S-8-tool-call-finetune.md) |
| [Phase 3](../slm-agent-platform-epic-v3.md#phase-3-simplifier-agent) | [041 A-1](041-A-1-simplifier-core.md), [042 A-2](042-A-2-evaluator-gate-fallback.md), [043 A-3](043-A-3-result-events.md), [045 A-4](045-A-4-history-swap-on.md), [046 A-5](046-A-5-agent-dashboard.md) |
| [Phase 4](../slm-agent-platform-epic-v3.md#phase-4-golden-set-agent-and-evaluator-slm) | [040 D-0](040-D-0-recorder-agent.md), [064 D-1](064-D-1-golden-set-agent.md), [065 D-2](065-D-2-review-ui.md), [066 D-6](066-D-6-tags-splits.md), [067 D-5](067-D-5-import-schema-dedup.md), [068 D-3](068-D-3-import.md), [069 D-7](069-D-7-lakefs-versions.md), [070 D-4](070-D-4-export-formats.md), [071 D-8](071-D-8-data-rules.md), [073 E-1](073-E-1-evaluator-slm.md), [074 E-2](074-E-2-evaluator-agreement-test.md), [075 E-3](075-E-3-evaluator-replaces-judge.md) |
| [Phase 5](../slm-agent-platform-epic-v3.md#phase-5-service-registry) | [076 R-1](076-R-1-registry-data-model-api.md), [078 R-16](078-R-16-ai-system-inventory.md), [080 R-7](080-R-7-trust-levels.md), [081 R-8](081-R-8-description-scanning.md), [082 R-9](082-R-9-description-hash-pinning.md), [083 R-2](083-R-2-kubernetes-controller.md), [084 R-3](084-R-3-docker-watcher.md), [085 R-6](085-R-6-manual-registration.md), [086 R-10](086-R-10-health-checks.md), [087 R-4](087-R-4-mcp-import.md), [088 R-5](088-R-5-openapi-import.md), [089 R-15](089-R-15-event-schemas-registered.md), [090 R-13](090-R-13-search-test-set.md), [091 R-11](091-R-11-hybrid-search.md), [092 R-12](092-R-12-main-pick-fallbacks.md), [093 R-14](093-R-14-registry-mcp-server.md) |
| [Phase 6](../slm-agent-platform-epic-v3.md#phase-6-platform-mcp-server) | [094 P-1](094-P-1-platform-mcp-server.md), [095 P-4](095-P-4-mcp-scopes-audit.md), [096 P-2a](096-P-2a-mcp-golden-set-metrics-tools.md), [097 P-3](097-P-3-mcp-resources-prompts.md), [114 P-2b](114-P-2b-mcp-run-memory-config-tools.md) |
| [Phase 7](../slm-agent-platform-epic-v3.md#phase-7-orchestrator) | [098 O-10](098-O-10-orchestrator-schemas.md), [099 O-1](099-O-1-orchestrator-agent.md), [100 O-2](100-O-2-temporal-checkpoints.md), [101 O-13](101-O-13-version-pinning.md), [102 O-7](102-O-7-idempotent-steps.md), [103 O-11](103-O-11-workflow-engine.md), [104 O-3](104-O-3-agent-pools-keda.md), [105 O-4](105-O-4-fan-out-fan-in.md), [106 O-5](106-O-5-compare-runs.md), [107 O-6](107-O-6-run-budgets.md), [108 O-12](108-O-12-routing-planner-modes.md), [109 O-8](109-O-8-cancel-approval.md), [111 O-14](111-O-14-workflow-validation-rollback.md), [112 O-16](112-O-16-event-triggers.md), [113 O-9](113-O-9-orchestrator-memory.md), [115 O-15](115-O-15-save-planned-run.md) |
| [Phase 8](../slm-agent-platform-epic-v3.md#phase-8-production-readiness) | [020 X-8](020-X-8-event-broker.md), [038 X-1a](038-X-1a-terraform-first-cloud.md), [039 X-2](039-X-2-gpu-setup.md), [116 X-1b](116-X-1b-terraform-second-cloud.md), [117 X-3](117-X-3-slos-alerts.md), [119 X-6](119-X-6-runbooks.md), [120 X-7](120-X-7-cost-dashboard.md), [121 X-4](121-X-4-backups-restore.md), [122 X-5](122-X-5-load-tests.md) |
| [Phase 9](../slm-agent-platform-epic-v3.md#phase-9-governance-and-compliance) | [023 C-5](023-C-5-ai-generated-marking.md), [034 C-8](034-C-8-training-compute-log.md), [072 C-3](072-C-3-audit-log-agent.md), [077 C-1](077-C-1-governance-enforcement.md), [079 C-2](079-C-2-prohibited-practice-checklist.md), [110 C-6](110-C-6-human-oversight.md), [118 C-7](118-C-7-incident-flow.md), [123 C-4](123-C-4-documentation-pack.md), [124 C-9](124-C-9-control-mapping-report.md) |
| [Phase 10](../slm-agent-platform-epic-v3.md#phase-10-shared-memory-at-scale) | [125 L-1](125-L-1-shared-memory-at-scale.md) |
| [Phase 11](../slm-agent-platform-epic-v3.md#phase-11-later-ideas) | [126 L-2](126-L-2-system-prompt-improver.md), [127 L-3](127-L-3-router-slm.md) |

All 113 epic stories are covered exactly once (X-1 and P-2 as two issues each).

## Importing into GitHub

1. Create the labels (`story`, `decision`, `later`, `priority:*`, `phase:*`, `area:*`, `size:*`) and the milestones (W0–W12).
2. Create issues in index order with `gh issue create --title ... --label ... --milestone ... --body-file ...`, using the body below the frontmatter.
3. Replace each `NNN ID` reference with the real issue number, and link each issue to the epic.
