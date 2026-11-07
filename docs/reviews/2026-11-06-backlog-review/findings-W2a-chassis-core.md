# Findings W2a: W2 Chassis MVP, first half

Session: W2a. Issues: 007–016. Waves: W2.
Rubric: [rubric.md](rubric.md).

## Summary

PoC-1 to PoC-9 built most of the chassis core these ten issues ask for: the ports and fakes, the envelope and event schema, both A2A lanes, the S3 config loader with reload, the three inbound formats, `/ready` and drain, the model proxy with budgets, telemetry, and the cassette kit. This session ticks 35 boxes with evidence, and 9 more after a body edit. No issue retires as written; 011 H-2 and the lane-and-trust half of 008 H-14 retire after the edits named below. What is left is small per issue, and it feeds phases A (007, 009, 010, 011), B (012, 013), E (014), and F (015, 016). The top risk: phase B's route map per task (P1) has no issue, and 012 H-3, its natural home, still carries the parked vLLM and llama.cpp half.

Method: four `Explore` subagents for the boxes, one for the pinned specs and the notes. Every test id below was checked with `grep -rn "def <name>"`. Ten were run offline:

```
UV_NO_SYNC=1 scripts/check_offline.sh <10 test ids from 007, 008, 009, 010, 011, 013, 014, 016> -q -p no:cacheprovider
27 passed in 9.22s
```

`make test` collects `pocs/*/tests` too (`testpaths` in `pyproject.toml`), so a PoC test id with no `network` or `requires_env` mark runs in the gate.

## Issues

`Boxes` counts what is met under the current text. Boxes that tick only after a body edit are counted open.

