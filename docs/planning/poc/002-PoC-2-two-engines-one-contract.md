---
title: "PoC-2: Two engines, one contract, two lanes: prove the chassis is framework-agnostic"
labels: ["poc", "priority:P0", "area:harness"]
milestone: "Agent MVP"
index: 2
iteration: PoC-2
timebox: "2 weeks"
depends_on: ["PoC-1"]
backlog_refs: ["008 H-14", "009 CH-1", "013 CH-2", "054 H-16"]
---

## Question

Can two very different frameworks run as workloads behind the same `handle` contract, over A2A in the `sidecar` lane and over A2A in memory in the chassis's tests, with no framework code in the chassis and no key in any workload? Does a workload that is not Python pass the same contract?

## Why

The whole idea of the agent is that business logic can be anything. Proving that with two frameworks that work in very different ways (a typed agent loop and a graph) stops the contract from fitting one framework only. [ADR-001](../adr/001-chassis-delivery-model.md) makes `sidecar` the default lane, so the contract must hold over A2A as well as in-process, from the first engine. Doing it before the interfaces are added means a contract change is still cheap.

## Scope

- [ ] The `EngineConnector` interface: `setup`, `run` (an async stream of chassis events), `close`, and `capabilities`. Its `kind` is the lane.
- [ ] The `inprocess` connector from PoC-1, now refused outside the `fake` and `local` profiles.
- [ ] The `sidecar` connector: an a2a-sdk client that sends the request to the workload on localhost and streams its events back.
- [ ] The template A2A server from PoC-1, now in its own container, serving A2A on localhost only.
- [ ] The mapping between chassis events and A2A task updates from PoC-1, revised with what the frameworks need.
- [ ] `spec.engine.connector` picks the lane: `inprocess` or `sidecar`. The framework is the workload's own choice, not a chassis setting.
- [ ] A PydanticAI workload and a LangGraph workload, each in its own container with its own dependencies.
- [ ] A TypeScript echo workload over the JavaScript a2a-sdk (suggested: `@a2a-js/sdk`), about 100 lines, in its own container. It proves that `TaskInput`, `Context`, and the event schema carry nothing Python-specific while the contract is still v0.
- [ ] One event mapping per framework, in the workload: token deltas, tool calls, and usage metrics become chassis events.
- [ ] The same simplifier business logic written for each framework and for plain Python.
- [ ] The chassis model proxy: an OpenAI-compatible endpoint on the chassis. It adds the scoped LiteLLM key and forwards the call. Each workload's model client points at it, and no workload holds a key. The proxy keys each call to its inbound request by `traceparent`, which each framework's HTTP client must propagate (suggested: OpenTelemetry httpx instrumentation in the workload).
- [ ] An MCP tool endpoint on the chassis, backed by the fake `ToolPort`. One read-only tool (`glossary_lookup`) is defined once and served to every workload over MCP, so no framework needs its own tool conversion.
- [ ] An import-lint rule: the chassis imports no framework package.
- [ ] The lane contract suite: every case runs over A2A on localhost and over A2A in memory, and must give the same envelope and event stream.
- [ ] Every engine is tested offline against the fake model server from PoC-1, including streaming and tool calls.
- [ ] The `EnginePort` contract suite runs against the scripted fake engine and all three workloads.

## Reuse

- **Use:** PydanticAI and LangGraph as the two workloads. The a2a-sdk 1.x client in the `sidecar` connector, and its server in the template A2A server. The JavaScript a2a-sdk for the TypeScript echo. FastMCP 4 for the chassis's tool endpoint. LiteLLM behind the chassis model proxy. Optional: OpenInference instrumentors now, to see inside each framework while debugging.
- **Build:** the `EngineConnector` contract, the `inprocess` and `sidecar` connectors, the template A2A server, the chassis model proxy and tool endpoint, and one event mapping per framework. No product offers this part, so it is the core of our own code.
- **Watch:** a2a-sdk 1.x is a new major version, so pin it.
- Details: [reuse analysis](010-reuse-analysis.md)

## Out of scope

- More frameworks, growing the TypeScript echo into a full agent, and a remote solution (PoC-6a and PoC-6b).
- The `remote` lane and the trust rule (PoC-5).
- Chassis-only credentials for every internal service, and default-deny egress (PoC-5).
- Write tools, allow-lists, and the real `ToolPort` adapter (PoC-5 and backlog [054 H-16](../issues/054-H-16-tool-port.md)).
- The Anthropic-compatible model endpoint on the proxy, unless a workload needs it (backlog [013 CH-2](../issues/013-CH-2-outbound-model-proxy.md)).
- Framework-native memory or sessions. If a framework needs them, note it for PoC-4.

## Demo

The same request goes to four engines by swapping the workload container next to the chassis. Then the same cases run `inprocess` in the chassis's test suite. All four return the same envelope and stream deltas over A2A, and the three Python engines call the tool through the chassis. No workload container holds a key. The router shows the tokens for each, so the framework overhead is visible.

## Exit criteria

- [ ] All three Python engines pass their tests offline, against the fake model server.
- [ ] The TypeScript echo passes the lane contract suite with no chassis change.
- [ ] The lane contract suite passes: every case gives the same envelope and event stream over A2A on localhost and in memory.
- [ ] The fake engine and all four workloads pass the same `EnginePort` contract suite.
- [ ] All four engines pass the same response-shape tests, streaming and complete.
- [ ] Every outbound model and tool call carries the inbound request's trace id, per engine, and two concurrent requests in one replica each get their own budget. An engine whose client drops the header is listed, with the fix.
- [ ] The chassis has no import of any framework (lint passes).
- [ ] No workload container holds a key. Each workload's model client points at the chassis model proxy.
- [ ] The tool is defined once, served by the chassis over MCP, and works on all three engines.
- [ ] Event mapping size (lines of code), token overhead against plain Python, the local hop's extra latency (p50 and p95), the overhead per streamed delta, and the time to first token are recorded.
- [ ] Contract v1 is written down: the `handle` contract, the chassis event schema, and its A2A mapping, with any changes from v0 and why.

## Links

- Plan: [000-plan.md](000-plan.md) · Previous: [PoC-1](001-PoC-1-walking-skeleton.md) · Next: [PoC-3](003-PoC-3-one-interface-every-client.md)
- Decision: [ADR-001](../adr/001-chassis-delivery-model.md), [ADR-002](../adr/002-template-a2a-server-placement.md)
- Contract: [contract v1](../../contracts/contract-v1.md) (current), [contract v0](../../contracts/contract-v0.md) (PoC-1)
- Backlog issues this previews: [008 H-14](../issues/008-H-14-one-agent-interface.md), [009 CH-1](../issues/009-CH-1-engine-connectors-a2a.md), [013 CH-2](../issues/013-CH-2-outbound-model-proxy.md) (OpenAI-compatible only), [054 H-16](../issues/054-H-16-tool-port.md) (tool definition and the tool endpoint only)
- Epic: [B.1](../slm-agent-platform-epic-v3.md#b1), [B.3](../slm-agent-platform-epic-v3.md#b3), [B.5](../slm-agent-platform-epic-v3.md#b5)
