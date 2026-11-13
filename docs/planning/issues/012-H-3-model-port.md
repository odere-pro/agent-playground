---
title: "H-3: Model port to the LLM router, with a route map per task"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:L", "layer:agent-profile"]
milestone: "W2 Chassis MVP"
index: 12
epic_id: H-3
depends_on: ["003 G-1b", "007 H-1", "004 G-2"]
blocks: ["013 CH-2", "015 H-8", "017 H-4", "044 G-4"]
epic_refs: [F.1, I, R3]
---

## Why

Agents call models only through the LLM router, so any model is swapped by router config only. The port calls a named route (003 G-1b), which is why it comes after the routes. A route map per task lets one config send each task to its own model with no agent change, which the product's phase B needs. Under [ADR-001](../adr/001-chassis-delivery-model.md), each service calls LiteLLM with its own scoped virtual key, and the key, not the request, decides whose spend it is.

## What

- A `ModelPort` adapter that calls the LiteLLM router with the route named in `spec.model.route`. Built in PoC-1: `LiteLLMModel` (`chassis.adapters.litellm`), picked by `spec.adapters.model: litellm`. Offline it runs against the fake model server over an ASGI transport.
- A route map per task, as `docs/specs/models.md` ("The route map per task") says. `spec.model.routes` names a route per task, and `spec.model.judge_route` names the judge's route. The pipeline resolves `routes[input.data.task]`, else `route`, into `ctx.model_route`. The model proxy holds every call of a run to the run's route; the gate's fallback route still replaces it. The map and `judge_route` reload with no restart. Built in PR #78.
- Model output mapped to internal `delta` and `metrics` events: input and output tokens, latency, and route.
- A small model stays reachable as a LiteLLM route (`local-small`), not as a direct adapter.
- `temperature` and `max_tokens` from config. Temperature defaults to 0, so calls are deterministic by default.
- Spend is attributed by the service's own LiteLLM virtual key, not by request metadata, so router counting (004 G-2) puts the spend on the right service. A workload cannot move spend to another agent.
- Request metadata carries the trace and request IDs only.
- The route version reported in the response `versions` field.
- The route's native tool-call flag read and kept on `Context`, for the tool port later.

## Reuse

