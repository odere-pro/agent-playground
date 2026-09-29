---
title: "H-4: Harness features: schema check, evaluator gate, retry, fallback, timeout, budget"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:L", "layer:chassis", "layer:agent-profile"]
milestone: "W2 Chassis MVP"
index: 17
epic_id: H-4
depends_on: ["010 H-12", "012 H-3", "009 CH-1", "013 CH-2"]
blocks: ["041 A-1", "042 A-2", "064 D-1"]
epic_refs: [B.2, G.1]
---

## Why

These chassis pipeline stages make every call safe and bounded: bad output is caught, failures retry or fall back, and no call runs past its budget. Under [ADR-001](../adr/001-chassis-delivery-model.md), the framework owns the model loop in the `sidecar` and `remote` lanes, so each stage must also work through the connector and the model proxy (gap (e) in the [backlog plan](000-plan.md#adr-001-follow-ups)). The simplifier's `facts_kept` gate and fallback (042 A-2) and the golden set judge (064 D-1) are built on them. Idempotency is owned by 018 H-18, not this issue.

**Open concern (review 2026-09-29), to explore until we align:** with `evaluator_gate` on, every call pays a second model call and waits for it. Until 075 E-3 puts an encoder SLM on CPU behind `EvaluatorPort`, that roughly doubles p95 latency and adds a big-model call per request, which eats the saving the SLM route makes. To explore here: a sample rate for the gate (suggested: `harness.evaluator_sample_rate`), gating only when the model's own confidence is low, an asynchronous gate that scores after the answer is returned and feeds the golden set (as in 047 M-3), and the judge's own tokens, cost, and latency counted in the request's `metrics`.

## What

- Schema check: the output is checked against the agent's output schema. A failed check counts as a failed attempt.
- Evaluator gate (module `evaluator_gate`): calls the `EvaluatorPort` named in `harness.evaluator` and compares the score to `harness.threshold`.
- Retry: up to `harness.retries` more attempts after a schema failure, a gate failure, or a model error.
- Fallback: after the last retry, one call to `model.fallback_route`; the response has `status: fallback`.
- Timeout: `harness.timeout_ms` and the envelope's `budget.timeout_ms`; the smaller one wins.
- Budget: `harness.budget.max_tokens` and the envelope's `budget.max_tokens` cap tokens across all attempts.
- How each stage works in the `sidecar` and `remote` lanes:
  - Retry calls `handle` again through the connector. That reruns the whole agent, tool calls included. Write tools are safe through the idempotency key (054 H-16), but each attempt costs the full token price, which the budget caps.
  - A timeout cancels the A2A task.
  - The budget is enforced in the model proxy (013 CH-2), so it holds even if the workload ignores `ctx.budget`. The proxy keys the workload's calls to the request by `traceparent`.
  - Fallback. suggested: retry with `ctx.model.route` set to `fallback_route`, which the model proxy enforces for that request.
- A `metrics` event per attempt (attempt number, score, route, and reason), so the fallback rate can be measured.
- Every setting read from config, with safe defaults.

## Out of scope

- Idempotency and the result cache (018 H-18).
- The real `facts_kept` evaluator and the simplifier's gate settings (042 A-2).
- Budgets per orchestrator run (107 O-6).
- The `remote` lane (055 CH-6). It reuses these stages.

## Acceptance criteria

- [ ] Output that fails the schema check is retried, then sent to `fallback_route` after `retries` attempts, in both transports (`inprocess` and `sidecar`).
- [ ] With `retries: 1` and a fake evaluator that scores below `threshold`, the call retries once, then falls back, and the response has `status: fallback`, in both transports.
- [ ] A call that runs past `timeout_ms` stops and returns `status: error` with a timeout reason. In the `sidecar` lane, the A2A task is cancelled.
- [ ] A call that would go past `budget.max_tokens` stops before the next model call.
- [ ] A workload that ignores `ctx.budget` is still stopped by the model proxy.
- [ ] Retries and fallbacks show in metrics as a fallback rate.
- [ ] With `evaluator_gate` off, no evaluator call is made.
- [ ] Retry, fallback, timeout, and budget paths are tested offline with the fake `EvaluatorPort` and scripted model errors.

## Dependencies

- Depends on: [010 H-12](010-H-12-config-loader.md), [012 H-3](012-H-3-model-port.md), [009 CH-1](009-CH-1-engine-connectors-a2a.md), [013 CH-2](013-CH-2-outbound-model-proxy.md)
- Blocks: [041 A-1](041-A-1-simplifier-core.md), [042 A-2](042-A-2-evaluator-gate-fallback.md), [064 D-1](064-D-1-golden-set-agent.md)

## References

- Epic story: [H-4 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [B.2](../slm-agent-platform-epic-v3.md#b2) · [G.1](../slm-agent-platform-epic-v3.md#g1)
- Backlog plan: [000-plan.md](000-plan.md)
