---
title: "L-1: Shared memory at scale: session context, long-term memory, context_ref"
labels: ["later", "priority:P3", "phase:10-shared-memory", "area:memory", "size:M", "layer:platform"]
milestone: "W12 Later"
index: 125
epic_id: L-1
depends_on: ["113 O-9"]
blocks: []
epic_refs: [phase-10-shared-memory-at-scale, F.9, G.1]
---

## Why

Phase 10 is "plan now, build when scaling". Today each orchestrator run loads long-term memory at start, and agents get no shared context. At scale, agents need a shared way to fetch session context and memory while staying stateless. This P3 issue writes that plan and the entry criteria, so the `context_ref` slot in the envelope is ready to use without an API change. It extends orchestrator memory (113 O-9).

## What

- The note starts from the phase C checkpointer ADR, [ADR-013](../adr/013-langgraph-state-between-runs.md) (Proposed 2026-11-09): no state between runs by default; when needed, behind the chassis's `StatePort` through chassis tools, keyed by `session.id`. Layer 1 is that ADR's choice until the orchestrator exists.
- A design note for the four memory layers: execution state (Temporal once the orchestrator exists), session context (recent turns, in simplified form) in Valkey, long-term memory in Postgres with pgvector, and golden sets in object storage.
- `context_ref` already exists in the envelope (`packages/chassis/src/chassis/core/envelope.py`), accepted and passed through, unused. So the `context_ref` box is met by the design note only.
- The `context_ref` format, and how an agent fetches only what it needs through it (suggested: a URI per layer and key). The chassis resolves `context_ref` and passes the content in `ctx`. The workload never reads the stores.
- The rules kept: agents stay stateless, and the orchestrator owns writes to memory.
- Access control per layer, PII and retention rules (in line with 071 D-8), and cache expiry.
- How session context reuses the simplified answers from the history swap (044 G-4).
- Sizing and cost at, for example, ten times today's run volume.
- Entry criteria for starting the build, for example: several use cases need shared session context, or loading memory at run start misses its latency budget.

## Out of scope

- Building any of it; this issue is a plan.
- Changes to the envelope shape; `context_ref` already exists (007 H-1).

## Acceptance criteria

- [ ] A design note (ADR) covers all four layers, `context_ref`, access control, PII, retention, and sizing.
- [ ] The design keeps agents stateless and passes a review against the B.2 properties.
- [ ] The `context_ref` format needs no change to the envelope or the OpenAPI spec.
- [ ] Entry criteria are written as measurable triggers, with an owner who checks them.
- [ ] The plan is split into sized build issues, ready to add to the backlog when a trigger fires.

## Dependencies

- Depends on: [113 O-9](113-O-9-orchestrator-memory.md)
- Blocks: none

## References

- Epic story: [Phase 10](../slm-agent-platform-epic-v3.md#phase-10-shared-memory-at-scale)
- Epic context: [Phase 10](../slm-agent-platform-epic-v3.md#phase-10-shared-memory-at-scale) · [F.9](../slm-agent-platform-epic-v3.md#f9) · [G.1](../slm-agent-platform-epic-v3.md#g1)
- Backlog plan: [000-plan.md](000-plan.md)
