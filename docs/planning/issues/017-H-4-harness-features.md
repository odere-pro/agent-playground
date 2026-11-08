---
title: "H-4: Harness features: schema check, evaluator gate, retry, fallback, timeout, budget"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:M", "layer:chassis", "layer:agent-profile"]
milestone: "W2 Chassis MVP"
index: 17
epic_id: H-4
depends_on: ["010 H-12", "012 H-3", "009 CH-1", "013 CH-2"]
blocks: ["041 A-1", "042 A-2", "054 H-16", "064 D-1", "135 CH-15", "136 CH-16"]
epic_refs: [B.2, G.1]
---

## Why

These chassis pipeline stages make every call safe and bounded: bad output is caught, failures retry or fall back, and no call runs past its budget. Under [ADR-001](../adr/001-chassis-delivery-model.md), the framework owns the model loop in the `sidecar` and `remote` lanes, so each stage must also work through the connector and the model proxy (gap (e) in the [backlog plan](000-plan.md#adr-001-follow-ups)). The simplifier's `facts_kept` gate and fallback (042 A-2) and the golden set judge (064 D-1) are built on them. Idempotency is owned by 018 H-18, not this issue.

The gate's cost, a second model call per gated request, is decided in [ADR-010](../adr/010-evals-offline-tool-online-gate.md): a sample rate by request id, an async mode that scores after the answer, and the judge's tokens as one more `metrics` event. The default mode and sample rate are confirmed by phase B's judge numbers (001 DEC-1, ADR-010 open question 2).

## What

- Evaluator gate (module `evaluator_gate`): calls the `EvaluatorPort` named in `harness.evaluator` and compares the score to `harness.threshold`.
- Retry: up to `harness.retries` more attempts after a gate failure or a model error.
- Fallback: after the last retry, one call to `model.fallback_route`; the response has `status: fallback`.
- Timeout: the envelope's `budget.timeout_ms`.
- Budget: the envelope's `budget.max_tokens` caps tokens across all attempts.
- How each stage works in the `sidecar` and `remote` lanes:
  - Retry calls `handle` again through the connector. That reruns the whole agent, tool calls included. Write tools are safe through the idempotency key (054 H-16), but each attempt costs the full token price, which the budget caps.
  - A timeout cancels the A2A task. The run ends with one retryable `a2a.timeout` error.
  - The budget is enforced in the model proxy (013 CH-2), so it holds even if the workload ignores `ctx.budget`. The proxy keys the workload's calls to the request by `traceparent`, and refuses a call when nothing is left (429 `budget_exhausted`, counted as `chassis.model_calls_refused`).
  - Fallback: one more attempt with `ctx.model_route` set to `spec.model.fallback_route`, which the model proxy enforces for every call of that attempt (ADR-010 item 4).
- Decided in [ADR-010](../adr/010-evals-offline-tool-online-gate.md) and [contract v6](../../contracts/contract-v6.md): the gate is `GatedEngine`, a wrapper around the connector. Sync mode scores, retries, then falls back with `end.status: fallback`. Async mode, and every stream, scores after the answer and never changes it. `spec.harness.evaluator_sample_rate` gates by a hash of the request id. The score is a span attribute, not a `metrics` field.
- An evaluator outage fails open as built (ADR-010 item 4), decided 2026-11-06.
- A `metrics` event per attempt (attempt number, score, route, and reason), so the fallback rate can be measured.
- Every setting read from config, with safe defaults.

## Out of scope

- Idempotency and the result cache (018 H-18).
- The real `facts_kept` evaluator and the simplifier's gate settings (042 A-2).
- Budgets per orchestrator run (107 O-6).
- The `remote` lane (055 CH-6). It reuses these stages.
- The output schema check, and `harness.timeout_ms` and `harness.budget.max_tokens` in agent config. `HarnessSpec` has none of them, and no product phase needs them. The envelope's budget is the limit.
- The budget error code: P-11 in the [backlog review proposals](../../reviews/2026-11-06-backlog-review/proposals.md). A Python workload reports the proxy's `budget_exhausted` as `http_429`, so a client cannot tell a spent budget from a rate limit by its code.
- The TypeScript echo's budget refusal. It has no tool loop, so the case is a strict xfail.
- Retries and fallbacks LiteLLM can make per route (136 CH-16, gap review of 2026-11-07; decided in [ADR-013](../adr/013-model-call-retries-belong-to-the-router.md)). This issue's retry stage covers only the errors LiteLLM cannot see: a bad answer, a schema failure, a workload error. It never retries a `ModelError` the router already retried.

## Acceptance criteria

- [x] With `retries: 1` and a fake evaluator that scores below `threshold`, the call retries once, then falls back, and the response has `status: fallback`, in both transports. · Delivered in PoC-7, PoC-8: `packages/chassis/tests/test_evaluator_gate.py::test_a_failing_answer_retries_once_then_falls_back`, `pocs/poc-07-cross-cutting-decisions/tests/test_poc07_evals_gate.py::test_poc07_evals_gate_retries_once_then_falls_back` (sidecar), `pocs/poc-08-cross-cutting-build/tests/test_poc08_c6_gate.py::test_the_gate_retries_then_falls_back_to_the_configured_route` (every lane)
- [x] A call that runs past `timeout_ms` stops and returns `status: error` with a timeout reason. In the `sidecar` lane, the A2A task is cancelled. · Delivered in PoC-4: `pocs/poc-04-stateless-scalable/tests/test_timeout_budget_per_engine.py::test_past_timeout_ms_the_run_ends_with_a2a_timeout_and_the_task_is_cancelled`, `packages/chassis/tests/test_a2a_sidecar.py::test_timeout_mid_run_yields_a2a_timeout_and_cancels`
- [x] A call that would go past `budget.max_tokens` stops before the next model call. · Delivered in PoC-4: `pocs/poc-04-stateless-scalable/tests/test_timeout_budget_per_engine.py::test_a_spent_budget_refuses_the_next_model_call_with_budget_exhausted`, `packages/chassis/tests/test_model_proxy.py::test_an_exhausted_run_is_refused_with_429_complete_and_streamed`
- [x] A workload that ignores `ctx.budget` is still stopped by the model proxy. · Delivered in PoC-4, PoC-5: `pocs/poc-05-sandboxed/tests/test_poc05_hostile_offline.py::test_h29_past_the_run_budget_is_429_and_a_run_with_budget_left_is_200`; no workload reads `ctx.budget.max_tokens`
- [x] Retries and fallbacks show in metrics as a fallback rate. · Delivered in PoC-8: `packages/chassis/tests/test_metrics.py::test_gated_runs_and_fallbacks_are_both_counted`, [contract v7 A.3](../../contracts/contract-v7.md#a3-the-families) (`chassis_gate_fallbacks_total` over `gate_runs`)
- [x] With `evaluator_gate` off, no evaluator call is made. · Delivered in PoC-7: `packages/chassis/tests/test_evaluator_gate.py::test_gate_off_makes_no_evaluator_call_and_does_not_wrap`, `pocs/poc-07-cross-cutting-decisions/tests/test_poc07_evals_gate.py::test_poc07_evals_gate_off_makes_no_evaluator_call`
- [ ] A scripted model error is retried up to `retries` times, then falls back to `fallback_route`, in both lanes, and the retry and fallback are counted.
- [ ] Across attempts the run keeps one `timeout_ms` and one `max_tokens`: each attempt gets what is left, and the judge's tokens count against the budget.
- [ ] Retry, fallback, timeout, and budget paths are tested offline with the fake `EvaluatorPort` and scripted model errors.

## Dependencies

- Depends on: [010 H-12](010-H-12-config-loader.md), [012 H-3](012-H-3-model-port.md), [009 CH-1](009-CH-1-engine-connectors-a2a.md), [013 CH-2](013-CH-2-outbound-model-proxy.md)
- Blocks: [041 A-1](041-A-1-simplifier-core.md), [042 A-2](042-A-2-evaluator-gate-fallback.md), [054 H-16](054-H-16-tool-port.md), [064 D-1](064-D-1-golden-set-agent.md), [135 CH-15](135-CH-15-backpressure.md), [136 CH-16](136-CH-16-model-proxy-failures.md)

## References

- Epic story: [H-4 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [B.2](../slm-agent-platform-epic-v3.md#b2) · [G.1](../slm-agent-platform-epic-v3.md#g1)
- Backlog plan: [000-plan.md](000-plan.md)
