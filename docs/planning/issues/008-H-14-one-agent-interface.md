---
title: "H-14: One agent interface for all classes (stateless, orchestrator, data)"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:L", "layer:chassis"]
milestone: "W2 Chassis MVP"
index: 8
epic_id: H-14
depends_on: ["007 H-1"]
blocks: ["011 H-2", "040 D-0", "072 C-3", "076 R-1", "099 O-1"]
epic_refs: [B.1, B.5, Fig.1]
---

## Why

All agent classes (stateless, orchestrator, data) must share one interface and one config schema; this is an epic acceptance criterion. This issue makes class, kind, and modules part of the chassis, so the recorder (040 D-0), the audit log (072 C-3), the registry (076 R-1), and the orchestrator (099 O-1) are built from the same template as the simplifier. It also adds the two fields [ADR-001](../adr/001-chassis-delivery-model.md) puts in the config: the lane and the trust value. It comes right after 007 H-1 because the connectors (009 CH-1) and the inbound adapters (011 H-2) serve this one interface.

## What

- `class` (`stateless | orchestrator | data`) and `kind` (`transformer | evaluator | tool | orchestrator | data`) as typed fields, read from `spec.class` and `spec.kind`.
- A fixed mapping of kind to class: transformer, evaluator, and tool are stateless; orchestrator and data are their own classes.
- `spec.engine.connector` (`inprocess | sidecar | remote`, default `sidecar`) and `spec.trust` (`trusted | untrusted`) as typed fields.
- The same `handle` contract, envelope, and endpoints for every class, in every lane. Only the core differs.
- Optional modules switched on by the flags the contract already has (`spec.harness.evaluator_gate`, `spec.events.result_events`; contract spec section 12). A `spec.modules` list needs a contract change first.
- The B.5 module table as rules: each module is "Always", "Optional", or not allowed for each class.
- A start-up check that refuses an invalid class, kind, or module combination with a clear error.
- The same check also refuses `untrusted` together with `sidecar` or `inprocess`, and `inprocess` outside the `fake` and `local` profiles. It backs up the admission check at deploy time, and does not replace it.
- One echo agent per class, from the same template, used as test fixtures by later issues. Each is a workload that can run in every lane the tests use. In this issue, they run through the fake engine's direct call from 007 H-1. The lanes come in 009 CH-1.
- The five kinds that `agentctl new --kind <kind>` renders, each a config with the right class, kind, and modules.
- The class, kind, loaded modules, lane, trust value, and chassis version available at run time, so the manifest can report them later.
- Eight engines run the same three tasks behind one `handle`: plain Python, PydanticAI, LangGraph, OpenAI Agents SDK, TypeScript (`sidecar`), smolagents, the Claude Agent SDK, and `kagent-adk` (`remote`). The `handle` contract is frozen as the first stable line: `schema_version` stays `"0"` ([contract v5](../../contracts/contract-v5.md), part C).
- Decided in [ADR-006](../adr/006-agent-engines-default-supported-lanes.md) (amended 2026-11-06): LangGraph is the default; PydanticAI stays supported. The hosted-model numbers are on the owner list (the Mac run, [what is left](../poc/011-what-is-left.md) section 1).
- Note, 2026-11-10: the start-up refusal sees only two of the 12 B.5 rows, `evaluator_gate` and `result_events` (`packages/chassis/src/chassis/server/config.py`, `switched_on_modules`), so the other rows are table-only until their modules exist.

## Out of scope

- The config loader (010 H-12) and the HTTP endpoints (011 H-2).
- Running the echo agents in the `sidecar` lane (009 CH-1).
- The admission check that blocks `untrusted` workloads in the `sidecar` lane at deploy time (055 CH-6).
- The rest of the `agentctl` factory: frameworks, trust flags, the generated chart, and the governance block (059 H-15).
- Orchestration logic and Temporal (099 O-1, 100 O-2).

## Acceptance criteria

