---
title: "H-16: Tool port: native tool calls or JSON via guided decoding; read and write modes"
labels: ["story", "priority:P1", "phase:0-harness", "area:harness", "size:L", "layer:agent-profile"]
milestone: "W5 Chassis completion"
index: 54
epic_id: H-16
depends_on: ["018 H-18", "017 H-4", "022 H-6"]
blocks: ["063 S-8", "140 CH-20"]
epic_refs: [B.3, R2]
---

## Why

Tool agents answer by calling tools. Small models call tools less reliably than big ones, so the chassis must support native tool calls where a route has them and forced JSON where it does not. Under [ADR-001](../adr/001-chassis-delivery-model.md), the workload holds no key, so it reaches tools only through the chassis's tool proxy. The hard limit is the MCP gateway's allow-list for the service's key. Write tools must never repeat a side effect, which is why this comes after idempotency (018 H-18).

## What

- A `ToolPort` outbound port in the chassis, switched on for tool agents (kind `tool`). Built in PoC-2: `ToolPort` (`chassis.ports.tool`), the fake `InMemoryTools` (`chassis.fakes.tool`), and `ToolPortContract`.
- The outbound MCP tool proxy. The workload's MCP client points at the chassis, and the chassis calls the MCP gateway with the service's scoped key. The workload has no key, so it cannot call the gateway on its own. Built in PoC-2 and PoC-5: the MCP endpoint `/mcp` on the chassis's localhost-only proxy listener (FastMCP 4, stateless), and `McpGatewayTools` behind it. Tool keys are scoped to agent and request. Every Python workload gets its tools from `/mcp`, and no workload defines its own.
- Tools allow-listed per key in the MCP gateway. That allow-list is the hard limit. The chassis config allow-list (`tools` with `name` and `mode`) is a second check. Tools are read-only by default. A tool with side effects needs `mode: write`.
- The fake tools are two read-only tools, `glossary_lookup` and `acronym_expand`, in the fake MCP server and in `InMemoryTools`.
- Tool names: the LiteLLM gateway names tools `<server>-<tool>`, and does not forward `_meta`. The OpenAI Agents SDK refuses a name it was not offered, and the Claude CLI offers MCP tools as `mcp__chassis__<name>` ([engines on kind](../../../pocs/poc-06b-bake-off-remote-lane/notes/2026-10-09-lanes-b-kind.md), decisions 3 and 4).
- In the `sidecar` lane, the framework owns the model loop (gap (e) in the [backlog plan](000-plan.md#adr-001-follow-ups)).
- The tool proxy adds the call's `idempotency_key` to write-tool calls, so a retry never repeats the action. Built in PoC-5: write mode, the idempotency keyword, and the error codes `unknown_tool`, `tool_denied`, and `tool_unavailable` ([contract v4](../../contracts/contract-v4.md)). The adapter adds the key as a tool argument only when the tool's schema declares it. A third-party tool server may also ignore the key.
- The tool proxy traces every tool call, and each call shows as a `tool_call` event. Each call is one `chassis.tool.call` span with the parsed trace id and, for an in-flight run, its `request_id`.
- Tool descriptions and tool outputs are treated as data, never as instructions.
- A failed or malformed tool call is retried, then goes to `fallback_route` (017 H-4).
- The code-execution tool behind `ToolPort`. Decided in [ADR-005](../adr/005-remote-lane-auth-and-trust-admission.md), decision 6: `code-runner`, an MCP server in a gVisor sandbox with no network, with one agent-sandbox sandbox per call. B12 is closed for the code runner, and stays open everywhere else. Generated code that runs through it does not make an agent untrusted (ADR-001 item 5).
- A tool result over 1 MiB (suggested: `MAX_RESULT_BYTES`) is a non-retryable `tool_unavailable`, in the gateway and in the tool proxy. The gateway decodes gzip or deflate itself, in bounded pieces, and refuses any other encoding. Built in PR #91: `packages/chassis/tests/test_tool_gateway_contract.py::test_a_result_one_byte_over_the_cap_is_refused`, `::test_a_gzip_answer_over_the_cap_is_refused_on_its_decoded_size`, `packages/chassis/tests/test_tool_endpoint.py::test_a_port_result_over_the_cap_reaches_the_workload_as_tool_unavailable`.
- One argument checker, `chassis/ports/tool_arguments.py`, used by the fake and the gateway. Built in PR #91: `packages/chassis/tests/test_tool_arguments.py::test_each_rule_refuses_and_names_the_path`, `packages/chassis/tests/test_tool_gateway_contract.py::test_enum_and_nested_arguments_are_refused_before_any_request`.
- Suggested: first tool adapters for MCP tools (MCP Python SDK) and plain HTTP APIs, with endpoints set in config.

## Reuse

- **Use:** the LiteLLM MCP gateway (`/mcp`) for per-key tool allow-lists. The gateway's allow-list is the limit; the chassis has no allow-list of its own. Guided decoding for tool JSON is parked (2026-11-06, product plan).
- **Build:** the tool port, the MCP tool proxy that workloads point their MCP client at, the JSON fallback mode, and passing `idempotency_key` to write tools.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Finding tools through the registry (076 R-1, 092 R-12).
- Fine-tuning an SLM for tool calls (063 S-8).
- Tool calls from the orchestrator (099 O-1).
- Workloads that run code themselves. They fail the trust rule and run in the `remote` lane (055 CH-6).
- Parked on 2026-11-06 (product plan): native tool calls or JSON forced to the tool's schema by guided decoding, and the box "a test tool agent gets the same result from an allow-listed read tool on a route with native tool calls and on a route that uses guided JSON". `ChatCompletionRequest` drops `response_format`, and nothing passes guided-decoding parameters. It returns with the SLM track.
- The registry's review of a changed description (082 R-9). The box on description hashes is the chassis side only.

## Acceptance criteria

- [ ] A call to a tool that is not allow-listed is refused and logged, and no request reaches the tool. The refusal is tested (`test_tool_idempotency.py::test_the_allow_list_refuses_before_the_tool_runs`); the proof still missing is a log or metric line carrying `tool_denied`.
- [x] A tool that is not on the key's allow-list is refused by the gateway, even with the chassis config check switched off. A direct call from the workload to the gateway fails, because the workload has no key. · Delivered in PoC-5: `packages/chassis/tests/test_tool_gateway_contract.py::test_the_unlisted_probe_is_refused_and_its_marker_never_seen`; `pocs/poc-05-sandboxed/tests/test_poc05_kind_tool_gateway.py::test_unlisted_probe_is_refused_with_its_control`, `pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py::test_mcp_gateway_refuses_the_workload_without_the_chassis_key` (kind; in `remote-lane.yml` since PR #79, [run 42299094368](https://github.com/odere-pro/agent-playground/actions/runs/42299094368)); `pocs/poc-05-sandboxed/notes/2026-10-09-close-runs.md` section 6. The chassis has no allow-list of its own, so "with the chassis check switched off" holds by construction.
- [ ] A tool with side effects is refused unless its config says `mode: write`.
- [ ] Retrying a request that used a fake write tool performs the side effect once, because the tool proxy passes the same `idempotency_key`. The proof is a client retry with one `Idempotency-Key` that produces one side effect on the fake write tool.
- [ ] A bare tool name resolves to its one `<server>-<name>` match, or is refused. Outside a run, an unknown name answers `unknown_tool`. Today a write tool called by its bare name, or one that does not declare the key, never gets the key (`idempotency_key_required`), and on `/mcp` outside a run an unlisted name answers `idempotency_key_required` (`adapters/mcp/server.py`).
- [x] Every tool call through the proxy shows in the trace and as a `tool_call` event. · Delivered in PoC-2, with the fake tools: `packages/chassis/tests/test_tool_endpoint.py::test_one_span_per_call_with_the_tool_and_the_inbound_trace_id`; `pocs/poc-02-two-engines-one-contract/tests/test_tools.py::test_the_tool_works_on_each_engine[*]`; the `tool_call` event in each Python engine's stream in [the demo](../../../pocs/poc-02-two-engines-one-contract/demo/2026-10-01-demo-sidecar.md).
- [ ] Malformed tool-call output is retried, then answered by the fallback route.
- [ ] In a prompt injection test, a tool output with injected instructions adds no tool calls and does not change the allow-list. The guard blocks it today (`test_guard_stages.py::test_tool_output_injection_is_an_mcp_error_the_workload_never_reads`); the proof still missing is a red-team case that counts tool calls before and after the injection. Two red-team misses stay here: `tool-04-base64` and `exfil-03-write-tool-gets-secret` ([the red-team note](../../../pocs/poc-08-cross-cutting-build/notes/2026-10-29-redteam.md)).
- [ ] Tool calls count toward the call's budget (suggested: a `max_tool_calls` limit). Today the endpoint names the run but does not charge it, or count uncorrelated tool calls.
- [x] Code sent to the code-execution tool runs in its sandbox, not in the workload or chassis container. · Delivered in PoC-5: `pocs/poc-05-sandboxed/tests/test_poc05_kind_code_runner.py::test_code_runs_on_gvisor_with_no_egress`, `pocs/poc-05-sandboxed/tests/test_poc05_kind_code_runner.py::test_files_do_not_cross_calls` (kind, `remote-lane.yml`).
- [x] The MCP adapter and the fake tools pass the same `ToolPort` contract suite, and are picked by `spec.adapters.tools`. · Delivered in PoC-5: `packages/chassis/tests/test_contracts.py::TestInMemoryTools`, `packages/chassis/tests/test_tool_gateway_contract.py::TestMcpGatewayTools`, `packages/chassis/tests/test_tool_endpoint.py::test_fake_profile_builds_the_fake_tools`.
- [ ] The tool proxy passes the `ToolPortContract` bound through `/mcp` in the `inprocess` and `sidecar` lanes.
- [ ] A tool description that changes between refreshes is caught: the chassis hashes each description, counts a change in `chassis_tool_description_changed_total{tool}`, and logs which tool changed (gap review, 2026-11-07).

## Dependencies

- Depends on: [018 H-18](018-H-18-idempotency.md), [017 H-4](017-H-4-harness-features.md), [022 H-6](022-H-6-security-middleware.md)
- Blocks: [063 S-8](063-S-8-tool-call-finetune.md), [140 CH-20](140-CH-20-session-memory-tools-and-saver.md)

## References

- Epic story: [H-16 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [B.3](../slm-agent-platform-epic-v3.md#b3) · [R2](../slm-agent-platform-epic-v3.md#r2)
- Backlog plan: [000-plan.md](000-plan.md)
