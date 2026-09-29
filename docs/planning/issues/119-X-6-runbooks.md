---
title: "X-6: Runbooks for common failures"
labels: ["story", "priority:P1", "phase:8-production", "area:infra", "size:M", "layer:platform"]
milestone: "W10 Production readiness"
index: 119
epic_id: X-6
depends_on: ["117 X-3"]
blocks: []
epic_refs: [E.5]
---

## Why

When an alert fires at night, the person on call needs steps, not a design doc. Runbooks cut the time to fix common failures and give auditors evidence of operational control. They come right after the SLO alerts, so each alert can link to its runbook.

## What

- One runbook per common failure, each with five parts: symptoms, checks, fix, rollback, and escalation.
- Failures to cover: model server down or GPU node lost; router or provider outage and high fallback rate; broker consumer lag and dead-letter growth; stuck or failed Temporal runs (resume and cancel); a bad config or workflow (rollback); registry not registering or search degraded; Postgres failover on CloudNativePG; object storage errors; evaluator drift.
- Chassis failures to cover, from [ADR-001](../adr/001-chassis-delivery-model.md):
  - a bad chassis release: stop the rings, and pin the previous tag;
  - a pod refused by the minimum-version rule;
  - a compromised workload: revoke its key, tool grants, and network label, which cuts off that service alone. The ADR leaves this playbook out of scope, so it lives here;
  - a `remote` endpoint or cloud auth failure;
  - the urgent-fix order from ADR item 10: change the central limits first, then roll out one chassis image in fast rings.
- Every alert from X-3 carries a `runbook_url` annotation.
- Runbooks live in the repo as Markdown and are reviewed like code.
- A CI check that every alert rule has a runbook link that resolves.

## Out of scope

- The serious-incident runbook (118 C-7).
- The restore runbook, which comes with the restore test (121 X-4).
- New alert rules (117 X-3).

## Acceptance criteria

- [ ] Each failure in the list has a runbook with all five parts.
- [ ] Every alert rule links to a runbook, and the CI check passes.
- [ ] At least three runbooks (suggested: model server down, stuck run, bad config) are tried in a drill on staging, and the gaps found are fixed.
- [ ] In a drill, a person who did not write the runbook follows it without help.

## Dependencies

- Depends on: [117 X-3](117-X-3-slos-alerts.md)
- Blocks: none

## References

- Epic story: [X-6 in Phase 8](../slm-agent-platform-epic-v3.md#phase-8-production-readiness)
- Epic context: [E.5](../slm-agent-platform-epic-v3.md#e5)
- Backlog plan: [000-plan.md](000-plan.md)
