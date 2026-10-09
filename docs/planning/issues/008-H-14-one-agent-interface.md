---
title: "H-14: One agent interface for all classes (stateless, orchestrator, data)"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:M", "layer:chassis"]
milestone: "W2 Chassis MVP"
index: 8
epic_id: H-14
depends_on: ["007 H-1"]
blocks: ["009 CH-1", "011 H-2", "040 D-0", "059 H-15", "072 C-3", "076 R-1", "099 O-1"]
epic_refs: [B.1, B.5, Fig.1]
---

## Why

All agent classes (stateless, orchestrator, data) must share one interface and one config schema; this is an epic acceptance criterion. This issue makes class, kind, and modules part of the chassis, so the recorder (040 D-0), the audit log (072 C-3), the registry (076 R-1), and the orchestrator (099 O-1) are built from the same template as the simplifier. It also adds the two fields [ADR-001](../adr/001-chassis-delivery-model.md) puts in the config: the lane and the trust value. It comes right after 007 H-1 because the connectors (009 CH-1) and the inbound adapters (011 H-2) serve this one interface.

## What

- `class` (`stateless | orchestrator | data`) and `kind` (`transformer | evaluator | tool | orchestrator | data`) as typed fields, read from `spec.class` and `spec.kind`.
- A fixed mapping of kind to class: transformer, evaluator, and tool are stateless; orchestrator and data are their own classes.
- `spec.engine.connector` (`inprocess | sidecar | remote`, default `sidecar`) and `spec.trust` (`trusted | untrusted`) as typed fields.
- The same `handle` contract, envelope, and endpoints for every class, in every lane. Only the core differs.
- Optional modules switched on by `spec.modules` (for example `evaluator_gate`, `result_events`).
- The B.5 module table as rules: each module is "Always", "Optional", or not allowed for each class.
- A start-up check that refuses an invalid class, kind, or module combination with a clear error.
- The same check also refuses `untrusted` together with `sidecar` or `inprocess`, and `inprocess` outside the `fake` and `local` profiles. It backs up the admission check at deploy time, and does not replace it.
- One echo agent per class, from the same template, used as test fixtures by later issues. Each is a workload that can run in every lane the tests use. In this issue, they run through the fake engine's direct call from 007 H-1. The lanes come in 009 CH-1.
- The class, kind, loaded modules, lane, trust value, and chassis version available at run time, so the manifest can report them later.
- Status after PoC-2: four workloads (plain Python, PydanticAI, LangGraph, TypeScript) run behind one `handle` contract and pass the same `EnginePort` suite and the same response-shape tests ([PoC-2](../../../pocs/poc-02-two-engines-one-contract/README.md)). `spec.engine.connector` is a typed field with default `sidecar`, and `inprocess` is refused outside `fake` and `local`. Open: `class`, `kind`, `spec.trust`, modules, and the B.5 table.
- Status after PoC-6 ([ADR-006](../adr/006-agent-engines-default-supported-lanes.md), proposed): eight engines run the same three tasks behind one `handle`: plain Python, PydanticAI, LangGraph, OpenAI Agents SDK, TypeScript (`sidecar`), smolagents, the Claude Agent SDK, and `kagent-adk` (`remote`). Proposed default: PydanticAI. The `handle` contract is frozen as the first stable line: `schema_version` stays `"0"` ([contract v5](../../contracts/contract-v5.md), part C). Hosted-model numbers wait on the Mac run.

## Out of scope

- The config loader (010 H-12) and the HTTP endpoints (011 H-2).
- Running the echo agents in the `sidecar` lane (009 CH-1).
- The admission check that blocks `untrusted` workloads in the `sidecar` lane at deploy time (055 CH-6).
- The `agentctl new` scaffolding CLI (059 H-15).
- Orchestration logic and Temporal (099 O-1, 100 O-2).

## Acceptance criteria

- [ ] An echo agent for each class starts from the same template and returns the same envelope shape through the fake engine's direct call.
- [x] `spec.engine.connector` is `sidecar` when the config does not set it. Delivered in PoC-2: `packages/chassis/tests/test_profiles.py::test_the_engine_is_spec_engine_connector_and_defaults_to_sidecar`.
- [ ] `spec.trust: untrusted` with `spec.engine.connector` set to `sidecar` or `inprocess` fails at start with a clear error.
- [x] `spec.engine.connector: inprocess` fails at start in any profile other than `fake` or `local`. Delivered in PoC-2: `packages/chassis/tests/test_profiles.py::test_inprocess_refused_in_cloud`, `::test_inprocess_allowed_in_fake_and_local`; `packages/chassis/tests/test_server.py::test_config_refuses_the_lane_the_profile_forbids`.
- [ ] A kind that does not match its class (for example `class: data`, `kind: tool`) fails at start with a clear error.
- [ ] A module that the B.5 table marks "No" or "Never" for a class fails at start.
- [ ] An optional module not listed in `spec.modules` is not loaded (tested with a fake module).
- [ ] Class, kind, loaded modules, lane, trust value, and chassis version can be read at run time.
- [ ] Unit tests cover every row of the B.5 module table.

## Dependencies

- Depends on: [007 H-1](007-H-1-harness-library-ports-envelope.md)
- Blocks: [009 CH-1](009-CH-1-engine-connectors-a2a.md), [011 H-2](011-H-2-inbound-adapters.md), [040 D-0](040-D-0-recorder-agent.md), [059 H-15](059-H-15-agent-factory-cli.md), [072 C-3](072-C-3-audit-log-agent.md), [076 R-1](076-R-1-registry-data-model-api.md), [099 O-1](099-O-1-orchestrator-agent.md)

## References

- Epic story: [H-14 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [B.1](../slm-agent-platform-epic-v3.md#b1) · [B.5](../slm-agent-platform-epic-v3.md#b5) · [Fig. 1](../slm-agent-platform-epic-v3.md#fig1)
- Backlog plan: [000-plan.md](000-plan.md)
