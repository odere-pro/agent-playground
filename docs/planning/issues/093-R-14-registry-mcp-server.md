---
title: "R-14: Registry exposed as an MCP server"
labels: ["story", "priority:P1", "phase:5-registry", "area:registry", "size:S", "layer:platform"]
milestone: "W7 Registry and governance gate"
index: 93
epic_id: R-14
depends_on: ["051 H-13", "092 R-12"]
blocks: ["094 P-1"]
epic_refs: [F.4]
---

## Why

MCP clients, not only REST callers, need to use the registry, and it feeds the platform MCP server in Phase 6 (094 P-1). The MCP tools are generated from the registry's OpenAPI spec, so REST and MCP never drift apart. This is the last registry issue, so it also closes the Phase 5 done-when.

## What

- An MCP endpoint on the registry service, with tools generated from its OpenAPI spec (051 H-13): `registry_search`, `registry_get`, `registry_register`, `registry_approve`.
- Built with the MCP Python SDK. Suggested transport: Streamable HTTP.
- `registry_search` returns the main pick and fallbacks (092 R-12) as structured JSON, not free text.
- Only `active` entries are returned. Their descriptions were scanned on registration (081 R-8), and the client gets them as data (G.3).
- Read tools need the read scope. `registry_register` needs the write scope, and `registry_approve` the approve scope (022 H-6, 080 R-7).
- Tool annotations mark `registry_search` and `registry_get` as read-only.
- `registry_register` and `registry_approve` ship switched off by a config flag. 095 P-4 switches them on once confirmation and per-call audit exist.
- Registered in the registry like any other MCP server, so it can be found through itself.

## Reuse

- **Use:** FastMCP 4, generating tools from the registry's OpenAPI spec, the same way as 051 H-13.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- The platform MCP server with agent tools (094 P-1).
- Confirmation on destructive tools and the audit log on every MCP call (095 P-4).

## Acceptance criteria

- [ ] With the flag on, an MCP client lists all four tools, with schemas that match the registry's OpenAPI operations.
- [ ] `registry_search` over MCP and the REST search return the same results for the 090 R-13 queries (contract test).
- [ ] A change to a registry REST operation changes its MCP tool with no hand edits.
- [ ] By default, only the two read tools are listed; the write tools appear only when the flag is on.
- [ ] With the flag on in a test profile, `registry_register` without the write scope is refused; with it, a manual entry is created as `pending`.
- [ ] With the flag on, `registry_approve` without the approve scope is refused.
- [ ] Phase 5 done-when, shown in one recorded demo: an agent registers on deploy in Docker Compose and in Kubernetes, a manual entry waits for approval, a changed description goes back to review, and search meets the top-3 target of at least 90%.

## Dependencies

- Depends on: [051 H-13](051-H-13-openapi-mcp-tools.md), [092 R-12](092-R-12-main-pick-fallbacks.md)
- Blocks: [094 P-1](094-P-1-platform-mcp-server.md)

## References

- Epic story: [R-14 in Phase 5](../slm-agent-platform-epic-v3.md#phase-5-service-registry)
- Epic context: [F.4](../slm-agent-platform-epic-v3.md#f4)
- Backlog plan: [000-plan.md](000-plan.md)
