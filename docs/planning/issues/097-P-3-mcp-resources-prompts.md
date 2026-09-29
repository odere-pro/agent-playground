---
title: "P-3: MCP resources and prompts"
labels: ["story", "priority:P2", "phase:6-mcp-server", "area:mcp", "size:S", "layer:platform"]
milestone: "W8 Platform MCP server"
index: 97
epic_id: P-3
depends_on: ["094 P-1"]
blocks: []
epic_refs: [F.5]
---

## Why

Tools let an MCP client act; resources and prompts help it understand the system. Resources give read-only access to manifests, dataset versions, and logs, and prompts walk a user through common tasks (F.5). This is P2 because the Phase 6 done-when does not need it.

## What

- Resource: agent manifests from the registry, the same as each agent's `/manifest` (suggested URI template: `registry://agents/{name}/{version}/manifest`).
- Resource: dataset version metadata from lakeFS (069 D-7): version ID, record count, splits, and date.
- Resource: call logs by `request_id` from Loki (014 H-7), with PII already redacted.
- Prompt: how to add a new agent, from `agentctl new <name> --kind ...` (059 H-15) to self-registration.
- Prompt: how to build a golden set, with `golden_import`, `review_list`, `review_decide`, and `golden_export`.
- Prompt: how to debug a failed run. For now a run is one agent call, followed from its `request_id` to logs, trace, and manifest.
- Prompts take arguments, for example the agent name and kind.
- Resources need the read scope and are audited like tool calls (095 P-4).

## Out of scope

- Orchestrator run logs by `run_id`, and the run tools (114 P-2b).
- Changes through resources. Changes go through tools, such as the golden set tools (096 P-2a).

## Acceptance criteria

- [ ] `resources/list` and the resource templates show all three resource kinds.
- [ ] Reading the echo agent's manifest resource returns the same content as its `/manifest`.
- [ ] Reading a dataset version resource returns its ID, record count, and splits, and no write is possible through resources.
- [ ] Reading a call log that holds a PII fixture returns redacted lines only.
- [ ] `prompts/list` returns the three prompts; each takes its arguments and names only tools and commands that exist.
- [ ] A resource read without the read scope is refused and audited.

## Dependencies

- Depends on: [094 P-1](094-P-1-platform-mcp-server.md)
- Blocks: none

## References

- Epic story: [P-3 in Phase 6](../slm-agent-platform-epic-v3.md#phase-6-platform-mcp-server)
- Epic context: [F.5](../slm-agent-platform-epic-v3.md#f5)
- Backlog plan: [000-plan.md](000-plan.md)
