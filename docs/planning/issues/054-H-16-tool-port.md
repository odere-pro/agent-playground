---
title: "H-16: Tool port: native tool calls or JSON via guided decoding; read and write modes"
labels: ["story", "priority:P1", "phase:0-harness", "area:harness", "size:M", "layer:agent-profile"]
milestone: "W5 Chassis completion"
index: 54
epic_id: H-16
depends_on: ["012 H-3", "018 H-18", "013 CH-2"]
blocks: ["063 S-8"]
epic_refs: [B.3, R2]
---

## Why

Tool agents answer by calling tools. Small models call tools less reliably than big ones, so the chassis must support native tool calls where a route has them and forced JSON where it does not. Under [ADR-001](../adr/001-chassis-delivery-model.md), the workload holds no key, so it reaches tools only through the chassis's tool proxy. The hard limit is the MCP gateway's allow-list for the service's key. Write tools must never repeat a side effect, which is why this comes after idempotency (018 H-18).

## What

- A `ToolPort` outbound port in the chassis, switched on for tool agents (kind `tool`).
- The outbound MCP tool proxy. The workload's MCP client points at the chassis, and the chassis calls the MCP gateway with the service's scoped key. The workload has no key, so it cannot call the gateway on its own.
- Tools allow-listed per key in the MCP gateway. That allow-list is the hard limit. The chassis config allow-list (`tools` with `name` and `mode`) is a second check. Tools are read-only by default. A tool with side effects needs `mode: write`.
- Native tool calls when the model route declares native tool support in the router. Otherwise, JSON forced to the tool's schema by guided decoding. In the `sidecar` lane, the framework owns the model loop (gap (e) in the [backlog plan](000-plan.md#adr-001-follow-ups)). suggested: the model proxy (013 CH-2) passes the guided-decoding parameters through, and the JSON fallback applies to workloads that ask for it.
- The tool proxy adds the call's `idempotency_key` to write-tool calls, so a retry never repeats the action.
- The tool proxy traces every tool call, and each call shows as a `tool_call` event.
- Tool descriptions and tool outputs are treated as data, never as instructions.
- A failed or malformed tool call is retried, then goes to `fallback_route` (017 H-4).
- The code-execution tool behind `ToolPort` (suggested: agent-sandbox or E2B). Generated code that runs through it does not make an agent untrusted (ADR-001 item 5).
- Suggested: first tool adapters for MCP tools (MCP Python SDK) and plain HTTP APIs, with endpoints set in config.
- Status after PoC-2: `ToolPort` (`chassis.ports.tool`), the fake `InMemoryTools` with one read-only tool, `glossary_lookup`, defined once (`chassis.fakes.tool`), `ToolPortContract`, and the MCP endpoint `/mcp` on the chassis's localhost-only proxy listener (FastMCP 4, stateless). Every Python workload gets the tool from it, and no workload defines its own (`pocs/poc-02-two-engines-one-contract/tests/test_tools.py`). Each call is one `chassis.tool.call` span with the parsed trace id and, for an in-flight run, its `request_id`. Open: the real adapter (`tools: mcp`, named for PoC-5 in `profiles.REGISTRY`), the MCP gateway behind it, and charging tool calls to the run: the endpoint names the run but does not charge it or count uncorrelated tool calls. See [the PoC-2 debt note](../../../pocs/poc-02-two-engines-one-contract/notes/2026-10-01-debt.md).
- Status after PoC-5: `ToolPort` write mode, the idempotency keyword, and the error codes `unknown_tool`, `tool_denied`, `tool_unavailable` ([contract v4](../../contracts/contract-v4.md)). `McpGatewayTools` passes the same `ToolPortContract` as the fake (`packages/chassis/tests/test_tool_gateway_contract.py`); tool keys are scoped to agent and request. On kind the gateway refuses an unlisted tool as `tool_denied` (`test_poc05_kind_tool_gateway.py`). Findings: the LiteLLM gateway names tools `<server>-<tool>` and does not forward `_meta`. The adapter adds the idempotency key as a tool argument only when the tool's schema declares it, so a write tool that does not declare it, or one called by its bare name, never gets the key (`idempotency_key_required`); a third-party tool server may also ignore the key. On `/mcp` outside a run, a name the port does not list answers `idempotency_key_required` (`adapters/mcp/server.py`). suggested: `unknown_tool` when no run is in flight, and resolve a bare name to the one matching `<server>-<name>` entry, refusing when more than one matches. The code runner's result cache is in memory, in its dispatcher. B12 is closed for the code runner: each call runs in its own sandbox (`test_poc05_kind_code_runner.py::test_files_do_not_cross_calls`). It stays open everywhere else (PoC-8).
- Status after PoC-6: a second read-only tool, `acronym_expand`, in the fake MCP server and the fake tools. Findings: the gateway names tools `<server>-<tool>`, and the OpenAI Agents SDK refuses a call to a name it was not offered, so the kind script uses the gateway's name; the Claude CLI offers MCP tools as `mcp__chassis__<name>`; in plain-A2A mode (`kagent-adk`) tool calls are invisible to the chassis ([engines on kind](../../../pocs/poc-06b-bake-off-remote-lane/notes/2026-10-09-lanes-b-kind.md), decisions 3 and 4). Why: tool naming is this issue's.

