---
title: "H-8: Testing kit: fake adapters, contract tests per API, record and replay"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:M", "layer:chassis"]
milestone: "W2 Chassis MVP"
index: 15
epic_id: H-8
depends_on: ["011 H-2", "012 H-3", "009 CH-1"]
blocks: ["025 H-10", "057 H-19", "058 CH-8"]
epic_refs: [F.1]
---

## Why

Contract tests are part of the Phase 0 done-when, and every agent needs the same way to test itself. The kit gives each agent fake adapters, contract tests per API, and record and replay of model calls, so tests run fast, offline, and the same way in CI. It is also the home of [ADR-001](../adr/001-chassis-delivery-model.md)'s hard requirement 2: every contract case runs over A2A, as in production, and directly, as in tests. It comes after the connectors (009 CH-1), the inbound adapters (011 H-2), and the model port (012 H-3) because it tests and fakes them.

## What

- Fake adapters for every port from 007 H-1, with scripted answers and scripted errors.
- A scripted fake model server: OpenAI-compatible `/v1/chat/completions`, streaming and complete, with tool calls. Frameworks call models over HTTP with their own clients, so this is how every engine is tested offline.
- A fixture kit for business logic authors: fake model, fake tools, and event assertions. In the `fake` profile, a test scripts the model's answer per case.
- Integration fixtures with testcontainers (Valkey, Postgres, Kafka, MinIO) for real adapters.
- Schemathesis property tests generated from the OpenAPI spec. Built in PoC-3 (`pocs/poc-03-one-interface-every-client/tests/test_openapi_props.py`).
- A contract test suite per API: `/v1/run`, `/v1/chat/completions`, and `/v1/messages`, streaming and complete, plus `/health` and `/ready`. Built in PoC-3: the inbound suite (`chassis_contracts.inbound`) and the interface suite (`chassis_contracts.interface`).
- Each case runs two ways: over A2A on localhost through the `sidecar` connector, and over A2A in memory through `inprocess` (hard requirement 2).
- One command to run the suite against a workload, by handle or by A2A endpoint, in any language (suggested: a pytest plugin). It starts the chassis in the `fake` profile as a separate process (suggested: `uvx chassis serve --profile fake`), so a workload's environment never installs the chassis package or its dependencies. Built in PoC-9: `packages/agent-testkit`.
- A contract test that checks a framework's events map to the chassis JSON event schema. 058 CH-8 uses it for each framework mapping.
- Record and replay: record real model calls through the router once, then replay them in tests with no network. Built in PoC-3: `chassis_contracts.recording.CassetteTransport`, and `make record-cassettes` re-records offline.
- Recordings stripped of API keys and auth headers.
- A determinism check: the same input and config version give the same output on replay.
- Packaged with the chassis, so the template CI (025 H-10) runs it against the workload.
- Built so later adapters can add their own contract tests.

## Out of scope

- Contract tests for events, MCP, and the public A2A endpoint, added by those issues (021 H-20, 051 H-13, 061 H-5). The A2A transport to workloads is in scope here.
- The CI job that runs a fake workload through the `remote` lane (055 CH-6).
- The framework event mappings, and a non-Python workload to run the suite against (058 CH-8).
- Running the suite in the template CI for each new service (025 H-10).
- The suite as a release gate for the chassis and for every new service (ADR-001 item 11): the published pin (025 H-10) and the release rings (056 CH-7).
- The stateless check in CI (057 H-19).
- PoC-3 debt that stays as it is: vcrpy is pinned to 8.3.0 because of the private `Cassette._save`; three Unix-socket test runners instead of one; the gate grew from about 35 s to about 100-130 s ([the PoC-3 debt note](../../../pocs/poc-03-one-interface-every-client/notes/2026-10-01-debt.md)).

## Acceptance criteria

