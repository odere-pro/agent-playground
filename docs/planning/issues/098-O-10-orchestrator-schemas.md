---
title: "O-10: Orchestrator config and workflow schemas (JSON Schema) in the config store"
labels: ["story", "priority:P1", "phase:7-orchestrator", "area:orchestrator", "size:M", "layer:platform"]
milestone: "W9 Orchestrator"
index: 98
epic_id: O-10
depends_on: ["010 H-12"]
blocks: ["099 O-1", "103 O-11"]
epic_refs: [C.2]
---

## Why

Orchestration is config, not code: routing, workflows, limits, and approvals all live in the config store. So the schemas come first in the orchestrator wave, before the engine that reads them. One fixed shape lets the engine, the planner, validation, and the MCP tools agree from the start.

## What

- `schemas/orchestrator-config.schema.json` and `schemas/workflow.schema.json` in the config store, for `kind: OrchestratorConfig` and `kind: Workflow` under `apiVersion: agents/v1`, with `metadata.name` and `metadata.version` required.
- The orchestrator config schema reuses the shared agent config blocks by `$ref` (`interface`, `security`, `governance`, and the rest), so every class still has one config schema.
- It adds `planner` (`mode`: `fixed | planned | hybrid`, `model_route`), `routing` (`match`, `workflow`, `default`), `registry` (`top_k`, `min_trust`), `limits` (`max_steps`, `max_tokens`, `max_cost_usd`, `timeout_s`), `memory` (`short_term`, `long_term`, `load`), and `approvals` (`require_for`).
- The workflow schema covers `pattern` (`chain | fan_out | compare`), `split`, `steps` (`id`, `agent` as `{ task }` or `{ name }`, `parallel`, `pool`, `input`), `evaluate`, `on_failure` (`retries`, `then`: `fallback_route | skip | stop`), and `budget`. Suggested: a `repeat` count per step for `compare`.
- Unknown fields are rejected, so a typo fails at load, not at run time.
- The config loader (H-12) picks the schema by `kind`.
- Example files: `agents/orchestrator.yaml`, `workflows/simplify-long-doc.yaml`, `workflows/compare-simplifiers.yaml`.
- A CI check that validates every file under `agents/` and `workflows/`.

## Out of scope

- Running workflows from these files (103 O-11).
- Checks beyond the schema (does this agent exist, does this rule name a real workflow), dry run, and rollback (111 O-14).
- The compare tie rule field, which extends the workflow schema (106 O-5).

## Acceptance criteria

- [ ] Both schemas are in the config store under `schemas/`, versioned like any other file.
- [ ] The orchestrator config and `simplify-long-doc` examples from C.2 validate as written.
- [ ] Invalid files are rejected with the path of the bad field: an unknown `planner.mode`, an unknown `pattern`, a step without `id`, an unknown `on_failure.then`, a negative limit, and an unknown field.
- [ ] A step `agent` must have exactly one of `task` or `name`.
- [ ] The loader validates an `OrchestratorConfig` and a `Workflow` by `kind`, and a changed file is reloaded without a restart.
- [ ] CI fails when any config file does not match its schema.

## Dependencies

- Depends on: [010 H-12](010-H-12-config-loader.md)
- Blocks: [099 O-1](099-O-1-orchestrator-agent.md), [103 O-11](103-O-11-workflow-engine.md)

## References

- Epic story: [O-10 in Phase 7](../slm-agent-platform-epic-v3.md#phase-7-orchestrator)
- Epic context: [C.2](../slm-agent-platform-epic-v3.md#c2)
- Backlog plan: [000-plan.md](000-plan.md)