## Reuse

- **Use:** vLLM tool parsers and guided decoding (already in the stack), and the LiteLLM MCP gateway (`/mcp`) for per-key tool allow-lists. The gateway's allow-list is the hard limit; the chassis config allow-list is a second check.
- **Build:** the tool port, the MCP tool proxy that workloads point their MCP client at, the JSON fallback mode, and passing `idempotency_key` to write tools.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Finding tools through the registry (076 R-1, 092 R-12).
- Fine-tuning an SLM for tool calls (063 S-8).
- Tool calls from the orchestrator (099 O-1).
- Workloads that run code themselves. They fail the trust rule and run in the `remote` lane (055 CH-6).

## Acceptance criteria

- [ ] A test tool agent gets the same result from an allow-listed read tool on a route with native tool calls and on a route that uses guided JSON.
- [ ] A call to a tool that is not allow-listed is refused and logged, and no request reaches the tool.
- [ ] A tool that is not on the key's allow-list is refused by the gateway, even with the chassis config check switched off. A direct call from the workload to the gateway fails, because the workload has no key.
- [ ] A tool with side effects is refused unless its config says `mode: write`.
- [ ] Retrying a request that used a fake write tool performs the side effect once, because the tool proxy passes the same `idempotency_key`.
- [x] Every tool call through the proxy shows in the trace and as a `tool_call` event. Delivered in PoC-2 with the fake tools: `packages/chassis/tests/test_tool_endpoint.py::test_one_span_per_call_with_the_tool_and_the_inbound_trace_id`; `pocs/poc-02-two-engines-one-contract/tests/test_tools.py::test_the_tool_works_on_each_engine[*]`; the `tool_call` event in each Python engine's stream in [the demo](../../../pocs/poc-02-two-engines-one-contract/demo/2026-10-01-demo-sidecar.md).
- [ ] Malformed tool-call output is retried, then answered by the fallback route.
- [ ] In a prompt injection test, a tool output with injected instructions adds no tool calls and does not change the allow-list.
- [ ] Tool calls count toward the call's budget (suggested: a `max_tool_calls` limit).
- [ ] Code sent to the code-execution tool runs in its sandbox, not in the workload or chassis container.
- [ ] The MCP adapter and the fake tools pass the same `ToolPort` contract suite, and are picked by `spec.adapters.tools`. The tool proxy passes the suite in the `inprocess` and `sidecar` lanes.

## Dependencies

- Depends on: [012 H-3](012-H-3-model-port.md), [018 H-18](018-H-18-idempotency.md), [013 CH-2](013-CH-2-outbound-model-proxy.md)
- Blocks: [063 S-8](063-S-8-tool-call-finetune.md)

## References

- Epic story: [H-16 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [B.3](../slm-agent-platform-epic-v3.md#b3) · [R2](../slm-agent-platform-epic-v3.md#r2)
- Backlog plan: [000-plan.md](000-plan.md)
