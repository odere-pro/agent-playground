---
title: "H-3: Model port to the LLM router, plus direct vLLM and llama.cpp adapters"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:M", "layer:agent-profile"]
milestone: "W2 Chassis MVP"
index: 12
epic_id: H-3
depends_on: ["003 G-1b", "007 H-1", "004 G-2"]
blocks: ["013 CH-2", "014 H-7", "015 H-8", "017 H-4", "044 G-4", "054 H-16", "062 H-11"]
epic_refs: [F.1, I, R3]
---

## Why

Agents call models only through the LLM router, so any model is swapped by router config only. The port calls a named route (003 G-1b), which is why it comes after the routes. Direct vLLM and llama.cpp adapters let a developer test an agent locally without running the router. Under [ADR-001](../adr/001-chassis-delivery-model.md), each service calls LiteLLM with its own scoped virtual key, and the key, not the request, decides whose spend it is.

## What

- A `ModelPort` adapter that calls the LiteLLM router with the route named in `spec.model.route`.
- Model output mapped to internal `delta` and `metrics` events: input and output tokens, latency, and route.
- Direct adapters for a local vLLM server and a local llama.cpp server (CPU), picked by config. They are allowed only in the `local` and `fake` profiles. In the cluster, egress allows only LiteLLM.
- `temperature` and `max_tokens` from config. Temperature defaults to 0, so calls are deterministic by default.
- Spend is attributed by the service's own LiteLLM virtual key, not by request metadata, so router counting (004 G-2) puts the spend on the right service. A workload cannot move spend to another agent.
- Request metadata carries the trace and request IDs only.
- The route version reported in the response `versions` field.
- The route's native tool-call flag read and kept on `Context`, for the tool port later.
- Status after PoC-1: `LiteLLMModel` (`chassis.adapters.litellm`) exists, is picked by `spec.adapters.model: litellm`, and binds `ModelPortContract` next to the fake (`packages/chassis/tests/test_contracts.py::TestLiteLLMModel`). Offline it runs against the fake model server over an ASGI transport. Delivered: route switch by config, tokens in the `metrics` event, route in `versions`. Open: direct vLLM and llama.cpp adapters, `latency_ms` in the `metrics` event, the virtual-key spend test, the temperature default, and the `cloud` profile refusal.

## Reuse

- **Use:** LiteLLM virtual keys: one per service, scoped to its routes and budget. The router attributes spend by key.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Retry, fallback to `fallback_route`, timeout, and budget (017 H-4).
- History swap before a model call (044 G-4).
- Tool calls and JSON with guided decoding (054 H-16).
- The workload-facing model proxy (013 CH-2). It uses this port.
- Provisioning the scoped per-service keys, and the default-deny egress policy (026 CH-4).

## Acceptance criteria

- [ ] The echo agent with a model step gets a streamed answer from `big-default` through the router.
- [x] Changing `spec.model.route` in config changes the model with no code change. Delivered in PoC-1: `pocs/poc-01-walking-skeleton/tests/test_model_adapter.py::test_switching_the_model_adapter_is_a_config_change`; the route switch in `pocs/poc-01-walking-skeleton/demo/2026-09-29-demo-fake-variant.md`.
- [ ] The same agent runs against a local vLLM server and a local llama.cpp server with only a config change.
- [ ] Token counts and latency arrive as a `metrics` event. PoC-1 delivered the token counts (the `metrics` frame in `pocs/poc-01-walking-skeleton/demo/2026-09-29-demo-fake-variant.md`); `latency_ms` is still `null` there, so this stays open.
- [ ] The router's usage records put the spend on the calling service's virtual key, even when the request metadata names another agent (tested with two virtual keys created by the test).
- [ ] Request metadata carries the trace and request IDs, and nothing that picks whose spend it is.
- [ ] The direct vLLM and llama.cpp adapters fail at start in the `cloud` profile.
- [x] The route version appears in the response `versions` field. Delivered in PoC-1: `pocs/poc-01-walking-skeleton/tests/test_serve.py::test_stream_and_complete_carry_the_same_output`.
- [ ] Temperature is 0 when the config does not set it.
- [ ] Each model adapter (router, direct vLLM, llama.cpp) sits behind `ModelPort`, is picked by `spec.adapters.model`, and passes the same contract suite as the fake.

## Dependencies

- Depends on: [003 G-1b](003-G-1b-named-model-routes.md), [007 H-1](007-H-1-harness-library-ports-envelope.md), [004 G-2](004-G-2-token-cost-counting.md)
- Blocks: [013 CH-2](013-CH-2-outbound-model-proxy.md), [014 H-7](014-H-7-observability.md), [015 H-8](015-H-8-testing-kit.md), [017 H-4](017-H-4-harness-features.md), [044 G-4](044-G-4-history-swap.md), [054 H-16](054-H-16-tool-port.md), [062 H-11](062-H-11-adapter-package.md)

## References

- Epic story: [H-3 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [F.1](../slm-agent-platform-epic-v3.md#f1) · [I](../slm-agent-platform-epic-v3.md#app-i) · [R3](../slm-agent-platform-epic-v3.md#r3)
- Backlog plan: [000-plan.md](000-plan.md)
