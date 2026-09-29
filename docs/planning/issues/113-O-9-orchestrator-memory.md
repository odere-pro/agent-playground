---
title: "O-9: Short-term memory (execution state) and long-term memory (settings, gotchas, feedback)"
labels: ["story", "priority:P1", "phase:7-orchestrator", "area:orchestrator", "size:L", "layer:platform"]
milestone: "W9 Orchestrator"
index: 113
epic_id: O-9
depends_on: ["100 O-2", "068 D-3"]
blocks: ["114 P-2b", "125 L-1"]
epic_refs: [F.8, F.9]
---

## Why

The orchestrator needs two kinds of memory. Short-term memory is the state of the current run, so it can resume. Long-term memory holds settings, gotchas, and feedback, so each run starts with what the team already knows. This issue delivers the last Phase 7 done-when part, "loads settings from long-term memory", so it also carries the end-to-end check for Phase 7.

## What

- Execution state per run, kept by Temporal: `run_id`, plan, steps, step status, and references to inputs and outputs, not the full text (suggested: object storage references).
- Run state is readable through a Temporal query and the run status API.
- Long-term memory in Postgres with pgvector, behind the memory port: settings and preferences (for example the banned-word list and output style), gotchas (known failures and their fixes), and feedback from users and evaluators per run.
- `memory.load` sets what is loaded at the start of each run. The loaded snapshot is kept with the run.
- Gotchas relevant to the task are found by vector search. The embedding model name is stored with each vector.
- Only the orchestrator's chassis holds the memory store credential. suggested: it exposes the store to its workload as MCP tools through the tool proxy (054 H-16). This is gap (a) in [the gaps in the backlog plan](000-plan.md#adr-001-follow-ups).
- Only the orchestrator writes to memory, through an API for `settings_get`, `settings_set`, `gotcha_add`, and `feedback_add` (suggested: under `/v1/memory`).
- Feedback flows into the golden set through its import API (068 D-3).

## Out of scope

- The MCP memory tools (114 P-2b).
- Session context and `context_ref` for agents (125 L-1).

## Acceptance criteria

- [ ] The run status shows plan, steps, step status, and input and output references, for a running and a finished run.
- [ ] A setting (the banned-word list) written before a run is loaded at run start and shows in the run state.
- [ ] A setting changed during a run affects the next run, not the running one.
- [ ] A gotcha added for `simplify_text` is loaded by the next `simplify_text` run.
- [ ] Feedback added for a run shows in the golden set with its `run_id`.
- [ ] The orchestrator's workload container holds no memory store credential.
- [ ] An end-to-end test covers the Phase 7 done-when: a fan-out simplification from a workflow file, a workflow switch by config only, a compare job, a resume after a killed step with no repeated side effect, a stop on budget, and settings loaded from long-term memory.

## Dependencies

- Depends on: [100 O-2](100-O-2-temporal-checkpoints.md), [068 D-3](068-D-3-import.md)
- Blocks: [114 P-2b](114-P-2b-mcp-run-memory-config-tools.md), [125 L-1](125-L-1-shared-memory-at-scale.md)

## References

- Epic story: [O-9 in Phase 7](../slm-agent-platform-epic-v3.md#phase-7-orchestrator)
- Epic context: [F.8](../slm-agent-platform-epic-v3.md#f8) · [F.9](../slm-agent-platform-epic-v3.md#f9)
- Backlog plan: [000-plan.md](000-plan.md)
