---
title: "X-3: SLOs and alerts for every agent"
labels: ["story", "priority:P1", "phase:8-production", "area:infra", "size:M", "layer:platform"]
milestone: "W10 Production readiness"
index: 117
epic_id: X-3
depends_on: ["014 H-7", "038 X-1a", "056 CH-7"]
blocks: ["118 C-7", "119 X-6"]
epic_refs: [G.2]
---

## Why

Without SLOs, nobody knows when an agent is failing its users. SLOs and alerts turn the per-agent dashboards into signals that reach a person. This issue delivers the Phase 8 done-when part "alerts fire on SLO breaches". It comes before the incident flow and the runbooks, which start from these alerts.

## What

- SLOs for every agent: availability, p95 latency, and error rate, plus fallback rate for agents with a fallback route (target < 10%, from the success metrics).
- Default targets in the library chart (024 CH-3), overridable per agent (suggested: an `slo` block in the agent config, added to the schema).
- Prometheus recording and alert rules per agent, generated on deploy (suggested: burn-rate alerts on the error budget).
- Alerts for broker consumer lag, dead-letter topics, and failed Temporal runs, routed the same way.
- SLOs compared per ring to gate the chassis rollout (056 CH-7). A ring is promoted only while its services hold their SLOs.
- Every alert carries the chassis version as a label, so a fault can be tied to a chassis release.
- An alert for pods below the minimum chassis version while that rule is in warn mode (056 CH-7).
- Alerts for `remote` endpoint failures and cloud auth failures (055 CH-6).
- Alert routing to on-call (suggested: Alertmanager, part of the Prometheus stack).
- SLO panels and remaining error budget on each agent's Grafana dashboard.
- The same rules in Docker Compose and in the cloud.

## Out of scope

- Incident events and the serious-incident runbook (118 C-7).
- Runbooks for each alert (119 X-6).
- Load tests (122 X-5).

## Acceptance criteria

- [ ] Every active agent in the registry has SLO rules, and a newly deployed agent gets them with no manual step.
- [ ] A fault test on staging (a fake agent returning errors) fires the error-rate alert, and the alert reaches on-call (Phase 8 done-when).
- [ ] A latency fault test fires the p95 latency alert.
- [ ] A simplifier fallback rate above 10% fires an alert.
- [ ] A chassis canary with a raised error rate fires an alert that carries its chassis version.
- [ ] Each agent dashboard shows its SLOs and remaining error budget.

## Dependencies

- Depends on: [014 H-7](014-H-7-observability.md), [038 X-1a](038-X-1a-terraform-first-cloud.md), [056 CH-7](056-CH-7-chassis-release-rings.md)
- Blocks: [118 C-7](118-C-7-incident-flow.md), [119 X-6](119-X-6-runbooks.md)

## References

- Epic story: [X-3 in Phase 8](../slm-agent-platform-epic-v3.md#phase-8-production-readiness)
- Epic context: [G.2](../slm-agent-platform-epic-v3.md#g2)
- Backlog plan: [000-plan.md](000-plan.md)