- **Use:** LiteLLM virtual keys: one per service, scoped to its routes and budget. The router attributes spend by key.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Retry, fallback to `fallback_route`, timeout, and budget (017 H-4).
- `fallback_route` is restart-only (PR #83). Making it reloadable needs a `RELOADABLE` entry and a reload test; that is not decided.
- History swap before a model call (044 G-4).
- Tool calls and JSON with guided decoding (054 H-16).
- The workload-facing model proxy (013 CH-2). It uses this port.
- Provisioning the scoped per-service keys, and the default-deny egress policy (026 CH-4).
- Direct vLLM and llama.cpp adapters, and their refusal in the `cloud` profile. Parked on 2026-11-06 (product plan; models spec, What is parked).
- `agentctl` refusing a route the service's key lacks: 059 H-15.

## Acceptance criteria

- [x] The echo agent with a model step gets a streamed answer from `big-default` through the router. · Delivered by: `pocs/poc-08-cross-cutting-build/tests/test_poc08_router_live.py::test_the_echo_agent_gets_a_streamed_answer_from_big_default_through_the_router`, `pocs/poc-08-cross-cutting-build/notes/2026-11-13-router-live.md`
- [x] Changing `spec.model.route` in config changes the model with no code change. Delivered in PoC-1: `pocs/poc-01-walking-skeleton/tests/test_model_adapter.py::test_switching_the_model_adapter_is_a_config_change`; the route switch in `pocs/poc-01-walking-skeleton/demo/2026-09-29-demo-fake-variant.md`.
- [ ] Token counts and latency arrive as a `metrics` event. PoC-1 delivered the token counts (the `metrics` frame in `pocs/poc-01-walking-skeleton/demo/2026-09-29-demo-fake-variant.md`); `latency_ms` is still `null` there, so this stays open.
- [x] The router's usage records put the spend on the calling service's virtual key, even when the request metadata names another agent (tested with two virtual keys created by the test). · Delivered by: `pocs/poc-08-cross-cutting-build/tests/test_poc08_router_live.py::test_spend_goes_on_the_calling_key_even_when_metadata_names_another_agent`, `pocs/poc-08-cross-cutting-build/notes/2026-11-13-router-live.md`
- [x] Request metadata carries the trace and request IDs, and nothing that picks whose spend it is. · Delivered by: `packages/chassis/tests/test_model_call_metadata.py::test_request_metadata_carries_the_trace_and_request_ids_only`, `::test_an_uncorrelated_call_sends_no_metadata_and_no_tag`, `packages/chassis/tests/test_litellm.py::test_the_agent_name_picks_no_spend_tag`, `packages/chassis/tests/test_model_proxy_guardrails_key.py`.
- [x] The route version appears in the response `versions` field. Delivered in PoC-1: `pocs/poc-01-walking-skeleton/tests/test_serve.py::test_stream_and_complete_carry_the_same_output`.
- [x] Temperature is 0 when the config does not set it. · Delivered by: `packages/chassis/tests/test_litellm.py::test_temperature_is_0_when_no_caller_sets_it`, `packages/chassis/tests/test_model_proxy.py::test_temperature_is_0_when_the_body_names_none`. No `spec.model.temperature` field exists yet; the default is 0 at the port, the adapter, and the model proxy.
- [x] The router adapter and the fake sit behind `ModelPort`, are picked by `spec.adapters.model`, and pass the same contract suite. · Delivered in PoC-1: `packages/chassis/tests/test_contracts.py::TestLiteLLMModel`, `::TestScriptedModel`.
- [x] `ChassisConfig` holds a route map per task (`ModelSpec.routes`) and a `judge_route`, checked by the schema. · Delivered by: PR #78, `packages/chassis/tests/test_model_routes.py::test_the_published_config_schema_has_the_map_and_the_judge_route`, `::test_model_spec_resolves_a_task_through_the_map_else_the_route`, `::test_model_spec_refuses_an_empty_task_or_route_name`.
- [x] Admission enforces the resolved route and refuses another with 403 `model_route_denied`. · Delivered by: `packages/chassis/tests/test_model_route_denied.py::test_a_call_on_the_resolved_route_passes`, `::test_another_route_is_403_model_route_denied`, `::test_a_run_keeps_its_own_route_after_a_reload_drops_it`, `::test_a_run_with_no_resolved_route_refuses_every_route`, `::test_the_gates_fallback_route_is_the_runs_route_for_its_attempt`, `packages/chassis/tests/test_evaluator_gate.py::test_a_fallback_attempt_on_the_first_route_is_refused_not_redirected`
- [x] The route map reloads with no restart. · Delivered by: PR #78, `packages/chassis/tests/test_model_routes.py::test_a_reload_changes_the_map_with_no_restart`; `packages/chassis/tests/test_config_loader.py::test_a_reload_changes_the_route_map_and_the_judge_route`.

## Dependencies

- Depends on: [003 G-1b](003-G-1b-named-model-routes.md), [007 H-1](007-H-1-harness-library-ports-envelope.md), [004 G-2](004-G-2-token-cost-counting.md)
- Blocks: [013 CH-2](013-CH-2-outbound-model-proxy.md), [015 H-8](015-H-8-testing-kit.md), [017 H-4](017-H-4-harness-features.md), [044 G-4](044-G-4-history-swap.md)

## References

- Epic story: [H-3 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [F.1](../slm-agent-platform-epic-v3.md#f1) · [I](../slm-agent-platform-epic-v3.md#app-i) · [R3](../slm-agent-platform-epic-v3.md#r3)
- Backlog plan: [000-plan.md](000-plan.md)
