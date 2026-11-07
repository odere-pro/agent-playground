---
title: "P-1: Platform MCP server built from the harness, with registry and agent tools"
labels: ["story", "priority:P1", "phase:6-mcp-server", "area:mcp", "size:S", "layer:platform"]
milestone: "W8 Platform MCP server"
index: 94
epic_id: P-1
depends_on: ["051 H-13", "093 R-14"]
blocks: ["095 P-4", "097 P-3"]
epic_refs: [F.4]
---

## Why

One MCP server lets Claude, or any MCP client, work with the whole system. It is built with the harness and registered like any other agent. This base issue brings the registry and agent tools, which cover the first two parts of the Phase 6 done-when: search the registry and run an agent.

## What

- A new agent built on the chassis, from the service template (suggested: kind `tool`), with MCP tools generated from its OpenAPI spec (051 H-13).
- Registry tools (`registry_search`, `registry_get`, `registry_register`, `registry_approve`), forwarded to the registry MCP server (093 R-14), so their schemas stay the same.
- `agent_list`: active agents from the registry, with name, task, version, and description.
- `agent_run`: calls an active agent through `POST /v1/run`, complete only. The call goes out through the chassis's outbound agent call, which holds the credentials. The workload holds none.
- This issue builds that outbound agent call. It is a chassis feature, with a fake and a contract case. The agent port in 099 O-1 reuses it.
- MCP is complete only, generated from the OpenAPI spec. Decided in ADR-003 item 6. Streaming waits until FastMCP can stream an OpenAPI tool.
- `agent_run` sets an `idempotency_key` when the caller gives none, and passes on the trace context. No MCP case exists in the idempotency tests today; the replay box needs one (suggested: `test_an_mcp_call_replays_on_the_same_idempotency_key`).
- The chassis passes the caller's identity to the target agent on that outbound call, so that agent's own scopes apply (suggested: token exchange). This needs an ADR-008 note. ADR-008 item 2 says the token is never forwarded to the workload; this hop is chassis to agent, not chassis to workload.
- The write tools stay off by config until 095 P-4 adds scopes, confirmation, and audit.
- Registers itself on deploy with its governance block, like any agent.

## Reuse

- **Use:** FastMCP 4, and the LiteLLM MCP gateway to put agent MCP endpoints behind one URL.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Read and write scopes, confirmation, and the audit log on every call (095 P-4).
- Golden set and metrics tools (096 P-2a), and run, memory, config, and workflow tools (114 P-2b).
- Resources and prompts (097 P-3).
- A streamed `agent_run`, until FastMCP can stream an OpenAPI tool (ADR-003 item 6).

## Acceptance criteria

- [ ] An MCP client connects and lists `registry_search`, `registry_get`, `agent_list`, and `agent_run`; the write tools are not listed while they are off.
- [ ] `registry_search` for the echo agent's task returns the echo agent (`packages/workloads/echo-*`) as the main pick with its fallbacks.
- [ ] `agent_run` on the echo agent returns a complete result.
- [ ] `agent_run` on a `pending` or `inactive` entry is refused.
- [ ] Two `agent_run` calls with the same `idempotency_key` return the same result.
- [ ] One trace links the MCP call to the agent call.
- [ ] The server shows in the registry as an active agent with its governance block, and contract tests (015 H-8) cover each tool.

## Dependencies

- Depends on: [051 H-13](051-H-13-openapi-mcp-tools.md), [093 R-14](093-R-14-registry-mcp-server.md)
- Blocks: [095 P-4](095-P-4-mcp-scopes-audit.md), [097 P-3](097-P-3-mcp-resources-prompts.md)

## References

- Epic story: [P-1 in Phase 6](../slm-agent-platform-epic-v3.md#phase-6-platform-mcp-server)
- Epic context: [F.4](../slm-agent-platform-epic-v3.md#f4)
- Backlog plan: [000-plan.md](000-plan.md)
