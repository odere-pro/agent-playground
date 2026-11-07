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
- The base is `docs/guides/poc-05-runbooks.md` (leaked-token rotation, admission refusal, stuck claims). This issue adds the rollback and escalation parts it lacks.
- First, the failures of components that exist, from [ADR-001](../adr/001-chassis-delivery-model.md) and the PoCs:
  - a router or provider outage, and a high fallback rate;
  - a bad chassis release: stop the rings, and pin the previous tag;
  - a pod refused by the minimum-version rule (admission rule 9 in `deploy/helm/agent-lib`, PR #79);
  - a compromised workload: revoke its key, tool grants, and network label, which cuts off that service alone. The ADR leaves this playbook out of scope, so it lives here;
  - a `remote` auth failure;
  - a bad config (rollback);
  - the urgent-fix order from ADR item 10: change the central limits first, then roll out one chassis image in fast rings.
- Failures of parts that are `later` or parked join when those parts ship: model server down or GPU node lost, broker lag and dead letters, stuck Temporal runs, registry degraded, Postgres failover on CloudNativePG, object storage errors, and evaluator drift.
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
- [ ] At least three runbooks (suggested: router outage, bad chassis release, bad config) are tried in a drill on staging, and the gaps found are fixed.
- [ ] In a drill, a person who did not write the runbook follows it without help.
- [ ] Runbooks also cover reading one trace end to end and a kill switch for one agent, and the kill switch is tried once on the local cluster (the vault's control 10) (gap review, 2026-11-07).

## Dependencies

- Depends on: [117 X-3](117-X-3-slos-alerts.md)
- Blocks: none

## References

- Epic story: [X-6 in Phase 8](../slm-agent-platform-epic-v3.md#phase-8-production-readiness)
- Epic context: [E.5](../slm-agent-platform-epic-v3.md#e5)
- Backlog plan: [000-plan.md](000-plan.md)
