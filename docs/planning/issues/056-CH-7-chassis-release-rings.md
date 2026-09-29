---
title: "CH-7: Chassis release in rings, with a per-service pin and a minimum-version admission rule"
labels: ["story", "priority:P1", "phase:0-harness", "area:harness", "size:M", "layer:chassis"]
milestone: "W5 Chassis completion"
index: 56
epic_id: CH-7
depends_on: ["024 CH-3", "055 CH-6", "014 H-7"]
blocks: ["077 C-1", "083 R-2", "117 X-3"]
epic_refs: [G.4]
---

## Why

[ADR-001](../adr/001-chassis-delivery-model.md) items 9 and 10: a chassis release is one image, rolled out in rings, with no service rebuild. One bad release could reach every service at once. Rings, a canary, and per-service pins limit how far it gets. A minimum-version admission rule makes sure that fixes actually land. This comes before the registry and the orchestrator add many services.

## What

- Rings: a canary, then 10%, then all. suggested: the canary is one non-critical service.
- Promotion to the next ring when the error rate and latency hold. suggested: for one hour.
- suggested: Argo CD sync waves or Argo Rollouts to drive the rings.
- A per-service pin to the previous tag, and a one-step rollback.
- The chassis version reported by `/manifest` (051 H-13), so the rollout can be checked per service.
- A minimum-version admission rule, in the same policy set as 055 CH-6. It warns first, then enforces.
- Schema compatibility across a rollout: the chassis accepts the current and the previous major version of the event schema (007 H-1), so a workload built before the release keeps working while the rings advance.
- The urgent-fix order (ADR item 10), written down:
  1. Change the central limits first (key, allow-list, network policy). They take effect at once.
  2. Then build one chassis image and roll it out in fast rings.

## Reuse

- **Use:** Argo CD, as planned. suggested: Argo CD sync waves or Argo Rollouts for the rings. Kyverno or ValidatingAdmissionPolicy for the minimum-version rule, in the same policy set as 055 CH-6.
- **Build:** the ring order, the promotion checks, and the pin and rollback steps.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- SLO alerts per ring (117 X-3).
- Runbooks for a bad release (119 X-6).
- Model canaries (048 M-4). They are a different rollout.

## Acceptance criteria

- [ ] A new chassis tag reaches the canary, then 10%, then all services, with no service image rebuilt.
- [ ] A canary with a raised error rate stops the rollout before the next ring.
- [ ] A service pinned to the previous tag keeps it through a rollout, and a rollback is one change.
- [ ] A pod below the minimum version gets a warning in warn mode and is refused in enforce mode.
- [ ] `/manifest` on every service reports the chassis version it runs.
- [ ] Before a ring rollout, the contract suite passes against a pinned workload image built on the previous event schema version.
- [ ] The urgent-fix path is written down and tried once on the local cluster.

## Dependencies

- Depends on: [024 CH-3](024-CH-3-helm-library-chart.md), [055 CH-6](055-CH-6-remote-lane-trust-rule.md), [014 H-7](014-H-7-observability.md)
- Blocks: [077 C-1](077-C-1-governance-enforcement.md), [083 R-2](083-R-2-kubernetes-controller.md), [117 X-3](117-X-3-slos-alerts.md)

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.4](../slm-agent-platform-epic-v3.md#g4)
- Backlog plan: [000-plan.md](000-plan.md)
