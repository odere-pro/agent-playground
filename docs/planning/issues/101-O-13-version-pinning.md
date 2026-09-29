---
title: "O-13: Version pinning per run"
labels: ["story", "priority:P1", "phase:7-orchestrator", "area:orchestrator", "size:S", "layer:platform"]
milestone: "W9 Orchestrator"
index: 101
epic_id: O-13
depends_on: ["100 O-2"]
blocks: []
epic_refs: [C.2]
---

## Why

A config change must never break a run in progress or change what a resumed run does. Pinning records the exact config and workflow versions at run start, so every step and every resume reads the same versions. It comes right after Temporal run state, because the pins live there, and before the workflow engine, so the engine reads workflow files only through pinned versions.

## What

- At run start, resolve and record the exact versions of the orchestrator config and of the workflow file (when the run uses one) from the versioned config store.
- Keep the pins in the Temporal run state, so they survive restarts and resumes.
- A small pinning helper: resolve a name to a version at start, then read by that version only. Later issues read config through this helper.
- Each step records the `versions` (config, prompt, model route) that the called agent reports in its response.
- The run status and the final run response show all pinned versions.
- Suggested: pins use the config store's object version IDs, plus `metadata.version` for people to read.

## Out of scope

- Running workflow files (103 O-11), which reads each file through the pinning helper.
- Rollback to a previous version (111 O-14).
- Version reporting inside each agent, which the `versions` field already covers (007 H-1).

## Acceptance criteria

- [ ] A run started with orchestrator config 1.0.0 keeps using 1.0.0 after 1.0.1 is written to the config store.
- [ ] After a pod kill and resume, the run still reads the pinned versions, not the latest ones.
- [ ] A new run started after the change uses 1.0.1.
- [ ] A test writes a new version of a workflow file during a run, and the helper still returns the pinned version for that run.
- [ ] The run status shows the pinned config and workflow versions, and each step shows the agent `versions` it got back.

## Dependencies

- Depends on: [100 O-2](100-O-2-temporal-checkpoints.md)
- Blocks: none

## References

- Epic story: [O-13 in Phase 7](../slm-agent-platform-epic-v3.md#phase-7-orchestrator)
- Epic context: [C.2](../slm-agent-platform-epic-v3.md#c2)
- Backlog plan: [000-plan.md](000-plan.md)