- [ ] An echo agent for each class starts from the same template and returns the same envelope shape through the fake engine's direct call.
- [x] `spec.engine.connector` is `sidecar` when the config does not set it. Delivered in PoC-2: `packages/chassis/tests/test_profiles.py::test_the_engine_is_spec_engine_connector_and_defaults_to_sidecar`.
- [x] `spec.trust: untrusted` with `spec.engine.connector` set to `sidecar` or `inprocess` fails at start with a clear error. · Delivered in PoC-5: `pocs/poc-05-sandboxed/tests/test_poc05_trust_config.py::test_untrusted_outside_the_remote_lane_is_refused`; [contract v4](../../contracts/contract-v4.md#trust).
- [x] `spec.engine.connector: inprocess` fails at start in any profile other than `fake` or `local`. Delivered in PoC-2: `packages/chassis/tests/test_profiles.py::test_inprocess_refused_in_cloud`, `::test_inprocess_allowed_in_fake_and_local`; `packages/chassis/tests/test_server.py::test_config_refuses_the_lane_the_profile_forbids`.
- [x] A kind that does not match its class (for example `class: data`, `kind: tool`) fails at start with a clear error, in a config `agentctl` generates too. · Delivered by: `packages/chassis/tests/test_agent_class.py::test_the_config_refuses_a_kind_that_does_not_match_its_class`; `packages/agentctl/tests/test_agentctl_config.py::test_a_rendered_config_refuses_a_kind_that_does_not_match_its_class`, `::test_the_rendered_config_names_its_class_and_kind`.
- [x] A module that the B.5 table marks "No" or "Never" for a class fails at start, in a config `agentctl` generates too. · Delivered by: `packages/chassis/tests/test_agent_class.py::test_the_config_refuses_a_flag_the_table_marks_no_for_data`; `packages/agentctl/tests/test_agentctl_config.py::test_a_rendered_data_config_refuses_a_module_b5_marks_no` (the two switchable rows; the note above says the rest are table-only; agentctl renders only stateless kinds, so the test edits a generated config to `class: data`).
- [ ] An optional module whose flag is off is not loaded (tested with a fake module), in a config `agentctl` generates too.
- [x] Lane, trust value, and chassis version can be read at run time. · Delivered in PoC-5: `packages/chassis/tests/test_manifest.py::test_manifest_matches_the_config_and_the_openapi_spec`, `::test_manifest_agent_trust_shows_untrusted_with_the_remote_lane`.
- [x] Class, kind, and loaded modules can be read at run time. · Delivered by: `packages/chassis/tests/test_manifest.py::test_manifest_class_kind_and_modules_match_the_config`, `::test_manifest_class_kind_and_modules_are_empty_when_the_config_names_none`, `::test_manifest_class_and_kind_values_are_the_agent_class_values` (`GET /manifest` fields `agent_class`, `kind`, `modules`; `modules` lists the switched-on optional modules, and the always-on modules are implied by the class).
- [x] Unit tests cover every row of the B.5 module table. · Delivered by: `packages/chassis/tests/test_agent_class.py::test_every_b5_cell_is_the_rule` (12 rows by 3 classes), `::test_the_table_has_every_b5_row_and_nothing_else`; the rules are `chassis.agent_class.MODULE_TABLE`.
- [ ] `agentctl new demo --kind <kind>` works for all five kinds, and each result builds and passes the template CI, the stateless check included, with no hand edits.

## Dependencies

- Depends on: [007 H-1](007-H-1-harness-library-ports-envelope.md)
- Blocks: [011 H-2](011-H-2-inbound-adapters.md), [040 D-0](040-D-0-recorder-agent.md), [072 C-3](072-C-3-audit-log-agent.md), [076 R-1](076-R-1-registry-data-model-api.md), [099 O-1](099-O-1-orchestrator-agent.md)

## References

- Epic story: [H-14 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [B.1](../slm-agent-platform-epic-v3.md#b1) · [B.5](../slm-agent-platform-epic-v3.md#b5) · [Fig. 1](../slm-agent-platform-epic-v3.md#fig1)
- Backlog plan: [000-plan.md](000-plan.md)
