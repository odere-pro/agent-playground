---
title: "CH-8: Framework event mappings and one non-Python workload in the service template"
labels: ["story", "priority:P1", "phase:0-harness", "area:harness", "size:M", "layer:chassis"]
milestone: "W5 Chassis completion"
index: 58
epic_id: CH-8
depends_on: ["015 H-8", "025 H-10"]
blocks: ["059 H-15", "062 H-11"]
epic_refs: [F.1, B.5]
---

## Why

[ADR-001](../adr/001-chassis-delivery-model.md) item 2: each service maps its framework's events to the chassis's JSON event schema, and a contract test checks the mapping. The service template ships the mappings for the supported frameworks. The ADR also says the chassis must front one agent in another language. The PoC bake-off (PoC-6) picks the supported frameworks, so this issue waits until W5.

## What

- One event mapping per supported framework, in the service template. suggested: PydanticAI, LangGraph, and OpenAI Agents SDK, pending the PoC-6 decision.
- Each mapping turns token deltas, tool calls, and usage into chassis events.
- A contract test per mapping, using the suite from 015 H-8, that checks the stream against the event schema.
- Each framework's model client points at the chassis model proxy (013 CH-2), and its MCP client at the tool proxy (054 H-16).
- One workload in another language that serves A2A itself and emits the same schema, with its own Dockerfile. suggested: TypeScript.
- A short guide: how to add a mapping for a new framework.
- Status after PoC-6 ([scorecard](../../../pocs/poc-06a-bake-off-sidecar-lane/notes/2026-10-09-scorecard.md), [ADR-006](../adr/006-agent-engines-default-supported-lanes.md), proposed): mappings now exist for OpenAI Agents SDK, the TypeScript agent, smolagents (`remote`), and the Claude Agent SDK (`remote`), each in its own file, besides plain Python, PydanticAI, and LangGraph. Mapping size in code lines: PydanticAI 181, LangGraph 224, plain Python 243, OpenAI Agents SDK 295, smolagents 380, Claude 310, TypeScript 551 (with its own A2A server). No engine needed an event change ([contract v5](../../contracts/contract-v5.md), part C). Proposed default: PydanticAI. LangGraph still uses a stand-in MCP client, because the locked `langchain-mcp-adapters` does not import against `mcp` 2. Hosted-model numbers wait on the Mac run. Why: the supported frameworks and their mappings are this issue's.

## Reuse

- **Use:** the OpenInference instrumentors as a reference for which events each framework exposes. The a2a-sdk for JavaScript (or another language) for the non-Python workload.
- **Build:** one event mapping per supported framework, a contract test per mapping, and the non-Python example.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Frameworks that fail the trust rule's behavior test, such as the Claude Agent SDK with shell and file tools switched on, or smolagents. They run in the `remote` lane (055 CH-6).
- The factory CLI's `--framework` option (059 H-15).

## Acceptance criteria

- [ ] The workload for each supported framework passes the contract suite over A2A on localhost and in memory.
- [ ] Each mapping has a contract test that fails on an event that does not match the schema.
- [ ] The non-Python workload passes the same contract suite as the Python ones, with no chassis change.
- [ ] The chassis imports no framework package (import-lint passes).
- [ ] Token deltas and tool-call events stream for every mapped framework, or the gap is listed.

## Dependencies

- Depends on: [015 H-8](015-H-8-testing-kit.md), [025 H-10](025-H-10-template-repo.md)
- Blocks: [059 H-15](059-H-15-agent-factory-cli.md), [062 H-11](062-H-11-adapter-package.md)

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [F.1](../slm-agent-platform-epic-v3.md#f1) · [B.5](../slm-agent-platform-epic-v3.md#b5)
- Backlog plan: [000-plan.md](000-plan.md)
