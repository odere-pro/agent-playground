---
title: "CH-8: Framework event mappings and one non-Python workload in the service template"
labels: ["story", "priority:P1", "phase:0-harness", "area:harness", "size:L", "layer:chassis"]
milestone: "W5 Chassis completion"
index: 58
epic_id: CH-8
depends_on: ["015 H-8"]
blocks: ["059 H-15", "062 H-11", "128 CH-9"]
epic_refs: [F.1, B.5]
---

## Why

[ADR-001](../adr/001-chassis-delivery-model.md) item 2: each service maps its framework's events to the chassis's JSON event schema, and a contract test checks the mapping. The service template ships the mappings for the supported frameworks. The ADR also says the chassis must front one agent in another language. The PoC bake-off (PoC-6) picks the supported frameworks, so this issue waits until W5.

## What

- One event mapping per supported framework, in the service template. Decided in [ADR-006](../adr/006-agent-engines-default-supported-lanes.md) (amended 2026-11-06): LangGraph is the default, and decision 2 fixes the supported set. Plain Python, PydanticAI, LangGraph, and the OpenAI Agents SDK run in `sidecar`; smolagents and the Claude Agent SDK run in `remote`.
- Each mapping turns token deltas, tool calls, and usage into chassis events. Each lives in its own file. Mapping size in code lines: PydanticAI 181, LangGraph 224, plain Python 243, OpenAI Agents SDK 295, Claude 310, smolagents 380, TypeScript 551 (with its own A2A server). No engine needed an event change ([contract v5](../../contracts/contract-v5.md), part C; [scorecard](../../../pocs/poc-06a-bake-off-sidecar-lane/notes/2026-10-09-scorecard.md)).
- A contract test per mapping, using the suite from 015 H-8, that checks the stream against the event schema.
- Each framework's model client points at the chassis model proxy (013 CH-2), and its MCP client at the tool proxy (054 H-16). LangGraph uses a stand-in MCP client, because the locked `langchain-mcp-adapters` does not import against `mcp` 2 (`packages/workloads/echo-langgraph/src/echo_langgraph/tools.py`).
- One workload in another language that serves A2A itself and emits the same schema, with its own Dockerfile: the TypeScript agent (PoC-6).
- The template ships one mapping per supported framework, as a copy of the echo workload. `test_the_example_logic_is_the_echo_workload` fails on drift (PoC-9).
- The LangChain items of the product's phase C: the `langchain-mcp-adapters` swap, a trace-join test for LangGraph's graph steps (ADR-006 decision 1), and a `langchain` `create_agent` variant next to the `StateGraph` one.
- A short guide: how to add a mapping for a new framework.

## Reuse

- **Use:** the OpenInference instrumentors as a reference for which events each framework exposes. The a2a-sdk for JavaScript (or another language) for the non-Python workload.
- **Build:** one event mapping per supported framework, a contract test per mapping, and the non-Python example.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Frameworks that fail the trust rule's behavior test, such as the Claude Agent SDK with shell and file tools switched on, or smolagents. They run in the `remote` lane (055 CH-6).
- The factory CLI's `--framework` option (059 H-15).
- The TypeScript agent in the template. ADR-006 (D5) keeps it out: the case for it is a team, not a default.
- The checkpointer and interrupt ADRs: 128 CH-9.
- The hosted-model numbers per engine. They are a Mac run on the owner list ([what is left](../poc/011-what-is-left.md), section 1).

## Acceptance criteria

