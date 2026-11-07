---
title: "O-16: Event triggers: workflows started by events, run and step events at each stage"
labels: ["story", "priority:P1", "phase:7-orchestrator", "area:orchestrator", "size:M", "layer:platform"]
milestone: "W9 Orchestrator"
index: 112
epic_id: O-16
depends_on: ["021 H-20", "100 O-2", "108 O-12", "103 O-11"]
blocks: []
epic_refs: [D.1, D.3]
---

## Why

The orchestrator is part of a bigger event-driven system, like every agent. Other systems must be able to start a workflow with an event, and the recorder, the audit log, and callers must see each run and step as events. This covers the epic criterion that every agent can be started by an API call or a CloudEvents event and publishes its results as events.

## What

- The event consumer adapter on the orchestrator: an `agents.task.requested.v1` event starts a run through the same routing as REST.
- The Temporal workflow ID comes from the event `idempotencykey` (or `id`), so a duplicate event starts no second run.
- Run events at each stage: `runs.run.started.v1`, `runs.step.completed.v1` per step, and `runs.run.completed.v1` with the final status (suggested: `completed`, `failed`, `cancelled`, or `stopped_budget`).
- Events are published by the chassis, from activities and never from workflow code (gap (b) in [the gaps in the backlog plan](000-plan.md#adr-001-follow-ups)), with `partitionkey` set from `run_id` (the hashed run key, as the event port sets it today), so their order per run is kept.
- CloudEvents extensions on every event: `traceparent`, `idempotencykey`, `configversion`, `modelroute`. `CloudEvent` is `extra="forbid"`, so a new extension breaks older consumers. PoC-8 Q3 decides it, on the owner list.
- Long tasks reply by event: `runs.run.completed.v1` carries the request id in the CloudEvent `subject`.
- The recorder and the audit log subscribe to the `runs.*` events.

## Out of scope

- The `oversight.*` events (110 C-6).
- Dead-letter topics and backoff (053 H-22), which the orchestrator reuses as is.
- The orchestrator's AsyncAPI spec of what it consumes and produces, which comes with 052 H-21.

## Acceptance criteria

- [ ] An `agents.task.requested.v1` event with the echo agent's task starts a run of the routed workflow.
- [ ] The same event delivered twice starts one run.
- [ ] A three-step run publishes one started, three step completed, and one completed event, in order on the run's partition.
- [ ] After a resume, consumers see no duplicate step event, or drop it by event `id`.
- [ ] Every event carries the four CloudEvents extensions, and `traceparent` links it to the run trace.
- [ ] The recorder stores the run events, and the audit log export shows them.

## Dependencies

- Depends on: [021 H-20](021-H-20-event-consumer-adapter.md), [100 O-2](100-O-2-temporal-checkpoints.md), [108 O-12](108-O-12-routing-planner-modes.md), [103 O-11](103-O-11-workflow-engine.md)
- Blocks: none

## References

- Epic story: [O-16 in Phase 7](../slm-agent-platform-epic-v3.md#phase-7-orchestrator)
- Epic context: [D.1](../slm-agent-platform-epic-v3.md#d1) · [D.3](../slm-agent-platform-epic-v3.md#d3)
- Backlog plan: [000-plan.md](000-plan.md)
