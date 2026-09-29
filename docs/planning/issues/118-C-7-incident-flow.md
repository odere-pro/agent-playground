---
title: "C-7: Incident flow: incident events, alert routing, runbook for serious incidents"
labels: ["story", "priority:P1", "phase:9-governance", "area:governance", "size:M", "layer:platform"]
milestone: "W10 Production readiness"
index: 118
epic_id: C-7
depends_on: ["072 C-3", "117 X-3"]
blocks: []
epic_refs: [D.3, E.5]
---

## Why

A serious incident with an AI system brings legal duties under AI Act Art. 73, and it needs a clear path from detection to report. The incident flow turns alerts and agent reports into incident events, routes them to on-call and compliance, and keeps a record. It comes right after the SLO alerts, which are its main source.

## What

- `incidents.incident.detected.v1` events, produced by any agent and by monitoring.
- An agent raises an incident with no SDK. The workload emits an `incident` event in the chassis JSON event schema over A2A, and the chassis publishes `incidents.incident.detected.v1` (suggested fields: severity, agent, agent version, `run_id`, `trace_id`, summary).
- Monitoring alerts become incident events (suggested: an Alertmanager webhook into a small event producer).
- Routing: on-call gets every incident; the compliance team also gets those marked as a possible serious incident.
- The audit log stores every incident event as an incident record.
- A serious-incident runbook: triage, severity, who decides whether it is a serious incident (legal), how to collect evidence (audit log export for the time range, event replay), and the reporting deadline, confirmed with legal.

## Out of scope

- Runbooks for common failures (119 X-6).
- The legal decision on whether an incident is serious; legal makes it, and the platform records it.
- SLO rules (117 X-3).

## Acceptance criteria

- [ ] An SLO alert on staging produces an `incidents.incident.detected.v1` event.
- [ ] An agent raises an incident by emitting an `incident` event, and the published event carries its `trace_id` and agent version. The test runs over A2A on localhost and in memory.
- [ ] A possible serious incident reaches both on-call and the compliance team.
- [ ] Every incident shows in the audit log export for its time range.
- [ ] The compliance owner reviews the serious-incident runbook, and it is tried once in a tabletop drill.

## Dependencies

- Depends on: [072 C-3](072-C-3-audit-log-agent.md), [117 X-3](117-X-3-slos-alerts.md)
- Blocks: none

## References

- Epic story: [C-7 in Phase 9](../slm-agent-platform-epic-v3.md#phase-9-governance-and-compliance)
- Epic context: [D.3](../slm-agent-platform-epic-v3.md#d3) · [E.5](../slm-agent-platform-epic-v3.md#e5)
- Backlog plan: [000-plan.md](000-plan.md)
