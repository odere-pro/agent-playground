---
title: "CH-7: Chassis release in rings, with a per-service pin and a minimum-version admission rule"
labels: ["story", "priority:P1", "phase:0-harness", "area:harness", "size:L", "layer:chassis"]
milestone: "W5 Chassis completion"
index: 56
epic_id: CH-7
depends_on: ["024 CH-3", "055 CH-6", "014 H-7"]
blocks: ["077 C-1", "083 R-2"]
epic_refs: [G.4]
---

## Why

[ADR-001](../adr/001-chassis-delivery-model.md) items 9 and 10: a chassis release is one image, rolled out in rings, with no service rebuild. One bad release could reach every service at once. Rings, a canary, and per-service pins limit how far it gets. A minimum-version admission rule makes sure that fixes actually land. This comes before the registry and the orchestrator add many services.

## What

- Rings: a canary, then 10%, then all. suggested: the canary is one non-critical service.
- Promotion to the next ring when the error rate and latency hold. suggested: for one hour.
- suggested: Argo CD sync waves or Argo Rollouts to drive the rings.
- A per-service pin to the previous tag, and a one-step rollback.
- The chassis version reported by `/manifest` (built: `packages/chassis/src/chassis/server/manifest.py`), so the rollout can be checked per service. Today `/manifest` reports the Python package version (`CHASSIS_VERSION = __version__`), not the image tag the rings roll out.
- Alerts that come with the rings, moved from 117 X-3 on 2026-11-06: SLOs compared per ring, so a ring is promoted only while its services hold them; an alert for pods below the minimum chassis version while that rule warns; alerts for `remote` endpoint failures.
- Schema compatibility across a rollout: the chassis accepts the current and the previous major version of the event schema (007 H-1), so a workload built before the release keeps working while the rings advance.
- The urgent-fix order (ADR item 10), written down:
  1. Change the central limits first (key, allow-list, network policy). They take effect at once.
  2. Then build one chassis image and roll it out in fast rings.

## Reuse

- **Use:** Argo CD, as planned. suggested: Argo CD sync waves or Argo Rollouts for the rings. The minimum-version rule moved to 026 CH-4: a ValidatingAdmissionPolicy, in the `agent-trust-rule` set (ADR-005 decision 4).
- **Build:** the ring order, the promotion checks, and the pin and rollback steps.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- SLO alerts per ring (117 X-3).
- Runbooks for a bad release (119 X-6).
- Model canaries (048 M-4). They are a different rollout.
- Rings across the first cloud's clusters, after 038 X-1a. Every box here runs on kind.
- The minimum-version admission rule, moved to 026 CH-4 on 2026-11-06. Since PR #79 it is rule 9 of `agent-trust-rule` (ValidatingAdmissionPolicy, [ADR-005](../adr/005-remote-lane-auth-and-trust-admission.md) decision 4), in `agent-lib.admission` and in `deploy/kind/poc05/admission/policy.yaml`. The chart renders the allowed tags from `chassis.image.supportedTags` (the current tag and the previous one), so bumping the tag in `agent-lib` moves the minimum. Since 2026-11-08 the rule is its own policy, `agent-chassis-version`, and its binding warns first (`admission.chassisVersionAction: Warn` for one ring cycle, then `Deny`; the 026 CH-4 box). The rings that drive the switch are this issue's. Two shapes for when they exist (platform-security review, 2026-11-08): a pair of bindings, Warn on the canary ring's namespaces and Deny elsewhere, in place of one cluster-wide value; and an annotation on the binding in Warn mode (suggested: `agents.platform/chassis-version-ring: warn`) so `kubectl get validatingadmissionpolicybinding` shows the ring without the values.

## Acceptance criteria

- [ ] A new chassis tag reaches the canary, then 10%, then all services, with no service image rebuilt.
- [ ] A canary with a raised error rate stops the rollout before the next ring.
- [ ] A service pinned to the previous tag keeps it through a rollout, and a rollback is one change.
- [ ] `/manifest` on every service reports the chassis version it runs.
- [ ] The chassis version in `/manifest` equals the image tag.
- [ ] Before a ring rollout, the contract suite passes against a pinned workload image built on the previous event schema version.
- [ ] The urgent-fix path is written down and tried once on the local cluster.
- [ ] Once the `sign` job is required, admission checks the chassis image's signature, and an unsigned image is refused on kind (gap review, 2026-11-07).

## Dependencies

- Depends on: [024 CH-3](024-CH-3-helm-library-chart.md), [055 CH-6](055-CH-6-remote-lane-trust-rule.md), [014 H-7](014-H-7-observability.md)
- Blocks: [077 C-1](077-C-1-governance-enforcement.md), [083 R-2](083-R-2-kubernetes-controller.md)

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.4](../slm-agent-platform-epic-v3.md#g4)
- Backlog plan: [000-plan.md](000-plan.md)
