---
title: "CH-2: Outbound model proxy: an OpenAI- and Anthropic-compatible URL for workloads, forwarded with the service's scoped key"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:M", "layer:chassis", "layer:agent-profile"]
milestone: "W2 Chassis MVP"
index: 13
epic_id: CH-2
depends_on: ["012 H-3", "009 CH-1"]
blocks: ["017 H-4", "041 A-1", "044 G-4", "047 M-3", "054 H-16"]
epic_refs: [F.1, R3, J]
---

## Why

In the `sidecar` lane the workload holds no key ([ADR-001](../adr/001-chassis-delivery-model.md), item 6). Frameworks call models over HTTP with their own clients, pointed at a base URL. So the chassis gives every workload a model URL of its own. The proxy adds the service's scoped key and forwards the call to LiteLLM. It stays thin: it counts, traces, and applies per-call budgets. The hard limits (models, budgets, rate limits) stay on the key in LiteLLM, so they hold even if the proxy is bypassed.

## What

- An OpenAI-compatible `/v1/chat/completions` and an Anthropic-compatible `/v1/messages` on a localhost port of the chassis, streaming and complete. In the `remote` lane the same proxies also listen on the pod IP, and each remote authenticates (055 CH-6 adds the credential). The chassis's public port never shares the localhost listener.
- Every call is keyed to its inbound request by `traceparent`, which the workload's HTTP client propagates (suggested: OpenTelemetry httpx instrumentation, switched on by the service template). That is what lets the proxy apply the request's budget and route when several requests run in one replica. A call with no trace id is served, counted against a per-replica budget (suggested: `harness.budget.max_tokens` per minute), and logged with a warning.
- Each call goes through `ModelPort` (012 H-3) to LiteLLM with the service's virtual key. The workload's own `Authorization` header is ignored.
- Per-call accounting: tokens and cost go into the request's metrics.
- The per-call budget from `ctx` (`budget.max_tokens`): a call that would go over it is refused or stopped.
- One span per model call, joined to the request's trace.
- The workload asks for a named route, and the proxy passes it on. suggested: on a fallback retry, the chassis sets the route for that request (see gap (e) in the backlog plan).
- Nothing else: no prompt changes, no caching, and no routing logic of its own.

## Reuse

- **Use:** the official `openai` and `anthropic` SDK types, already used by 011 H-2, and `httpx` streaming for the pass-through.
- **Build:** a thin proxy: it adds the scoped key, counts tokens, traces the call, and applies per-call budgets. It does no routing of its own.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- The MCP tool proxy (054 H-16).
- History swap (044 G-4), which decides whether it runs here or in LiteLLM.
- Provisioning the per-service keys and refusing calls without one (026 CH-4).
- The scripted fake model server (015 H-8). This issue uses the fake `ModelPort` adapter from 007 H-1.
- The `remote`-lane credential for the proxies (055 CH-6).

## Acceptance criteria

- [ ] A stock OpenAI client and a stock Anthropic client in the workload container, pointed at the proxy, get streamed and complete answers.
- [ ] The workload container holds no LiteLLM key. The proxy adds the service's key.
- [ ] A call that would go over the request's budget is refused, and the workload gets a clear error.
- [ ] Two concurrent requests in one workload replica each get their own budget: the one that goes over is refused, the other completes.
- [ ] A model call with no trace id is served, counted against the replica budget, and logged with a warning.
- [ ] Token counts from the proxy match the router's counts for the same calls. suggested: within 1%.
- [ ] Every model call shows as a span in the request's trace.
- [ ] In the `fake` profile, the proxy answers from the fake `ModelPort` adapter, with no network.

## Dependencies

- Depends on: [012 H-3](012-H-3-model-port.md), [009 CH-1](009-CH-1-engine-connectors-a2a.md)
- Blocks: [017 H-4](017-H-4-harness-features.md), [041 A-1](041-A-1-simplifier-core.md), [044 G-4](044-G-4-history-swap.md), [047 M-3](047-M-3-shadow-mode.md), [054 H-16](054-H-16-tool-port.md)

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [F.1](../slm-agent-platform-epic-v3.md#f1) · [R3](../slm-agent-platform-epic-v3.md#r3) · [J](../slm-agent-platform-epic-v3.md#app-j)
- Backlog plan: [000-plan.md](000-plan.md)
