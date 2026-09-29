---
title: "O-7: Idempotent steps, so a resume never repeats a side effect"
labels: ["story", "priority:P1", "phase:7-orchestrator", "area:orchestrator", "size:M", "layer:platform"]
milestone: "W9 Orchestrator"
index: 102
epic_id: O-7
depends_on: ["018 H-18", "100 O-2"]
blocks: []
epic_refs: [B.2, D.1]
---

## Why

A resumed or retried step must never repeat a side effect, such as sending a message twice or writing a record twice. Temporal retries activities and resumes runs, so the same step can be called more than once. This issue gives every step a stable `idempotency_key` and relies on the agent-side check from H-18. It delivers the Phase 7 done-when part "resumes a run after a killed step without repeating side effects".

## What

- A deterministic `idempotency_key` per step, built from `run_id`, step `id`, and an item index for parallel steps (suggested: a UUIDv5 of those values).
- The same key on every activity retry and every resume of that step. A new run gets new keys.
- The key goes to the agent in the native envelope, so the agent's idempotency check returns the stored result instead of running again.
- Agents pass the key on to write tools through the chassis tool proxy (054 H-16).
- Completed steps are never called again; their results come from the checkpoint.
- Suggested: the Valkey result cache TTL covers the longest run (`limits.timeout_s`) plus a resume window, so a late resume still hits the cache.
- The step key shows in traces and logs of both the orchestrator and the agent.

## Out of scope

- The agent-side key check and result cache (018 H-18).
- Duplicate events on the broker (053 H-22), and run and step events (112 O-16).
- Parallel steps themselves (103 O-11), which use the item index from here.

## Acceptance criteria

- [ ] Kill the orchestrator pod after a step's write tool ran but before the checkpoint. After resume, the fake write tool from the testing kit counts one call, not two.
- [ ] A forced activity retry reuses the same `idempotency_key`, and the agent returns the same result.
- [ ] Two runs with the same input get different step keys.
- [ ] The key function gives distinct keys for distinct item indexes and the same key for the same inputs (unit tests).
- [ ] The step key is visible in the traces of both the orchestrator and the called agent.

## Dependencies

- Depends on: [018 H-18](018-H-18-idempotency.md), [100 O-2](100-O-2-temporal-checkpoints.md)
- Blocks: none

## References

- Epic story: [O-7 in Phase 7](../slm-agent-platform-epic-v3.md#phase-7-orchestrator)
- Epic context: [B.2](../slm-agent-platform-epic-v3.md#b2) · [D.1](../slm-agent-platform-epic-v3.md#d1)
- Backlog plan: [000-plan.md](000-plan.md)
