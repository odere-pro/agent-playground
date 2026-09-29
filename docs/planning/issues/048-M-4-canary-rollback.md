---
title: "M-4: Canary rollout and one-step rollback"
labels: ["story", "priority:P1", "phase:2-simplifier-slm", "area:lifecycle", "size:S", "layer:platform"]
milestone: "W4 Simplifier agent live"
index: 48
epic_id: M-4
depends_on: ["047 M-3", "046 A-5"]
blocks: []
epic_refs: [G.4]
---

## Why

After shadow mode, a candidate needs a safe way to take real traffic and a fast way back. Canary rollout sends a small share of users to the new model, and one-step rollback undoes it. This is the last piece of the Phase 2 done-when: the SLM can be promoted and rolled back through CI.

## What

- Canary by router config only: weighted deployments behind the `simplifier-slm` route in LiteLLM (suggested steps: 5%, then 25%, then 100%).
- A CI job starts, advances, and ends a canary for a candidate that passed the eval gate (037 M-2) and shadow mode (047 M-3).
- Canary metrics per model version on the agent dashboard (046 A-5): `facts_kept` pass rate, fallback rate, error rate, and p95 latency.
- Automatic rollback when a threshold is breached. Suggested: fallback rate ≥ 10%, or an error rate above the current model's.
- One-step manual rollback: one CI job or command points the route back to the previous model version.
- Full promotion updates the model stage in MLflow and publishes `models.model.promoted.v1`.
- `versions` in each response shows which model version served it.

## Reuse

- **Use:** KServe `LLMInferenceService` canary traffic split on Kubernetes. suggested: LiteLLM route weights in Docker Compose.
- **Build:** the rollback command and the promotion event.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Shadow mode (047 M-3).
- The audit log that stores promotion events (072 C-3).
- Quantized variants (049 M-5), which reuse this flow.

## Acceptance criteria

- [ ] A CI job moves a candidate to 5% of `simplifier-slm` traffic by router config only, with no agent restart.
- [ ] Canary metrics show per model version on the agent dashboard.
- [ ] A forced breach (a candidate that fails every `facts_kept` check) rolls back automatically.
- [ ] A manual rollback is one CI job, and traffic is back on the previous version within one minute (suggested).
- [ ] Full promotion updates MLflow and publishes one `models.model.promoted.v1` event.
- [ ] Phase 2 done-when: a candidate that meets the targets on the held-out set is promoted to 100% and rolled back to the previous version through CI, end to end.

## Dependencies

- Depends on: [047 M-3](047-M-3-shadow-mode.md), [046 A-5](046-A-5-agent-dashboard.md)
- Blocks: none

## References

- Epic story: [M-4 in Phase 2](../slm-agent-platform-epic-v3.md#phase-2-simplifier-slm-and-model-lifecycle)
- Epic context: [G.4](../slm-agent-platform-epic-v3.md#g4)
- Backlog plan: [000-plan.md](000-plan.md)
