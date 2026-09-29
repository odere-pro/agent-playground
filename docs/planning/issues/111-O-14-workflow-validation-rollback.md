---
title: "O-14: Workflow validation and dry run before activation, and rollback"
labels: ["story", "priority:P1", "phase:7-orchestrator", "area:orchestrator", "size:M", "layer:platform"]
milestone: "W9 Orchestrator"
index: 111
epic_id: O-14
depends_on: ["103 O-11"]
blocks: ["114 P-2b", "115 O-15"]
epic_refs: [C.2]
---

## Why

A bad workflow file must not reach live traffic. Validation and a dry run catch errors before a version becomes active, and rollback undoes a bad change in one step. Because workflows are config, this is the release process for orchestration. It comes after the engine, since a dry run needs it.

## What

- Checks beyond the schema: unique step `id`s, `input` references point to earlier steps, each `{ name }` agent exists and each `{ task }` resolves in the registry at or above `min_trust`, each `pool` exists, and each routing rule names an existing workflow.
- Dry run: run the workflow with fake agents or recorded responses (H-8 record and replay), with write tools off and no records written.
- Activation: a workflow version becomes active only after its checks and dry run pass (suggested: an active-version pointer per workflow in the config store).
- Rollback: point the active version back to the previous one in the config store, in one call, with no restart.
- The same activation and rollback for the orchestrator config.
- Each activation and rollback publishes `config.config.changed.v1`, so the audit log records it.
- An API on the orchestrator for these actions (suggested: `validate`, `dry-run`, `activate`, and `rollback` under `/v1/workflows`), which the MCP tools wrap later. `dry-run` returns an ID at once and reports its result through a status call, because the chassis timeout rules out long calls (050 CH-5).

## Out of scope

- The MCP tools `workflow_validate`, `workflow_dry_run`, `config_rollback`, and the rest (114 P-2b).
- Saving a planned run as a workflow (115 O-15).
- Schema checks on load (098 O-10).

## Acceptance criteria

- [ ] A workflow with an `input` reference to a later step, or an unknown agent, fails the checks with a list of errors and never becomes active.
- [ ] A dry run of `simplify-long-doc` completes with fake agents, calls no write tool, and writes no record.
- [ ] A version that failed its dry run cannot be activated.
- [ ] Rollback from 2.1.0 to 2.0.0 is one call. The next run uses 2.0.0, and a run already in progress stays on 2.1.0.
- [ ] Each activation and rollback shows as a `config.config.changed.v1` event with who and when.

## Dependencies

- Depends on: [103 O-11](103-O-11-workflow-engine.md)
- Blocks: [114 P-2b](114-P-2b-mcp-run-memory-config-tools.md), [115 O-15](115-O-15-save-planned-run.md)

## References

- Epic story: [O-14 in Phase 7](../slm-agent-platform-epic-v3.md#phase-7-orchestrator)
- Epic context: [C.2](../slm-agent-platform-epic-v3.md#c2)
- Backlog plan: [000-plan.md](000-plan.md)