- [x] The echo agent passes the contract suite for all three APIs, streaming and complete, in both transports: over A2A on localhost through `sidecar`, and over A2A in memory through `inprocess`. · Delivered in PoC-3: `pocs/poc-03-one-interface-every-client/tests/test_exit_criteria.py::test_exit_2_only_the_two_legitimate_groups_of_cells_skip`; `pocs/poc-03-one-interface-every-client/tests/test_interface_contract.py::TestInterfaces`.
- [x] A broken response shape (for example a missing `status`) fails the matching contract test in both transports. · Delivered by: `packages/chassis/tests/test_interface_suite_broken_shape.py::test_a_missing_status_fails_the_native_complete_cell` (`inprocess` and `sidecar`; the native complete cell of `InterfaceContract.test_every_cell_answers` fails with "the envelope has no status", the stream cell still passes), `::test_the_native_cell_passes_over_a_sound_chassis`.
- [x] The event-mapping contract test fails on a fixture stream with an event that does not match the schema. · Delivered by: `agent_testkit.event_mapping.EventMappingContract`; `packages/agent-testkit/tests/test_agent_testkit_event_mapping.py::test_the_schema_case_fails_on_a_fixture_event_that_does_not_match` (four bad fixtures), `::TestToyMapping` and `::TestToyMappingAsync` (a sound mapping passes).
- [x] A recorded model call replays with no network access and gives the same output. · Delivered in PoC-3: `packages/contract-suites/tests/test_recording.py::test_streamed_record_then_replay_gives_the_same_bytes_frame_by_frame`, `::test_replay_never_calls_inner_even_on_a_miss`.
- [x] Recordings contain no API keys or auth headers. · Delivered in PoC-3: `packages/chassis/tests/test_recorded_model.py::test_cassettes_hold_no_key`; `packages/contract-suites/tests/test_recording.py::test_save_raises_when_a_key_shaped_string_survives_the_scrub`.
- [x] Each fake adapter can return a scripted error, so retry and fallback paths can be tested. · Delivered by: `packages/chassis/tests/test_fakes_scripted_errors.py`, one test per fake (model, tools, events, state, config, engine, auth, guardrails, evaluator, feedback), each failing once and then answering; `InMemoryConfig.fail_next` is new. `TelemetryPort` has no error to script: telemetry never fails a run.
- [x] The whole suite runs with one command against a workload, by handle or by A2A endpoint, from an environment that has the kit but not the chassis package. · Delivered in PoC-9: `packages/agent-testkit/tests/test_agent_testkit_isolation.py::test_the_kit_imports_with_the_chassis_unimportable`.
- [x] The suite runs in the `check` job on every PR. · Delivered in PoC-3: `.github/workflows/ci.yml` job `check` runs `make check`, which runs `make test`, the contract suites included.
- [x] An engine that calls the model over HTTP runs offline against the fake model server, including streaming and a tool call. · Delivered in PoC-2 and PoC-6: `pocs/poc-02-two-engines-one-contract/tests/test_engines.py::test_python_engine_passes_offline_against_the_fake_model_server` (streaming); `pocs/poc-06a-bake-off-sidecar-lane/tests/test_poc06a_spec.py::test_the_lookup_task_calls_both_tools_in_order` (tool call).
- [ ] A real adapter and its fake pass the same port contract suite, one with testcontainers and one in memory.
- [x] Recording is refused unless the host ends in `.invalid` or the inner transport is ASGI. · Delivered by: `chassis_contracts.recording.RecordRefused`; `packages/contract-suites/tests/test_recording.py::test_recording_is_refused_when_the_host_may_be_real`, `::test_recording_is_allowed_when_the_host_is_under_invalid`, `::test_recording_is_allowed_when_the_inner_transport_is_asgi`, `::test_replay_is_never_refused_whatever_the_host`.
- [x] In the `fake` profile, a test scripts the model's answer per case, with no chassis import. · Delivered by: `start_chassis(..., model_script=...)`, `WorkloadContract.model_script`, and the `agent_testkit_model_script` fixture; `packages/agent-testkit/tests/test_agent_testkit_model_script.py::test_each_case_gets_its_scripted_answer`, `::test_a_case_gets_its_scripted_model_error`, `::test_this_module_imports_no_chassis`.

## Dependencies

- Depends on: [011 H-2](011-H-2-inbound-adapters.md), [012 H-3](012-H-3-model-port.md), [009 CH-1](009-CH-1-engine-connectors-a2a.md)
- Blocks: [025 H-10](025-H-10-template-repo.md), [057 H-19](057-H-19-stateless-check-ci.md), [058 CH-8](058-CH-8-framework-event-mappings.md)

## References

- Epic story: [H-8 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [F.1](../slm-agent-platform-epic-v3.md#f1)
- Backlog plan: [000-plan.md](000-plan.md)
