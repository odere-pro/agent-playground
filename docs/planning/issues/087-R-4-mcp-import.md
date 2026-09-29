---
title: "R-4: MCP import: call tools/list and register each tool"
labels: ["story", "priority:P1", "phase:5-registry", "area:registry", "size:S", "layer:platform"]
milestone: "W7 Registry and governance gate"
index: 87
epic_id: R-4
depends_on: ["081 R-8", "082 R-9"]
blocks: []
epic_refs: [F.3, R5]
---

## Why

Many tools come as MCP servers, and one server can hold many tools. MCP import registers every tool from a server's `tools/list` in one step. MCP tool descriptions are the main path for tool poisoning [R5], so this import comes after scanning (081 R-8) and hash pinning (082 R-9) and uses both.

## What

- Import by MCP server URL, with a Vault reference for its credentials, using the MCP Python SDK.
- Calls `initialize`, then `tools/list`, following the page cursor to the end.
- Registers the server as an `mcp` entry, and each tool as a `tool` entry linked to its server, with `inputSchema` and `outputSchema` as its input and output schema.
- Stores tool annotations (`readOnlyHint`, `destructiveHint`) as hints only. The tool's read or write mode still comes from the agent config (B.3).
- External servers get `trust: external` and wait for approval (080 R-7).
- Every tool description goes through the scanner (081 R-8).
- Import again on demand and on a schedule (suggested: daily, and on `notifications/tools/list_changed`). New tools are added, removed tools become `inactive`, and changed descriptions go back to review (082 R-9).

## Reuse

- **Use:** the MCP Python SDK client for `tools/list`.
- **Build:** the mapping from tools to registry entries.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- OpenAPI import (088 R-5).
- Calling the tools from agents (054 H-16).
- Exposing the registry itself as an MCP server (093 R-14).

## Acceptance criteria

- [ ] Importing a test MCP server with 5 tools creates 1 server entry and 5 tool entries, all `external` and `pending`, with schemas that match `tools/list`.
- [ ] A server with more tools than one page imports all of them.
- [ ] A tool with a poisoned description is flagged, and its findings show in the approval queue.
- [ ] After one tool's description changes on the server, a new import sends only that tool back to review.
- [ ] A tool removed from the server becomes `inactive`, and its row is kept.
- [ ] No credential is stored in an entry, only the Vault reference.

## Dependencies

- Depends on: [081 R-8](081-R-8-description-scanning.md), [082 R-9](082-R-9-description-hash-pinning.md)
- Blocks: none

## References

- Epic story: [R-4 in Phase 5](../slm-agent-platform-epic-v3.md#phase-5-service-registry)
- Epic context: [F.3](../slm-agent-platform-epic-v3.md#f3) · [R5](../slm-agent-platform-epic-v3.md#r5)
- Backlog plan: [000-plan.md](000-plan.md)
