---
title: "O-9: Short-term memory (execution state) and long-term memory (settings, gotchas, feedback)"
labels: ["story", "priority:P1", "phase:7-orchestrator", "area:orchestrator", "size:L", "layer:platform"]
milestone: "W9 Orchestrator"
index: 113
epic_id: O-9
depends_on: ["100 O-2"]
blocks: ["114 P-2b", "125 L-1"]
epic_refs: [F.8, F.9]
---

## Why

The orchestrator needs two kinds of memory. Short-term memory is the state of the current run, so it can resume; Temporal keeps it, and 100 O-2 builds it. Long-term memory holds settings and gotchas, so each run starts with what the team already knows. This issue delivers the last Phase 7 done-when part, "loads settings from long-term memory", so it also carries the end-to-end check for Phase 7.

## What

- Run state and the run status moved to 100 O-2 on 2026-11-06. This issue is long-term memory and the Phase 7 end-to-end test.
- Long-term memory in Postgres with pgvector, behind a new memory port with a fake and a contract suite: settings and preferences (for example the banned-word list and output style), and gotchas (known failures and their fixes).
- `memory.load` sets what is loaded at the start of each run. The loaded snapshot is kept with the run.
- Gotchas relevant to the task are found by vector search. The embedding model name is stored with each vector.
- Only the orchestrator's chassis holds the memory store credential. suggested: it exposes the store to its workload as MCP tools through the tool proxy (054 H-16). This is gap (a) in [the gaps in the backlog plan](000-plan.md#adr-001-follow-ups).
- Only the orchestrator writes to memory, through an API for `settings_get`, `settings_set`, and `gotcha_add` (suggested: under `/v1/memory`).

## Out of scope

- The MCP memory tools (114 P-2b).
- Session context and `context_ref` for agents (125 L-1).
- Feedback. It does not go in the memory store. Feedback goes through `POST /v1/feedback` to Langfuse or LangSmith scores (ADR-009), and the golden set reads it as a consumer of `agents.feedback.received.v1`, as ADR-009 item 6 says. `feedback_add` wraps `/v1/feedback`. Evaluator results are spans and counters (ADR-010 item 5), not stored feedback.

## Acceptance criteria

- [ ] A setting (the banned-word list) written before a run is loaded at run start and shows in the run state.
- [ ] A setting changed during a run affects the next run, not the running one.
- [ ] A gotcha added for the echo agent's task is loaded by the next run of that task.
- [ ] The orchestrator's workload container holds no memory store credential.
- [ ] An end-to-end test covers the Phase 7 done-when: a fan-out run from a workflow file, a workflow switch by config only, a compare job, a resume after a killed step with no repeated side effect, a stop on budget, and settings loaded from long-term memory.

## Dependencies

- Depends on: [100 O-2](100-O-2-temporal-checkpoints.md)
- Blocks: [114 P-2b](114-P-2b-mcp-run-memory-config-tools.md), [125 L-1](125-L-1-shared-memory-at-scale.md)

## References

- Epic story: [O-9 in Phase 7](../slm-agent-platform-epic-v3.md#phase-7-orchestrator)
- Epic context: [F.8](../slm-agent-platform-epic-v3.md#f8) · [F.9](../slm-agent-platform-epic-v3.md#f9)
- Backlog plan: [000-plan.md](000-plan.md)