- [x] The workload for each supported framework passes the contract suite over A2A on localhost and in memory. · Delivered in PoC-6: `pocs/poc-06a-bake-off-sidecar-lane/tests/test_poc06a_engine_contract.py::TestEngineInMemory`, `pocs/poc-06a-bake-off-sidecar-lane/tests/test_poc06a_engine_contract.py::TestEngineOverA2A`; `pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_remote_engine_contract.py::TestRemoteEngineOverA2A` ("localhost" is a Unix socket).
- [x] Each mapping has a contract test that fails on an event that does not match the schema. · Delivered by: each workload's `EventMapper` bound to `agent_testkit.event_mapping.EventMappingContract` with a hand-built in-memory framework stream, plus a negative case that fails the schema check: `packages/workloads/echo-pydanticai/tests/test_echo_pydanticai_event_mapping.py::TestPydanticAIMapping`, `::test_the_contract_fails_on_usage_that_maps_to_a_bad_metrics_event`; `packages/workloads/echo-langgraph/tests/test_echo_langgraph_event_mapping.py::TestLangGraphMapping`, `::test_the_contract_fails_on_a_tool_result_that_maps_to_a_nameless_tool_call`; `packages/workloads/echo-openai-agents/tests/test_oai_event_mapping.py::TestOpenAIAgentsMapping`, `::test_the_contract_fails_on_an_output_that_maps_to_a_nameless_tool_call`; `packages/workloads/echo-claude-agent/tests/test_echo_claude_agent_event_mapping.py::TestClaudeAgentMapping`, `::test_the_contract_fails_on_a_result_block_that_maps_to_a_tool_call_with_no_id`; `packages/workloads/echo-smolagents/tests/test_echo_smolagents_event_mapping.py::TestSmolagentsMapping`, `::test_the_contract_fails_on_usage_that_maps_to_a_bad_metrics_event`. TypeScript (`echo-typescript`) checks each mapped event with its own `validateEvent` before it leaves its A2A server; the refusal is tested in `packages/workloads/echo-typescript/test/mapping.test.ts` "a yielded event with an unknown field is workload.bad_event", "a yielded event with NaN or Infinity is workload.bad_event", "schema: refuses an unknown field, a wrong schema_version, a missing field, a wrong type", and `test/schema.test.ts` (`make ts-check`). Plain Python (echo-python) has no framework stream to map; its events are checked by the lane contract.
- [x] The non-Python workload passes the same contract suite as the Python ones, with no chassis change. · Delivered in PoC-6: `pocs/poc-06a-bake-off-sidecar-lane/tests/test_poc06a_criteria.py::test_criterion_2_the_typescript_agent_passes_through_the_generic_sidecar_connector`; `docs/contracts/contract-v5.md` part C.4.
- [x] The chassis imports no framework package (import-lint passes). · Delivered in PoC-1: `pocs/poc-01-walking-skeleton/tests/test_day0.py::test_import_rules_hold` (the "no agent framework anywhere in the chassis" contract in `pyproject.toml`).
- [x] Token deltas and tool-call events stream for every mapped framework, or the gap is listed. · Delivered in PoC-6: `pocs/poc-06a-bake-off-sidecar-lane/notes/2026-10-09-scorecard.md` "Criterion 2" and "Criterion 3" (gaps: smolagents sends one delta; `kagent-adk` shows no `tool_call`).
- [x] The LangGraph template uses `langchain-mcp-adapters` on `mcp` 2, or a test fails when that pairing is ready. · Delivered by: `packages/workloads/echo-langgraph/tests/test_langgraph_handle.py::test_langchain_mcp_adapters_loads_the_chassis_tools_on_mcp_2` (strict xfail; the adapter is a dev dependency in `packages/workloads/echo-langgraph/pyproject.toml`, locked in `uv.lock`; 0.3.2, the newest release on 2026-11-09, requires `mcp<2`), and `packages/workloads/echo-langgraph/tests/test_langgraph_handle.py::test_the_template_carries_the_same_mcp_stand_in`, which keeps the template's stand-in equal to the workload's so the swap lands in both.
- [x] A trace-join test shows LangGraph's graph steps under the run span (ADR-006 decision 1). · Delivered by: `packages/chassis/tests/test_langgraph_trace_join.py::test_each_graph_step_is_under_the_run_span`, `::test_each_graph_step_is_a_span_in_step_order`, `::test_each_graph_step_is_a_child_of_the_graph_span`, `::test_the_graph_span_is_under_the_run_span_through_the_workload` (one tool-using `/v1/run` over A2A, `sidecar` lane, the in-memory span exporter; `poc_harness.trace_join`, the PoC-7 shape).
- [x] `agentctl new` defaults to LangGraph and scaffolds a `sidecar` agent that passes the lane contract. · Delivered by: PR #71, `packages/agentctl/tests/test_agentctl_cli.py::test_the_defaults_scaffold_a_langgraph_sidecar_agent_with_a_first_commit`, `packages/agentctl/tests/test_agentctl_cli.py::test_the_template_default_framework_matches_the_rules`; the lane contract in the scaffolded repo's `make test`: `pocs/poc-09-agent-mvp-template/tests/test_poc09_scaffold.py::test_c1_scaffolded_make_test_passes_offline`.
- [ ] The agent `agentctl new` scaffolds with the LangGraph default passes the evals.
- [x] A `langchain` `create_agent` variant ships in the template. · Delivered by: `packages/agentctl/src/agentctl/template/_frameworks/langchain/handle.py.jinja`; `packages/agentctl/tests/test_agentctl_render.py::test_the_langchain_variant_is_create_agent_on_the_langgraph_pieces`, `packages/agentctl/tests/test_agentctl_render.py::test_the_example_logic_is_the_echo_workload` (shared files equal echo-langgraph's); the rendered agent's unit tests, `packages/agentctl/tests/test_agentctl_rendered_checks.py::test_rendered_unit_tests_pass_offline`; its `make test` offline, `pocs/poc-09-agent-mvp-template/tests/test_poc09_scaffold.py::test_c1_scaffolded_make_test_passes_offline` (`POC09_SCAFFOLD=1`).

## Dependencies

- Depends on: [015 H-8](015-H-8-testing-kit.md)
- Blocks: [059 H-15](059-H-15-agent-factory-cli.md), [062 H-11](062-H-11-adapter-package.md), [128 CH-9](128-CH-9-langgraph-checkpointer-interrupts.md)

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [F.1](../slm-agent-platform-epic-v3.md#f1) · [B.5](../slm-agent-platform-epic-v3.md#b5)
- Backlog plan: [000-plan.md](000-plan.md)
