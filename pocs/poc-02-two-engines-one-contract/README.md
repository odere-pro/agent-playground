# PoC-2: Two engines, one contract, two lanes: prove the chassis is framework-agnostic

Status: not started
Planning doc: [002-PoC-2-two-engines-one-contract.md](../../docs/planning/poc/002-PoC-2-two-engines-one-contract.md)
Time box: 2 weeks

## Question

Can two very different frameworks run as workloads behind the same `handle` contract, over A2A in the `sidecar` lane and over A2A in memory in the chassis's tests, with no framework code in the chassis and no key in any workload? Does a workload that is not Python pass the same contract?

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

## Exit criteria

Each one has a scenario test in `tests/` or a recorded reason it cannot have one yet.

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

## How to run

```bash
make test-poc POC=02
```

## Demo

Not recorded yet. The script and its output go to `demo/`.

## Notes and decisions

Dated files in `notes/`. `notes/backlog-changes.md` lists what the backlog should change when this iteration closes.
