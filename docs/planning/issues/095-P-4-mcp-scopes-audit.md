---
title: "P-4: MCP read and write scopes, confirmation on destructive tools, audit log on every call"
labels: ["story", "priority:P1", "phase:6-mcp-server", "area:mcp", "size:M", "layer:platform"]
milestone: "W8 Platform MCP server"
index: 95
epic_id: P-4
depends_on: ["072 C-3", "094 P-1"]
blocks: ["096 P-2a", "114 P-2b"]
epic_refs: [G.3]
---

## Why

The MCP server gives an AI client access to the whole platform, so its tools need the same guards as the APIs (G.3). This issue is placed right after 094 P-1, so no write tools are exposed before scopes and audit exist. It uses the audit log agent (072 C-3) to keep a record of every call.

## What

- Every tool is tagged read or write, and also destructive when it changes something that is hard to undo. The MCP tool annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`) are set from these tags.
- OAuth scopes for callers (suggested: `mcp:read`, `mcp:write`), checked by the security middleware (022 H-6).
- A caller only sees the tools its scopes allow, and a call without the right scope is refused.
- Destructive tools need the user's confirmation before they act. Suggested: MCP elicitation, with a two-step confirm token for clients that do not support it.
- Suggested: the workload's A2A `input-required` state maps to MCP elicitation in the chassis.
- Every call, allowed or refused, sends an audit event (suggested: `mcp.tool.called.v1`) with caller, tool, redacted arguments, result status, and `trace_id`, stored by the audit log agent (072 C-3).
- PII is removed from arguments before the event is sent.
- Switches on `registry_register` and `registry_approve` from 094 P-1; `registry_approve` needs confirmation.
- A CI check fails when a tool has no read or write tag.

## Reuse

- **Use:** LiteLLM MCP gateway per-key tool allow-lists and OAuth, and the MCP `destructiveHint` tool annotation for confirmations. Option: agentgateway (per-tool CEL rules; v1.0 in March 2026, so newer).
- **Build:** the confirmation flow and the audit events.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Golden set and metrics tools (096 P-2a), and run, memory, config, and workflow tools (114 P-2b), which reuse these guards.
- Human oversight controls in the orchestrator (110 C-6).

## Acceptance criteria

- [ ] A token with only the read scope sees only read tools, and a write tool call made with it is refused and audited.
- [ ] A destructive tool does nothing until the user confirms, and declining leaves no change.
- [ ] N tool calls produce N audit records, each with caller, tool, status, and `trace_id`.
- [ ] A PII fixture in tool arguments is redacted in the audit record.
- [ ] `registry_register` and `registry_approve` work end to end with the right scopes.
- [ ] CI fails when a new tool has no read or write tag.

## Dependencies

- Depends on: [072 C-3](072-C-3-audit-log-agent.md), [094 P-1](094-P-1-platform-mcp-server.md)
- Blocks: [096 P-2a](096-P-2a-mcp-golden-set-metrics-tools.md), [114 P-2b](114-P-2b-mcp-run-memory-config-tools.md)

## References

- Epic story: [P-4 in Phase 6](../slm-agent-platform-epic-v3.md#phase-6-platform-mcp-server)
- Epic context: [G.3](../slm-agent-platform-epic-v3.md#g3)
- Backlog plan: [000-plan.md](000-plan.md)
