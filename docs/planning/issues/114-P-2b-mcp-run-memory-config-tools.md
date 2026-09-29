---
title: "P-2b: MCP run, memory, config, and workflow tools"
labels: ["story", "priority:P1", "phase:6-mcp-server", "area:mcp", "size:M", "layer:platform"]
milestone: "W9 Orchestrator"
index: 114
epic_id: P-2b
depends_on: ["095 P-4", "109 O-8", "111 O-14", "113 O-9"]
blocks: []
epic_refs: [F.4]
---

## Why

The platform MCP server must let any MCP client manage runs, not only search and run agents. These tools are split from P-2 because they need the orchestrator, which lands only now. This issue also covers the Config and Workflows tools listed in F.4, so a client like Claude can read, change, check, and roll back orchestration config under the same scopes and audit as every other tool.

## What

- Runs: `run_start`, `run_status`, `run_resume`, `run_cancel`, on the orchestrator run API.
- Memory: `settings_get`, `settings_set`, `gotcha_add`, `feedback_add`, on the memory API.
- Config: `config_get`, `config_set`, `config_validate`, `config_versions`, `config_rollback`, for files under `agents/` in the config store. `config_set` checks the file against its schema before it writes.
- Workflows: `workflow_list`, `workflow_get`, `workflow_set`, `workflow_validate`, `workflow_dry_run`, on the workflow validation API. `workflow_set` writes a new version but does not make it active.
- Tools generated from the orchestrator's OpenAPI spec where possible.
- Read and write scopes per tool, confirmation on destructive tools (`run_cancel`, `config_set`, `config_rollback`, `workflow_set`, `settings_set`), and an audit log entry on every call, as set up in P-4.

## Out of scope

- Registry and agent tools (094 P-1), golden set and metrics tools (096 P-2a).
- MCP resources and prompts (097 P-3).
- New orchestrator features; these tools only wrap existing APIs.

## Acceptance criteria

- [ ] An MCP client starts a run, reads its status, cancels it, and resumes a failed run, all through these tools.
- [ ] `config_set` with an invalid config is refused with the schema errors, and nothing is written.
- [ ] `config_rollback` makes the previous version active, and `config_versions` lists both versions.
- [ ] `workflow_set` stores a new version that stays inactive until it passes `workflow_validate` and `workflow_dry_run`.
- [ ] A client with only the read scope can call `run_status` and `config_get`, but not `run_cancel` or `config_set`.
- [ ] Destructive tools ask for confirmation, and every call shows in the audit log.

## Dependencies

- Depends on: [095 P-4](095-P-4-mcp-scopes-audit.md), [109 O-8](109-O-8-cancel-approval.md), [111 O-14](111-O-14-workflow-validation-rollback.md), [113 O-9](113-O-9-orchestrator-memory.md)
- Blocks: none

## References

- Epic story: [P-2b in Phase 6](../slm-agent-platform-epic-v3.md#phase-6-platform-mcp-server)
- Epic context: [F.4](../slm-agent-platform-epic-v3.md#f4)
- Backlog plan: [000-plan.md](000-plan.md)
