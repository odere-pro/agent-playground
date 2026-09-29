---
title: "M-3: Shadow mode: new model runs next to the current one without serving users"
labels: ["story", "priority:P1", "phase:2-simplifier-slm", "area:lifecycle", "size:M", "layer:platform"]
milestone: "W4 Simplifier agent live"
index: 47
epic_id: M-3
depends_on: ["037 M-2", "041 A-1", "042 A-2", "013 CH-2"]
blocks: ["048 M-4", "075 E-3"]
epic_refs: [G.4]
---

## Why

A model that passes the eval gate can still behave differently on live traffic. Shadow mode runs a candidate next to the current model on real requests, without serving users, so the team sees real-world quality before any user is exposed. It needs the eval gate (037 M-2) and the live simplifier (041 A-1), and it comes before canary rollout (048 M-4).

## What

- Suggested config: `model.shadow_route` and `model.shadow_sample_rate` in the agent config.
- For a sampled share of calls, the same request goes to the shadow route after the user's answer is complete. The caller only ever gets the current model's answer.
- In the `sidecar` lane, only the model proxy sees the request (gap (e) in [the gaps in the backlog plan](000-plan.md#adr-001-follow-ups)). suggested: the model proxy (013 CH-2) or LiteLLM makes the shadow calls. This issue decides which.
- Shadow calls have their own budget and timeout. On error they are dropped, not retried.
- Both outputs are scored with the same `facts_kept` evaluator (`evaluator-facts`).
- A shadow report per candidate in MLflow (032 M-1), linked to the model version: `facts_kept`, pass rate, would-be fallback rate, tokens, and latency, side by side with the current model.
- Shadow outputs are never returned and never sent as `agents.task.completed.v1` events. They show in traces, tagged `shadow`.
- Shadow traffic shows as its own line in the router's cost counts.

## Out of scope

- Canary rollout and rollback (048 M-4).
- The golden set eval gate (037 M-2).
- Quantized variants (049 M-5).

## Acceptance criteria

- [ ] Who makes the shadow calls (model proxy or LiteLLM) is decided and written down.
- [ ] A candidate route runs in shadow on staging at the configured sample rate, switched on and off by config only.
- [ ] The caller always gets the current model's answer, checked by a test where the shadow route returns a marker text.
- [ ] A shadow error or timeout never fails the user's call.
- [ ] User p95 latency with shadow on stays within 5% of shadow off (suggested threshold).
- [ ] The MLflow shadow report compares the candidate and the current model on at least 1,000 live calls (suggested size).
- [ ] No shadow output appears in a result event or a record.

## Dependencies

- Depends on: [037 M-2](037-M-2-eval-gate-ci.md), [041 A-1](041-A-1-simplifier-core.md), [042 A-2](042-A-2-evaluator-gate-fallback.md), [013 CH-2](013-CH-2-outbound-model-proxy.md)
- Blocks: [048 M-4](048-M-4-canary-rollback.md), [075 E-3](075-E-3-evaluator-replaces-judge.md)

## References

- Epic story: [M-3 in Phase 2](../slm-agent-platform-epic-v3.md#phase-2-simplifier-slm-and-model-lifecycle)
- Epic context: [G.4](../slm-agent-platform-epic-v3.md#g4)
- Backlog plan: [000-plan.md](000-plan.md)