| Issue | Boxes | Phase | Verdicts | Evidence or reason |
| ----- | ----- | ----- | -------- | ------------------ |
| 007 H-1 | 6/10 | A | tick, drift, resize S | 6 ticks. Box 2 lists `RegistryPort`, `StorePort`, and `EnginePort`, which do not exist (W2a-14). Box 7: no previous major exists yet (W2a-15). Open work: box 5 (the image's version, and `chassis serve` started in CI) and box 10 (a wheel build) |
| 008 H-14 | 3/9 | done (008a), later (008b) | tick, split, drift, deps | Lane, trust, and their read-out are built; class, kind, `spec.modules`, and the B.5 table are not, and no phase needs them before orchestration (W2a-08) |
| 009 CH-1 | 6/8 | A | tick, conflict, resize S | Box 6 ticked. Box 5: `inprocess` has no mid-run cancel by design (W2a-05). Box 3 duplicates 011 box 8 (W2a-19). New open box: a JSON-RPC error still surfaces as `engine_error` (W2a-18) |
| 010 H-12 | 8/11 | A | tick, conflict, drift | 4 ticks (MinIO, schema error, reload, remote URL). Box 11's `ctx` half has no field in `Context` (W2a-06). Boxes 4 and 5 point at fields and `vault://` refs nothing has (W2a-21). Two new open boxes: reload ceilings and `/openapi.json` after a reload (W2a-20). Size stays M |
| 011 H-2 | 5/8 | A | tick, conflict, drift, retire | 5 ticks. Box 5 rewrites to the fixed `/ready` list (W2a-22). Box 7 conflicts with contract v3's drain (W2a-04). Box 8 moves to 024 CH-3 (W2a-19). Then every box has evidence: retire. ADR-003 is still Proposed (W2a-23) |
| 012 H-3 | 2/10 | B | parked, split, conflict, resize L | Boxes 3, 7, and the vLLM half of 10 are parked (W2a-01). Box 6 conflicts with the agent tag the client sends (W2a-03). Open: 1, 4, 5, 6, 9. Suggested new home for the route map per task (W2a-02) |
| 013 CH-2 | 6/8 | B | tick | 2 ticks (the uncorrelated cap, the `fake` profile). Open: box 1 (stock Anthropic SDK, offline) and box 6 (proxy against router counts). New open box: the per-call timeout cap (W2a-24). Size stays M |
| 014 H-7 | 6/8 | E | tick, drift, deps | 6 ticks. Box 2 names Tempo; ADR-007 item 6 makes Jaeger the CI backend (W2a-11). Open: box 6 (Langfuse fields) and sampling. Drop the 012 H-3 dependency (W2a-07). Size stays S |
| 015 H-8 | 4/10 | F | tick, drift, resize M | 4 ticks. Box 7 names an image; the kit runs a handle (W2a-25). Box 8 needs a release workflow nobody has built (W2a-26). Open: 2, 3, 6, 10, plus the scriptable fake model (W2a-10) |
| 016 H-9 | 6/9 | F | tick, parked, drift, deps | 6 ticks. Box 8 (vLLM profile) parked. Box 1 asks for MinIO and Postgres the template stack does not use (W2a-28). Open: 1, 4 (hot reload). Size stays S |

## Ticks

Ready to paste. Each test id exists today.

- `007 H-1`, box "The core package imports no web framework": `· Delivered in PoC-1: pocs/poc-01-walking-skeleton/tests/test_day0.py::test_import_rules_hold` (runs the import-linter contract "no product SDK or web framework in core and ports", `pyproject.toml`).
- `007 H-1`, box "`make test` passes in CI from the first commit": `· Delivered in PoC-1: pocs/poc-01-walking-skeleton/tests/test_day0.py::test_fake_profile_reaches_no_socket_when_the_offline_gate_runs`, `::test_fake_profile_sees_no_api_key_when_the_offline_gate_runs`; `scripts/tests/test_strip_secrets.py::test_check_offline_strips_every_credential_suffix`.
- `007 H-1`, box "An echo `handle` function streams": `· Delivered in PoC-1: packages/chassis/tests/test_collector.py::test_echo_streams_and_collects_the_same_text`; `packages/chassis/tests/test_contracts.py::TestFakeEngineHandle::test_stream_starts_and_ends`.
- `007 H-1`, box "A fixture event stream from a non-Python workload": `· Delivered in PoC-2: packages/chassis/tests/test_schemas.py::test_events_from_a_typescript_workload_validate`; `docs/contracts/contract-v1.md#events-eventsv0json`.
- `007 H-1`, box "The envelope models accept the example envelope": `· Delivered in PoC-1: packages/chassis/tests/test_envelope.py::test_epic_example_response_is_accepted`, `::test_unknown_status_is_rejected`.
- `007 H-1`, box "Every response carries the `versions` field": `· Delivered in PoC-1: packages/chassis/tests/test_envelope.py::test_versions_is_required`; `packages/chassis/tests/test_server.py::test_complete_run_returns_a_response_with_versions`; `docs/contracts/contract-v1.md#versions`.
- `008 H-14`, box "`spec.trust: untrusted` with `spec.engine.connector` set to `sidecar` or `inprocess`": `· Delivered in PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_trust_config.py::test_untrusted_outside_the_remote_lane_is_refused`; `docs/contracts/contract-v4.md#trust`.
- `009 CH-1`, box "One trace ID links the logs of the chassis and the workload": `· Delivered in PoC-8: pocs/poc-08-cross-cutting-build/tests/test_poc08_workload_logs.py::test_the_workload_logs_json_with_the_chassis_runs_trace_id`, `pocs/poc-08-cross-cutting-build/tests/test_poc08_c3_trace.py::test_every_call_has_metrics_and_json_logs_with_the_trace_id`.
- `010 H-12`, box "The echo agent starts with its config read from a MinIO bucket": `· Delivered in PoC-4: pocs/poc-04-stateless-scalable/notes/2026-10-01-drills.md` section 4; `pocs/poc-04-stateless-scalable/tests/test_config_minio.py::test_a_store_change_reaches_the_next_request_when_it_comes_from_s3` (`network`).
- `010 H-12`, box "A config that breaks the schema is refused at start": `· Delivered in PoC-4: packages/chassis/tests/test_config_loader.py::test_start_fails_on_an_invalid_document_and_names_the_field_not_the_value`.
- `010 H-12`, box "A valid change is picked up without a restart": `· Delivered in PoC-4: pocs/poc-04-stateless-scalable/tests/test_config_reload.py::test_a_change_takes_effect_on_the_next_request_with_no_restart`, `::test_a_bad_config_is_refused_and_the_last_good_one_stays`; `docs/contracts/contract-v3.md#config`.
- `010 H-12`, box "A `remote` config without `spec.engine.url`": `· Delivered in PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_trust_config.py::test_the_remote_lane_requires_a_url`.
- `011 H-2`, box "The echo agent gives the same output through all three endpoints": `· Delivered in PoC-3: pocs/poc-03-one-interface-every-client/tests/test_interface_contract.py::TestInterfaces::test_every_interface_gives_the_same_answer`, `::TestInterfaces::test_stream_and_complete_agree`; `pocs/poc-03-one-interface-every-client/tests/test_exit_criteria.py::test_exit_2_only_the_two_legitimate_groups_of_cells_skip` (49 of 64 cells run; the 15 skips are MCP stream and `echo-typescript` in `inprocess`).
- `011 H-2`, box "Stock OpenAI and Anthropic client libraries": `· Delivered in PoC-3: pocs/poc-03-one-interface-every-client/tests/test_exit_criteria.py::test_exit_3_the_sdks_work_with_only_a_base_url_change`.
- `011 H-2`, box "An `error` event returns the right error shape": `· Delivered in PoC-3: pocs/poc-03-one-interface-every-client/tests/test_interface_contract.py::TestInterfaces::test_an_error_event_is_the_formats_error`; `docs/contracts/contract-v2.md#status-table`.
- `011 H-2`, box "Token counts appear in the `usage` field": `· Delivered in PoC-3: pocs/poc-03-one-interface-every-client/tests/test_interface_contract.py::TestInterfaces::test_every_cell_answers`.
- `011 H-2`, box "In the `sidecar` lane, `/ready` reports not ready": `· Delivered in PoC-4: packages/chassis/tests/test_readiness.py::test_the_sidecar_probe_is_false_on_a_bad_status_or_a_dead_workload`; `pocs/poc-04-stateless-scalable/tests/test_liveness.py::test_ready_goes_false_when_the_workload_hangs_and_health_stays_ok`.
- `013 CH-2`, box "A model call with no trace id is served": `· Delivered in PoC-5: packages/chassis/tests/test_model_proxy.py::test_a_call_without_an_in_flight_run_is_served_and_counted`; `packages/chassis/tests/test_uncorrelated_cap.py::test_calls_are_served_until_the_window_has_spent_the_cap`; `docs/contracts/contract-v4.md#the-uncorrelated-cap`.
- `013 CH-2`, box "In the `fake` profile, the proxy answers from the fake": `· Delivered in PoC-1: packages/chassis/tests/test_model_proxy.py::test_complete_maps_the_openai_shape_both_ways`, `::test_stream_ends_with_usage_and_done`.
- `014 H-7`, box "Every log line from the echo agent is JSON": `· Delivered in PoC-8: packages/chassis/tests/test_json_logs.py::test_inside_a_run_a_line_carries_the_request_id_and_a_32_hex_trace_id`; `pocs/poc-08-cross-cutting-build/tests/test_poc08_workload_logs.py::test_the_workload_logs_json_with_the_chassis_runs_trace_id`.
- `014 H-7`, box "In the `sidecar` lane, spans from the chassis and the workload": `· Delivered in PoC-7: pocs/poc-07-cross-cutting-decisions/tests/test_poc07_obs_trace_join.py::test_one_trace_joins_chassis_workload_framework_model_and_tool`; `pocs/poc-08-cross-cutting-build/tests/test_poc08_c3_trace.py::test_a_tool_call_has_one_trace_across_run_model_and_tool_spans`.
- `014 H-7`, box "A `traceparent` header from the caller is continued": `· Delivered in PoC-7: packages/chassis/tests/test_otel_telemetry.py::test_traceparent_continues_the_callers_trace`; `packages/chassis/tests/test_otel_wiring.py::test_run_span_continues_the_callers_traceparent_and_names_the_session`.
- `014 H-7`, box "`GET /metrics` returns Prometheus metrics": `· Delivered in PoC-8: packages/chassis/tests/test_metrics.py::test_a_run_is_counted_by_interface_and_status_with_its_latency`, `::test_a_run_error_is_counted_by_code`, `::test_model_tokens_and_cost_come_from_the_proxy_usage`; `docs/contracts/contract-v7.md#a-get-metrics`.
- `014 H-7`, box "The core package has no OpenTelemetry import": `· Delivered in PoC-7: pocs/poc-01-walking-skeleton/tests/test_day0.py::test_import_rules_hold` (`opentelemetry` is forbidden in `chassis.core` and `chassis.ports`, and allowed only under `chassis.adapters`, `pyproject.toml`).
- `014 H-7`, box "Tests assert on spans with the in-memory span exporter": `· Delivered in PoC-7: pocs/poc-07-cross-cutting-decisions/tests/test_poc07_obs_telemetry_swap.py::test_switching_the_backend_is_exporter_config_only`; `packages/chassis/tests/test_otel_telemetry.py::test_traceparent_continues_the_callers_trace`.
- `015 H-8`, box "The echo agent passes the contract suite for all three APIs": `· Delivered in PoC-3: pocs/poc-03-one-interface-every-client/tests/test_exit_criteria.py::test_exit_2_only_the_two_legitimate_groups_of_cells_skip`; `pocs/poc-03-one-interface-every-client/tests/test_interface_contract.py::TestInterfaces`.
- `015 H-8`, box "A recorded model call replays with no network": `· Delivered in PoC-3: packages/contract-suites/tests/test_recording.py::test_streamed_record_then_replay_gives_the_same_bytes_frame_by_frame`, `::test_replay_never_calls_inner_even_on_a_miss`.
- `015 H-8`, box "Recordings contain no API keys": `· Delivered in PoC-3: packages/chassis/tests/test_recorded_model.py::test_cassettes_hold_no_key`; `packages/contract-suites/tests/test_recording.py::test_save_raises_when_a_key_shaped_string_survives_the_scrub`.
- `015 H-8`, box "An engine that calls the model over HTTP runs offline": `· Delivered in PoC-2 and PoC-6: pocs/poc-02-two-engines-one-contract/tests/test_engines.py::test_python_engine_passes_offline_against_the_fake_model_server` (streaming); `pocs/poc-06a-bake-off-sidecar-lane/tests/test_poc06a_spec.py::test_the_lookup_task_calls_both_tools_in_order` (tool call).
- `016 H-9`, box "The workload's A2A port is not published": `· Delivered in PoC-9: pocs/poc-09-agent-mvp-template/tests/test_poc09_template_ops.py::test_workload_is_hardened_keyless_and_unpublished`, `::test_every_published_port_is_on_loopback_and_only_on_the_chassis`.
- `016 H-9`, box "The chassis reads the config from MinIO": `· Delivered in PoC-4: pocs/poc-04-stateless-scalable/notes/2026-10-01-drills.md` section 4 (the Compose scale stack, through the echo workload).
- `016 H-9`, box "A debugger attaches to each container": `· Delivered in PoC-9: pocs/poc-09-agent-mvp-template/notes/2026-11-05-machine-checks.md` section 2 (breakpoints on 5678 and 5679); `pocs/poc-09-agent-mvp-template/tests/test_poc09_template_ops.py::test_a_debugger_is_attached_to_each_container`.
- `016 H-9`, box "One trace ID links the logs of both containers": `· Delivered in PoC-8:` the same two tests as the 009 CH-1 tick. The workload runs as a thread in those tests, not as its own container.
- `016 H-9`, box "Secrets from `.env` are set only in the chassis container": `· Delivered in PoC-2 and PoC-9: pocs/poc-02-two-engines-one-contract/demo/2026-10-01-demo-sidecar.md` ("key-like variables in the workload: none"); `pocs/poc-09-agent-mvp-template/notes/2026-11-05-machine-checks.md` section 3.
- `016 H-9`, box "No real secret is in the repo": `· Delivered in PoC-8: scripts/tests/test_check_secrets.py::test_check_secrets_fails_when_the_hook_finds_a_new_secret`; CI job `lint-extra` in `.github/workflows/ci.yml` runs `scripts/check_secrets.sh` on every push.

### Ticks after a body edit

These tick once the finding's rewrite lands.

- `007 H-1`, box "Every listed port exists" (after W2a-14): `packages/chassis/tests/test_contracts.py::TestScriptedModel`, `::TestFakeEngineHandle`, `::TestInMemoryTools`, `::TestInMemoryConfig`, `::TestInMemoryTelemetry`; `test_events_contract.py::TestInMemoryBus`; `test_state_contract.py::TestInMemoryState`; `test_contracts_cross_cutting_ports.py::TestStaticTokens`, `::TestKeywordGuardrails`, `::TestScriptedEvaluator`, `::TestInMemoryFeedback` (all under `packages/chassis/tests/`).
- `007 H-1`, the unknown-version half of box 7 (after W2a-15): `packages/chassis/tests/test_events.py::test_unknown_schema_version_is_refused_with_a_clear_error`; `packages/chassis/tests/test_contract_freeze.py::test_both_servers_refuse_an_unknown_schema_version`.
- `008 H-14` (008a), box "Class, kind, … can be read at run time", reduced to lane, trust, and chassis version (after W2a-08): `packages/chassis/tests/test_manifest.py::test_manifest_matches_the_config_and_the_openapi_spec`, `::test_manifest_agent_trust_shows_untrusted_with_the_remote_lane`.
- `009 CH-1`, box "A timeout cancels the A2A task", scoped to `sidecar` (after W2a-05): `packages/chassis/tests/test_a2a_sidecar.py::test_timeout_mid_run_yields_a2a_timeout_and_cancels`; `packages/workload-a2a/tests/test_workload_a2a_server.py::test_cancel_while_the_handle_is_suspended_at_a_yield_is_one_canceled`.
- `011 H-2`, box "`/ready` reports not ready until …" (after W2a-22): `packages/chassis/tests/test_readiness.py::test_ready_goes_false_after_failed_probes_and_health_stays_ok`, `::test_ready_says_starting_then_ready_then_draining`; `docs/contracts/contract-v3.md#ready-and-health`.
- `011 H-2`, box "On SIGTERM …" (after W2a-04): `packages/chassis/tests/test_shutdown.py::test_sigterm_drains_the_in_flight_stream_after_ready_goes_false`; `pocs/poc-04-stateless-scalable/tests/test_graceful_shutdown.py::test_the_chassis_drains_in_flight_runs_and_ready_goes_false_first`; `pocs/poc-04-stateless-scalable/tests/test_workload_drain.py::test_workload_a2a_finishes_in_flight_handle_on_sigterm`; `docs/contracts/contract-v3.md#shutdown`.
- `012 H-3`, box "Each model adapter …", reduced to the router and the fake (after W2a-01): `packages/chassis/tests/test_contracts.py::TestLiteLLMModel`, `::TestScriptedModel`.
- `014 H-7`, box "One call shows as one trace …" with the Collector's backend in place of Tempo (after W2a-11): `pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_litellm.py::test_one_run_is_one_trace_with_a_litellm_span_under_the_model_proxy` (`network`); `pocs/poc-08-cross-cutting-build/notes/2026-10-30-stack-drills.md` (CI run 41011333394, passed).
- `015 H-8`, box "The whole suite runs with one command …" with a handle or endpoint in place of an image (after W2a-25): `packages/agent-testkit/tests/test_agent_testkit_isolation.py::test_the_kit_imports_with_the_chassis_unimportable`.

## Status bullets folded

- `008 H-14`, "Status after PoC-2": tick (default lane, `inprocess` refusal, now trust too). Class, kind, and modules: open boxes in 008b.
- `008 H-14`, "Status after PoC-6": eight engines and the frozen `schema_version "0"` move into `## What`, citing contract v5 part C. "Proposed default: PydanticAI" is stale (W2a-17). Hosted numbers: the owner list (Mac run, [what is left](../../planning/poc/011-what-is-left.md) section 1).
- `009 CH-1`, "Status after PoC-2": facts are ticks. `inprocess` cancel: rewrite (W2a-05). JSON-RPC error: a new open box (W2a-18). The `a2a.unsupported_state` message is fixed: drop it. `inprocess` batching: out of scope (accepted for tests and local runs). The weak `test_cancel_is_clean`: a proposal (W2a-18).
- `010 H-12`, "Status after PoC-1" and "after PoC-2": facts are ticks; the MinIO and reload items are now ticked; `risk_class` and `vault://` stay open boxes (W2a-21).
- `010 H-12`, "Status after PoC-4": facts are ticks. Ceilings and `/openapi.json` after a reload: two new open boxes (W2a-20).
- `011 H-2`, "Status after PoC-1": facts are ticks. The readiness registry: rewrite (W2a-22). SIGTERM drain: rewrite (W2a-04).
- `011 H-2`, "Status after PoC-3": facts are ticks. The complete-mode disconnect is fixed (`docs/contracts/contract-v3.md#a-client-that-leaves-a-complete-call`): a fact for `## What`. MCP disconnect: out of scope. `stop`, `stop_sequences`, the message `name`, and unknown Anthropic keys: the owner list, with ADR-003's acceptance (W2a-23).
- `012 H-3`, "Status after PoC-1": facts are ticks. vLLM, llama.cpp, and the `cloud` refusal: parked. `latency_ms`, the virtual-key spend test, and temperature: open boxes 4, 5, and 9.
- `013 CH-2`, "Status after PoC-2": facts are ticks. The uncorrelated cap is now built (ticked). The per-call timeout cap: a new open box (W2a-24). The Anthropic format: done in PoC-6, but the stock-SDK half of box 1 is open.
- `013 CH-2`, "Status after PoC-6": facts move into `## What`. No finish reason on `ModelPort`: out of scope, citing ADR-003 item 8.
- `014 H-7`, "Proposed in PoC-7": rewrite to "Decided in ADR-007" (accepted 2026-10-30) (W2a-12).
- `014 H-7`, "Status after PoC-8": facts are ticks; the two "waits on run" notes are replaced by the stack-drills note (W2a-13). Sampling: an open box (phase E). `/metrics` with no token (PoC-8 Q1), the feedback panel (Q4), Presidio (Q6): DEC-1.
- `015 H-8`, "Status after PoC-3": facts are ticks. Query-string filtering and key shapes other than `sk-` are fixed: drop them. The record-host guard: an open box. The vcrpy pin, the three socket runners, and the gate time: out of scope (W2a-27).
- Inputs never applied, named for these issues: the PoC-7 draft's 014 H-7 block (W2a-12) and its 013 CH-2 forced-route line, and the PoC-6c "to apply" line for 013 CH-2 (W2a-24). The PoC-9 w2–w5 report names none of 007–016.

## Findings

### W2a-01 [HIGH] [rule 13] docs/planning/issues/012-H-3-model-port.md
Problem: The direct vLLM and llama.cpp adapters (the third `## What` bullet, boxes 3 and 7, and half of box 10) are the SLM track the product plan parked on 2026-11-06; no such adapter exists (`profiles.py` lists `"vllm": "backlog 012 H-3"`).
Why it matters: Until they leave the criteria, 012 can never close, and 013 CH-2, 014 H-7, and 015 H-8 wait on it.
Fix: Move the vLLM and llama.cpp text to `## Out of scope` as "Parked on 2026-11-06 (product plan; models spec, What is parked)". Rewrite box 10 to the router and the fake, then tick it. Retitle in `ROWS` to "Model port to the LLM router". A small model stays reachable as a LiteLLM route (`local-small`).
Status: open

### W2a-02 [HIGH] [rule 14] docs/planning/issues/012-H-3-model-port.md
Problem: The P1 gap "No route map per task" (`ModelSpec.routes`, `judge_route`, phase B) has no issue in 007–016. The models spec says admission must enforce the resolved route, and the map reloads with no restart.
Why it matters: Phase B's done-when ("a config map swaps a model per task with no agent change") has no backlog home.
Fix: Give it to 012 H-3 once W2a-01 lands: three boxes (the map in `ChassisConfig`, admission enforces the resolved route with 403 `model_route_denied`, the map reloads). Resize 012 to L. Check with W01 first; 003 G-1b (named routes) is the other candidate. C2 places it.
Status: proposal

### W2a-03 [HIGH] [rule 11] docs/planning/issues/012-H-3-model-port.md
Problem: Box 6 wants request metadata with "nothing that picks whose spend it is". `adapters/litellm/client.py` sends `x-litellm-tags: agent:<name>` and `metadata.tags`, "so per-agent cost shows up in the router", and it sends no request id.
Why it matters: If spend reports read the tag, attribution rests on metadata, not on the service's virtual key, which is what the issue's `## What` rules out.
Fix: Keep the box open. Goes to `proposals.md`: either the tags stay as a report label only and the box says so, or they are dropped and the key alone attributes spend. Add the request id either way.
Status: proposal

### W2a-04 [HIGH] [rule 11] docs/planning/issues/011-H-2-inbound-adapters.md
Problem: Box 7 says new requests are refused on SIGTERM, and that drain holds when both containers get SIGTERM at once. `docs/contracts/contract-v3.md#shutdown` serves new calls during drain (`/ready` 503, `Connection: close`), and makes the container order a deploy rule: the workload stops after the chassis drains (native sidecar).
Why it matters: The box asks for a behavior the contract in force chose against, so it can never tick.
Fix: Rewrite the box to the contract: "`/ready` goes 503 first; in-flight runs finish; the workload is stopped after the chassis has drained". Tick with the drain tests listed under "Ticks after a body edit". The rolling restart on kind is 024 CH-3's.
Status: proposal

### W2a-05 [HIGH] [rule 11] docs/planning/issues/009-CH-1-engine-connectors-a2a.md
Problem: Box 5 wants the workload to see the cancel in both lanes. `adapters/a2a/inprocess.py` and the contract spec (section 4) say `inprocess` has "no mid-run cancel" by design.
Why it matters: `inprocess` is for tests and local runs only, like its batched streaming, which the issue already accepts.
Fix: Scope box 5 to `sidecar` (and `remote`), and move the `inprocess` cancel to `## Out of scope` with the batching. Tick as listed.
Status: proposal

### W2a-06 [HIGH] [rule 11] docs/planning/issues/010-H-12-config-loader.md
Problem: Box 11 says the workload gets its config and system prompt through `ctx`. `Context` (`core/envelope.py`) has no config field and no prompt field, only `versions.prompt`, and the template workload holds its own prompt.
Why it matters: The box describes a design the code did not take, and the no-credential half has kind-only evidence that is not in CI.
Fix: Goes to `proposals.md`, for the user: drop the `ctx` half (the workload owns its prompt; the chassis reports its version), or build it. Keep the no-credential half open in phase A. Its evidence is `pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py::test_minio_refuses_the_workload_and_any_unsigned_call` (`POC05_KIND`), which ticks when the sidecar kind suite runs in CI.
Status: proposal

### W2a-07 [MEDIUM] [rule 4] docs/planning/tools/backlog.py (H-7 row)
Problem: 014 H-7 depends on 012 H-3. Its open boxes (Langfuse fields, sampling) need nothing open in 012.
Why it matters: Once 012 holds phase B's route map, the dependency makes phase E wait on B. The product plan says E can start after A.
Fix: Drop `H-3` from the `H-7` row's deps.
Status: open

### W2a-08 [MEDIUM] [rule 9] docs/planning/issues/008-H-14-one-agent-interface.md
Problem: The lane, the trust value, and their read-out are built and tested. `class`, `kind`, `spec.modules`, and the B.5 table have no code, and no product phase needs them before orchestration ("after the MVP").
Why it matters: 009 CH-1 and 011 H-2 depend on 008 only for the lane field. As one issue, 008 never closes and blocks nothing it really feeds.
Fix: Split into 008a (lane and trust: boxes 2, 3, 4, and box 8 reduced to lane, trust, and chassis version; retire with the ticks above) and 008b (class, kind, modules, B.5: boxes 1, 5, 6, 7, the rest of 8, and 9; size M; phase `later`). Point 040 D-0, 072 C-3, 076 R-1, and 099 O-1 at 008b. 009 CH-1 and 011 H-2 keep 008a.
Status: open

### W2a-09 [MEDIUM] [rule 14] docs/planning/issues/014-H-7-observability.md
Problem: The P1 gap "LangSmith is in no code or config" (phase D: the `otlphttp/langsmith` exporter, `fake-langsmith`, two stack tests; ADR-007 item 10 as amended) has no issue in 007–016. 014 H-7 is the nearest.
Why it matters: Phase D has no backlog home for its Collector half.
Fix: A new phase-D issue for the exporter, the fake, and the stack tests. The `langsmith` feedback adapter (ADR-009 item 3a) goes with the `FeedbackPort` issue. C2 checks W2b, W5, and W6 first.
Status: proposal

### W2a-10 [MEDIUM] [rule 14] docs/planning/issues/015-H-8-testing-kit.md
Problem: The P2 gap "the template's fake model cannot be scripted per case" (phase F; the PoC-9 "Moved to the backlog" list) has no issue. 015 H-8's "fixture kit for business logic authors: fake model" is its home.
Why it matters: Phase F's done-when needs a person to test their own logic offline.
Fix: Add a box to 015 H-8: "In the `fake` profile, a test scripts the model's answer per case, with no chassis import."
Status: open

### W2a-11 [MEDIUM] [rule 5] docs/planning/issues/014-H-7-observability.md
Problem: Box 2 asks for one trace "in Tempo". ADR-007 item 6 makes Jaeger the CI backend and keeps Tempo for production, swapped by the Collector's exporter list only.
Why it matters: No stack runs Tempo, so the box cannot tick, though the property is shown.
Fix: Rewrite to "one trace in the Collector's trace backend (Jaeger in CI, Tempo in production; Decided in ADR-007)". Tick as listed. Also fold the Compose list (Prometheus, Grafana, Loki, Tempo, Langfuse) in `## What` into the ADR-007 shape.
Status: open

### W2a-12 [MEDIUM] [rule 10] docs/planning/issues/014-H-7-observability.md
Problem: The "Proposed in PoC-7 … the user accepts it at the PoC-7 pull request" bullet is stale: ADR-007 was accepted on 2026-10-30 and amended on 2026-11-06. The PoC-7 draft's 014 H-7 block (OtelTelemetry, baggage, the per-engine list, open: named-entity redaction, sampling, remote spans) was never applied.
Why it matters: The issue still reads as if the observability design were open.
Fix: Replace the bullet with "Decided in ADR-007 (accepted 2026-10-30; item 10 amended 2026-11-06)", and fold the PoC-7 draft block in as facts and open boxes.
Status: open

### W2a-13 [MEDIUM] [rule 10] docs/planning/issues/014-H-7-observability.md
Problem: The PoC-8 bullet says the CI evidence "waits on" runs 41011346864 and 41011346881. `pocs/poc-08-cross-cutting-build/notes/2026-10-30-stack-drills.md` records run 41011333394 passing.
Why it matters: A reader thinks the stack drills are unproven.
Fix: Cite the stack-drills note. Add an open box for sampling as a Collector policy (ADR-007 item 8; phase E). Send PoC-8 Q1, Q4, and Q6 to DEC-1.
Status: open

### W2a-14 [MEDIUM] [rule 12] docs/planning/issues/007-H-1-harness-library-ports-envelope.md
Problem: The port list names `RegistryPort` and `StorePort`, which do not exist, and `EnginePort`, which is `EngineConnector` (a connector, not a port; contract spec section 1).
Why it matters: Box 2 is met for the 11 ports that exist, and the two missing ones belong to later issues (the registry, data agents).
Fix: Rename `EnginePort` to `EngineConnector`. Move `RegistryPort` to 076 R-1 and `StorePort` to the data-agent issue, both under `## Out of scope`. Tick box 2 as listed.
Status: open

### W2a-15 [MEDIUM] [rule 8] docs/planning/issues/007-H-1-harness-library-ports-envelope.md
Problem: Box 7 wants a previous-major fixture to validate. `SUPPORTED_SCHEMA_VERSIONS = ("0",)`; no previous major exists, and ADR-006 item 6 (contract v5 part C) says the first breaking change bumps to `"1"` and keeps `"0"`.
Why it matters: The box cannot be tested until a version that may never ship.
Fix: Rewrite to "an unknown `schema_version` is refused with a clear error; when `"1"` ships, `"0"` stays accepted (`docs/contracts/contract-v5.md#c3-what-counts-as-breaking-from-now-on`)". Tick the unknown half.
Status: open

### W2a-16 [MEDIUM] [rule 3] docs/planning/tools/backlog.py (H-1 row)
Problem: 007 H-1 is sized L with 6 of 10 met. After W2a-14 and W2a-15, two boxes are left: the image's version against the package's, with a `chassis serve` start in CI (CI builds the image but only runs it with `sh`), and a wheel build (`uv build`; nothing builds one today).
Why it matters: L overstates phase A by about 4 days.
Fix: Resize to S. The wheel half may move to 025 H-10, which publishes the packages.
Status: open

### W2a-17 [MEDIUM] [rule 5] docs/planning/issues/008-H-14-one-agent-interface.md
Problem: The PoC-6 bullet says "ADR-006, proposed … Proposed default: PydanticAI". ADR-006 was accepted on 2026-10-30 and amended on 2026-11-06: LangGraph is the default (pinned `70244ad`). For 008b, the code switches optional parts with `spec.harness.evaluator_gate` and `spec.events.result_events`, not `spec.modules` (contract spec section 12).
Why it matters: The default engine in the issue is wrong, and 008b's module box names a field the contract does not use.
Fix: "Decided in ADR-006 (amended 2026-11-06): LangGraph is the default; PydanticAI stays supported." In 008b, write the module rule against the existing flags, or decide `spec.modules` in a contract change.
Status: open

### W2a-18 [MEDIUM] [rule 10] docs/planning/issues/009-CH-1-engine-connectors-a2a.md
Problem: The PoC-2 debt "a JSON-RPC error answer surfaces as `engine_error`" is still true: `connector.py` catches `A2AClientError` only (`docs/contracts/contract-v1.md#where-the-code-differs-from-the-decisions-text`). Only the remote plain mode maps it. `EngineConnectorContract.test_cancel_is_clean` still reads `start` and closes.
Why it matters: A sidecar that answers a JSON-RPC error gets the wrong code and `x-should-retry` value.
Fix: Add an open box: "a JSON-RPC error answer from the workload surfaces as an `a2a.*` code". Put the code change and a stronger cancel case in `proposals.md`. Drop the fixed `a2a.unsupported_state` item.
Status: proposal

### W2a-19 [MEDIUM] [rule 12] docs/planning/issues/011-H-2-inbound-adapters.md
Problem: 009 box 3 and 011 box 8 ask the same thing: a call to the workload port from outside the pod fails. Today only the bind refusal is tested (`packages/workload-a2a/tests/test_workload_a2a_server.py::test_non_loopback_host_is_refused`). No live cross-pod test exists, and the sidecar kind suite is not in CI (what is left, section 4).
Why it matters: Two issues stay open on one cluster check that [what is left](../../planning/poc/011-what-is-left.md) already moved to 024 CH-3 ("NetworkPolicy enforcement").
Fix: Move both boxes to 024 CH-3, which phase A's sidecar-suite CI job closes. Then 011 H-2 retires after W2a-04 and W2a-22, and 009 CH-1 has one box left (W2a-18).
Status: open

### W2a-20 [MEDIUM] [rule 10] docs/planning/issues/010-H-12-config-loader.md
Problem: The PoC-4 open items have no box. A reload can raise `spec.limits` with no bound (only `ge=1`) and change `spec.model.route` and `spec.prompt` (`deploy/compose/SECURITY.md` section 7). `/openapi.json` keeps the start limits (`interfaces/native.py`). `idempotency.ttl_s` now has a fixed cap (`TTL_S_MAX`), but it is not a bootstrap ceiling.
Why it matters: A store document is a way to lift the limits that protect the chassis, and only a status bullet records it.
Fix: Add two open boxes: "a reload that passes a ceiling from the bootstrap file is refused, and the last good config stays (suggested: ceilings for `spec.limits` and `ttl_s`)" and "`/openapi.json` shows the limits in force after a reload, or says it shows the start values". Phase A.
Status: open

### W2a-21 [MEDIUM] [rule 8] docs/planning/issues/010-H-12-config-loader.md
Problem: Box 4 tests `human_oversight`, which is not in `ChassisConfig` (contract v6 E.2 leaves it, with the other E.4 fields, to 077 C-1). No test passes a bad `risk_class`. Box 5's `vault://` references appear nowhere; secrets reach the chassis container through the chart (`secretEnv`; architecture spec, "The deploy shape"). Only an inline engine token is refused (`pocs/poc-05-sandboxed/tests/test_poc05_trust_config.py::test_a_bad_auth_block_is_refused[inline-token]`).
Why it matters: Two boxes name fields and a scheme that no phase plans to build here.
Fix: Box 4: test a bad `risk_class` here and move `human_oversight` and the other E.4 fields to 077 C-1. Box 5: rewrite to "a credential-shaped value anywhere in the config is refused", or send the `vault://` choice to DEC-1.
Status: open

### W2a-22 [MEDIUM] [rule 5] docs/planning/issues/011-H-2-inbound-adapters.md
Problem: Box 5 asks for "every registered readiness check". The chassis has a fixed list (lifespan done, config loaded, not draining, the workload probe) and no registry (contract spec section 6; `docs/contracts/contract-v3.md#ready-and-health`).
Why it matters: The box asks for an extension point no phase needs.
Fix: Rewrite to the fixed list, then tick as listed.
Status: open

### W2a-23 [MEDIUM] [rule 10] docs/planning/issues/011-H-2-inbound-adapters.md
Problem: ADR-003 (chat formats onto the canonical request) is still "Proposed, 2026-10-01". `stop`, `stop_sequences`, the OpenAI message `name`, and unknown Anthropic message keys still wait on it (the PoC-3 debt note).
Why it matters: 011 H-2 retires on contract v2 and v3. Its ADR has never been accepted.
Fix: Put the ADR-003 acceptance, with those four items, on the owner list (or DEC-1). Do not hold 011's retirement on it; the behavior is in the contract.
Status: open

### W2a-24 [MEDIUM] [rule 10] docs/planning/issues/013-CH-2-outbound-model-proxy.md
Problem: Three inputs were never applied: the per-call model timeout is not capped at the run budget (`DEFAULT_TIMEOUT_S = 60.0`), the PoC-7 draft's forced fallback route (`chassis.model_route_forced`), and the PoC-6c rule to keep the six-key filter and check the `local-small` route when a key is added. The two "Deadline: PoC-5" debt bullets have passed; the per-run credential and the scoped key are still not built.
Why it matters: The timeout lets one call outlive its run, and the stale deadlines read as if PoC-5 had closed them.
Fix: Add an open box for the timeout cap (with 017 H-4). Fold the forced-route fact and the six-key rule into `## What`. Point the two debt bullets at 026 CH-4, with no deadline.
Status: open

### W2a-25 [MEDIUM] [rule 5] docs/planning/issues/015-H-8-testing-kit.md
Problem: Box 7 runs the suite "against a given workload image". `agent-testkit` runs the chassis in the `fake` profile as its own process against a handle ref (architecture spec, the `inprocess` row), and never imports the chassis.
Why it matters: The box asks for an image runner the design replaced.
Fix: Rewrite to "against a workload, by handle or by A2A endpoint". Tick as listed.
Status: open

### W2a-26 [MEDIUM] [rule 12] docs/planning/issues/015-H-8-testing-kit.md
Problem: Box 8 makes the suite a release gate, but no release or tag workflow exists. Today the suite gates merges only (`ci.yml` job `check`).
Why it matters: The box depends on release work in 025 H-10 (the published pin) and 056 CH-7 (release rings), both higher numbers.
Fix: Reword to "the suite runs in the `check` job on every PR" and tick it, or move the release half to 025 H-10.
Status: open

### W2a-27 [MEDIUM] [rule 3] docs/planning/tools/backlog.py (H-8 row)
Problem: 015 H-8 is sized L with 4 of 10 met. Open after the edits: box 2 (a mutant without `status`, in both transports), box 3 (an event-mapping suite over a fixture), box 6 (scripted errors for `InMemoryConfig`, `InMemoryTelemetry`, `FakeEngine`), box 10 (`make test-integration` runs in no CI workflow), the record-host guard, and W2a-10.
Why it matters: L overstates the work. Box 10 is a real gap: the real adapters' contract suites never run in CI.
Fix: Resize to M. Out of scope: the vcrpy pin, the three socket runners, the gate time.
Status: open

### W2a-28 [MEDIUM] [rule 13] docs/planning/issues/016-H-9-local-debug-profile.md
Problem: Box 8 (switch llama.cpp and vLLM by profile flag) is the parked SLM track; only llama.cpp exists (`deploy/compose/docker-compose.local.yaml`). Box 1 wants MinIO, the router, and Postgres with pgvector. The template's Compose mounts the config as a file, uses an outside LiteLLM, and has neither MinIO nor Postgres. No product phase needs Postgres.
Why it matters: Two of the three open boxes ask for what the product does not use.
Fix: Park box 8. Rewrite box 1 to the template stack ("`docker compose up` from a generated repo starts the chassis and the workload"), and move Postgres to the first issue that needs it. Box 4 (hot reload) stays open; phase F.
Status: open

### W2a-29 [LOW] [rule 4] docs/planning/tools/backlog.py (H-9 row)
Problem: 016 H-9 depends on 014 H-7 and 010 H-12. Its open boxes (hot reload, the Compose rewrite) need nothing open in either.
Why it matters: Phase F's local profile would wait on phase E's Langfuse box.
Fix: Drop `H-7` from the `H-9` row. Keep `H-12` only if W2a-28 keeps MinIO.
Status: open

### W2a-30 [LOW] [rule 1] docs/planning/issues/007-H-1-harness-library-ports-envelope.md
Problem: The core import rule forbids `httpx`, `a2a`, `valkey`, `aiokafka`, and `minio`, but not `socket`, `urllib`, `requests`, or `aiohttp`.
Why it matters: Box 1 ("no network code") is ticked on a rule a plain socket would pass.
Fix: Add those modules to the `forbidden_modules` list in `pyproject.toml`. The tick stands; core imports none today.
Status: proposal
