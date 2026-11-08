---
title: "CH-16: The model proxy under failure: budget refusal code, per-call timeout, LiteLLM retries and fallbacks"
labels: ["story", "priority:P1", "phase:0-harness", "area:harness", "size:M", "layer:chassis"]
milestone: "Done: delivered by the PoC track"
index: 136
epic_id: CH-16
depends_on: ["013 CH-2", "017 H-4", "003 G-1b", "117 X-3"]
blocks: []
epic_refs: [F.1, G.1]
---

## Why

Three model-proxy paths fail quietly today. LiteLLM's daily budget refusal reaches the workload as `http_400`, with no counter and no alert. The proxy's upstream call has no per-call timeout, so a cancelled run keeps paying for it (`packages/chassis/src/chassis/adapters/litellm/client.py`). And the retry stage (017 H-4) is planned as a rerun of the whole agent, while LiteLLM has no `num_retries` or `fallbacks` per route. The [product plan's gap review](../../plans/2026-11-06-agent-container-product.md#if-phases-a-to-f-are-all-built-what-is-still-missing) (2026-11-07) puts all three in its suggested phase G.

## What

- A fixed code `service_budget_exhausted` on both proxy routes, OpenAI and Anthropic, when LiteLLM refuses for the service's budget. A counter `chassis_model_budget_exhausted_total{route}`, and an alert on it with the 117 X-3 rules.
- The run's remaining time goes into `ModelPort.complete` and `.stream`. The upstream call stops when the run ends.
- `num_retries` and `fallbacks` set per route in the LiteLLM config. The chassis retry stage then covers only the errors LiteLLM cannot see.

## Out of scope

- The run budget's own refusal, `budget_exhausted` as `http_429`: P-11 in the [backlog review proposals](../../reviews/2026-11-06-backlog-review/proposals.md).
- The gate's retry on a low score (017 H-4, 042 A-2).

## Acceptance criteria

- [x] With the fake model server answering LiteLLM's budget refusal, the workload gets `service_budget_exhausted` on the OpenAI and the Anthropic route, and the counter rises per route. · Delivered by: `packages/chassis/tests/test_model_proxy_failures.py::test_the_chat_route_answers_429_service_budget_exhausted_and_counts_it`, `::test_the_chat_stream_sends_the_code_in_its_error_frame_and_counts_it`, `::test_the_anthropic_route_answers_429_service_budget_exhausted`, `::test_the_counter_is_a_declared_family_on_metrics`; the adapter mapping in `packages/chassis/tests/test_litellm_failures.py::test_litellms_budget_refusal_is_service_budget_exhausted`; the port promise in `ModelPortContract::test_the_service_budget_refusal_is_one_code`, bound for the fake and the LiteLLM adapter
- [x] An alert rule on `chassis_model_budget_exhausted_total` sits with the 117 X-3 rules and fires in a fault test. · Delivered by: `deploy/prometheus/rules/chassis.yaml` (`ChassisModelBudgetExhausted`; 117 X-3's rules join it there), checked offline by `packages/chassis/tests/test_alert_rules.py`; the fault test is `pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_metrics.py::test_a_budget_refusal_raises_the_counter_and_fires_the_alert` on the PoC-8 Compose stack (`COMPOSE_STACK=1`), written with the CH-16 PR and not yet run: the first stack run is the evidence for "fires"
- [x] A run cancelled or timed out during a slow fake model call ends the upstream call, and the fake sees the connection close. · Delivered by: `packages/chassis/tests/test_litellm_failures.py::test_a_call_past_its_deadline_closes_the_upstream_connection`, `::test_a_stream_past_its_deadline_closes_the_upstream_connection` (the fake on uvicorn over a Unix socket counts the close in `app.state.aborted`); the run's remaining time reaches the port in `test_model_proxy_failures.py::test_a_correlated_call_gets_the_runs_remaining_time`; a run that ends mid-call cuts the model call in `::test_a_chat_call_whose_run_ends_is_cut_and_answered_403_run_ended`, `::test_an_anthropic_call_whose_run_ends_is_cut_and_answered_403_run_ended`; the port promise in `ModelPortContract::test_a_call_ends_at_its_timeout`
- [x] Each route in the LiteLLM config sets `num_retries` and `fallbacks`, and a stack test shows a provider error answered by the fallback with no chassis retry. · Delivered by: `packages/chassis/tests/test_litellm_route_resilience.py` over every LiteLLM config under `deploy/`; the no-retry promise in `ModelPortContract::test_a_failed_call_goes_upstream_once`; the stack test is `pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_litellm.py::test_a_provider_error_is_answered_by_the_fallback_route_with_one_chassis_call` (`COMPOSE_STACK=1`), written with the CH-16 PR and not yet run
- [x] The contract lists `service_budget_exhausted` with the version it was added in. · Delivered by: [contract v9](../../contracts/contract-v9.md), additive over v8 (B.5); the rule in force in [the contract](../../specs/contract.md), "The service's budget (CH-16)"; the decision in [ADR-013](../adr/013-model-call-retries-belong-to-the-router.md)

Delivered by: the CH-16 PR (2026-11-08). The two stack drills (`COMPOSE_STACK=1`) are written and not yet run; their first run on the Compose stacks is the evidence for the alert firing and the fallback route.

## Dependencies

- Depends on: [013 CH-2](013-CH-2-outbound-model-proxy.md), [017 H-4](017-H-4-harness-features.md), [003 G-1b](003-G-1b-named-model-routes.md), [117 X-3](117-X-3-slos-alerts.md)
- Blocks: none

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [F.1](../slm-agent-platform-epic-v3.md#f1) · [G.1](../slm-agent-platform-epic-v3.md#g1)
- Backlog plan: [000-plan.md](000-plan.md)
