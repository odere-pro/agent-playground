# Findings W2b: Chassis MVP, second half (harness, events, security, deploy)

Session: W2b. Issues: 017–026. Waves: W2.
Rubric: [rubric.md](rubric.md).

## Summary

PoC-4, PoC-5, PoC-7, PoC-8, and PoC-9 built most of the chassis side of these issues: the evaluator gate with retry and fallback, timeout and budget per run, idempotency on `StatePort`, result events through a broker client, OIDC and scopes, rate limits, AI marking, the `agent-lib` chart, the Copier template, and hard requirement 1 on kind. This review ticks 34 of 84 boxes and retires none. What is left is cluster work (the chart and its NetworkPolicy on an enforcing CNI, admission rules in the chart, per-service isolation, revoke), the retry stage on a model error, broker hardening, and event-triggered runs, which do not exist at all. 017 H-4 feeds phase B, 018 H-18, 024 CH-3, and 026 CH-4 feed phase A, 022 H-6 feeds E, 025 H-10 feeds F, and the event issues 019 to 021 and 023 C-5 are `later`. The top risk: 026 CH-4, the phase A issue, depends on issues in later phases (020 X-8, 022 H-6, 025 H-10), and none of its boxes asks for the admission rules phase A needs in the chart.

## Issues

| Issue | Boxes | Phase | Verdicts | Evidence or reason |
| ----- | ----- | ----- | -------- | ------------------ |
| 017 H-4 | 6/8 | B | tick, conflict, resize M, deps, drift, cite | Gate, timeout, and budget tests in every lane. Open: 1 (schema check, no phase needs it), 8 (no retry on a model error exists). Phase B names that retry stage, but no box asks for it (W2b-05). The gate fails open on an evaluator outage (W2b-03). Depends on 012 H-3, half parked (W2b-07) |
| 018 H-18 | 7/8 | A | tick, tick-blocked | PoC-4 replica tests and the `StatePortContract` bindings. Open: 5 (no test sends a client key and reads it back in the workload's `ctx` in the `sidecar` lane). The principal is not in the store key yet (`idempotency.py:176-182`), and auth now exists (W2b-10) |
| 019 H-17 | 5/9 | later | tick, drift, tick-blocked, proposal | `test_result_events.py`, the EventPort suite on Kafka and the in-memory bus, and the static secret check. Open: 2 (two extensions untested on a real result event; payload not checked against its JSON file), 3 (the box says "matches"; contract v3 sends the key's hash), 6 (DEC-1 has not picked the broker), 9 (no payload parity test across lanes) |
| 020 X-8 | 1/7 | later | tick, resize L, tick-blocked | Events land on Kafka through the scale stack (dated note). Open: 2 (plain manifest, no chart), 3 (two topics in `seed.sh`, not the D.3 catalog from config), 4 (retention and restart untested), 5 (no lag metric), 6 (ordered by key hash, not `run_id`), 7 (no-credential refusal proven on kind; no ACLs: "no authorizer" in `kafka.yaml`) |
| 021 H-20 | 0/8 | later | resize M, keep scope | No event-triggered run exists: `spec.events.consume` is refused at load (`packages/chassis/tests/test_server.py::test_events_consume_is_refused_until_event_triggered_runs_exist`), and `Interface` has no event value (`core/inbound.py:67`). Only the port half (subscribe, groups) is tested |
| 022 H-6 | 5/11 | E | tick, conflict, tick-blocked, drift | OIDC scopes, the 413 body cap, the no-key checks, the PoC-5 secret check from inside the workload, and the `AuthPort` and `GuardrailPort` suites. Open: 1 (`/metrics` open by default contradicts the box), 3 (two-replica test on one `InMemoryState`, not Valkey), 5 (`allowed_agents` not built), 6 (names never masked), 7 (memory with redaction not measured), 8 (Prompt Guard 2 run waits on the Mac). `rate_limit_on_unavailable: fail_open` (W2b-04) |
| 023 C-5 | 5/8 | later | tick, drift, cite | Marking tests for headers, the body field, the event extension, config-load refusal, and lane parity. Open: 4 (no client guide), 6 (no contract-suite case), 8 (overwrite shown for headers only). The AI Act date is 2026-12-02 (W2b-22) |
| 024 CH-3 | 3/9 | A | tick, drift, resize L, tick-blocked | The chart ran on kind on 2026-11-05 (`2026-11-05-kind-hpa-drill.md`), so the "Rendered only" status is stale. Open: 2, 3 (tag roll and pin untested), 4, 5 (render only), 7, 8 (PoC-4 evidence used the reverse container order, W2b-06) |
| 025 H-10 | 1/10 | F | tick, split, tick-blocked | A fresh clone passes CI with only its name changed (hosted run 41087571278). Open: 9 boxes. Boxes 2 to 6 need a cluster, Argo CD, Kyverno, and event triggers (021 H-20, `later`). Phase F's published pin and the chart in the generated repo have no box (W2b-08) |
| 026 CH-4 | 1/6 | A | tick, deps, resize L, drift | Each internal service refuses the sidecar workload on kind (`test_poc05_kind_hardreq1.py`, dated notes). Open: 2 (MinIO through the chassis), 3 (one Valkey user `~*` for every agent, `seed.sh:99-100`), 4 (no public-address or other-pod probe; DNS H20 open), 5 (no revoke test), 6 (hard requirement 1 and tool-gateway suites not in CI). Deps point at later phases (W2b-01). No box asks for the admission rules in the chart (W2b-02) |

## Ticks

Ten of these tests ran offline in this session: `scripts/check_offline.sh` on 10 ids, `26 passed, 1 xfailed in 18.03s` (the xfail is the TypeScript budget cell, a strict xfail by design).

- `017 H-4`, box "With `retries: 1` and a fake evaluator": `· Delivered in PoC-7, PoC-8: packages/chassis/tests/test_evaluator_gate.py::test_a_failing_answer_retries_once_then_falls_back, pocs/poc-07-cross-cutting-decisions/tests/test_poc07_evals_gate.py::test_poc07_evals_gate_retries_once_then_falls_back (sidecar), pocs/poc-08-cross-cutting-build/tests/test_poc08_c6_gate.py::test_the_gate_retries_then_falls_back_to_the_configured_route (every lane)`
- `017 H-4`, box "A call that runs past `timeout_ms`": `· Delivered in PoC-4: pocs/poc-04-stateless-scalable/tests/test_timeout_budget_per_engine.py::test_past_timeout_ms_the_run_ends_with_a2a_timeout_and_the_task_is_cancelled, packages/chassis/tests/test_a2a_sidecar.py::test_timeout_mid_run_yields_a2a_timeout_and_cancels`
- `017 H-4`, box "A call that would go past `budget.max_tokens`": `· Delivered in PoC-4: pocs/poc-04-stateless-scalable/tests/test_timeout_budget_per_engine.py::test_a_spent_budget_refuses_the_next_model_call_with_budget_exhausted, packages/chassis/tests/test_model_proxy.py::test_an_exhausted_run_is_refused_with_429_complete_and_streamed`
- `017 H-4`, box "A workload that ignores `ctx.budget`": `· Delivered in PoC-4, PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_hostile_offline.py::test_h29_past_the_run_budget_is_429_and_a_run_with_budget_left_is_200; no workload reads ctx.budget.max_tokens`
- `017 H-4`, box "Retries and fallbacks show in metrics": `· Delivered in PoC-8: packages/chassis/tests/test_metrics.py::test_gated_runs_and_fallbacks_are_both_counted, docs/contracts/contract-v7.md#a3-the-families (chassis_gate_fallbacks_total over gate_runs)`
- `017 H-4`, box "With `evaluator_gate` off": `· Delivered in PoC-7: packages/chassis/tests/test_evaluator_gate.py::test_gate_off_makes_no_evaluator_call_and_does_not_wrap, pocs/poc-07-cross-cutting-decisions/tests/test_poc07_evals_gate.py::test_poc07_evals_gate_off_makes_no_evaluator_call`
- `018 H-18`, box "Two calls with the same key and input": `· Delivered in PoC-4: pocs/poc-04-stateless-scalable/tests/test_idempotency_replicas.py::test_a_repeated_key_returns_the_same_result_on_any_replica, ::test_a_repeated_key_makes_no_second_model_call`
- `018 H-18`, box "The same key with a different input": `· Delivered in PoC-4: pocs/poc-04-stateless-scalable/tests/test_idempotency_replicas.py::test_same_key_other_input_is_a_conflict (422)`
- `018 H-18`, box "Two concurrent calls with the same key": `· Delivered in PoC-4: pocs/poc-04-stateless-scalable/tests/test_idempotency_replicas.py::test_concurrent_duplicates_run_once, pocs/poc-04-stateless-scalable/tests/test_idempotency_valkey.py::test_concurrent_duplicates_run_once_through_valkey`
- `018 H-18`, box "A cached result is returned as a stream": `· Delivered in PoC-4: pocs/poc-04-stateless-scalable/tests/test_idempotency_replicas.py::test_a_cached_result_replays_as_a_stream`
- `018 H-18`, box "Only the chassis is given the Valkey credential": `· Delivered in PoC-4, PoC-5: pocs/poc-04-stateless-scalable/tests/test_read_only_compose.py::test_no_key_in_any_workload_env, pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py::test_valkey_refuses_the_workload_without_the_chassis_password (pocs/poc-05-sandboxed/notes/2026-10-08-sidecar-suite.md)`
- `018 H-18`, box "Cache entries expire": `· Delivered in PoC-4: pocs/poc-04-stateless-scalable/tests/test_idempotency_replicas.py::test_the_cache_expires_after_ttl, packages/chassis/tests/test_idempotency.py::test_a_cached_result_lives_ttl_s`
- `018 H-18`, box "The Valkey adapter and the in-memory fake": `· Delivered in PoC-4: packages/chassis/tests/test_state_contract.py::TestInMemoryState, packages/chassis/tests/integration/test_valkey_state_contract.py::TestValkeyState, packages/chassis/tests/test_profiles.py::test_overrides_pick_the_adapter_per_port`
- `019 H-17`, box "With `result_events` on": `· Delivered in PoC-4: packages/chassis/tests/test_result_events.py::test_a_completed_run_publishes_one_valid_cloudevent, ::test_a_failed_run_publishes_the_failed_type`
- `019 H-17`, box "A broker outage does not fail": `· Delivered in PoC-4: packages/chassis/tests/test_result_events.py::test_a_failing_broker_does_not_fail_the_run (chassis.events.publish_failed)`
- `019 H-17`, box "The core has no broker client import": `· Delivered in PoC-4, PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_events_agnostic.py::test_the_events_port_and_core_import_no_broker_client_in_source, pocs/poc-04-stateless-scalable/tests/test_swap_drill.py::test_event_port_swaps_by_config_only`
- `019 H-17`, box "The broker client adapter and the in-memory bus": `· Delivered in PoC-4: packages/chassis/tests/test_events_contract.py::TestInMemoryBus, packages/chassis/tests/integration/test_kafka_events_contract.py::TestKafkaEvents, pocs/poc-04-stateless-scalable/tests/test_swap_drill.py::test_event_port_swaps_by_config_only`
- `019 H-17`, box "The workload container is given no broker credential": `· Delivered in PoC-4, PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_kafka_static.py::test_the_secret_reaches_only_the_chassis_container_of_the_events_agent, pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py::test_kafka_refuses_the_workload_without_the_chassis_credential (pocs/poc-05-sandboxed/notes/2026-10-09-close-runs.md); no Dapr in any profile (ADR-004)`
- `020 X-8`, box "`docker compose up` starts the broker": `· Delivered in PoC-4: pocs/poc-04-stateless-scalable/notes/2026-10-01-dapr-vs-broker.md (kafka row: 2,134 of 2,134 result events, all 3 runs, scale stack)`
- `022 H-6`, box "A valid caller without the needed scope": `· Delivered in PoC-7: packages/chassis/tests/test_security_auth.py::test_run_scope_is_needed_on_every_run_interface, ::test_read_scope_is_needed_for_the_manifest_and_docs (403 forbidden, contract v6 D.4)`
- `022 H-6`, box "An input over the size limit": `· Delivered in PoC-3: packages/chassis/tests/test_limits.py::test_oversized_body_is_413_before_validation, ::test_oversized_streamed_body_with_no_length_is_413`
- `022 H-6`, box "No secret value appears in config": `· Delivered in PoC-5, PoC-8: pocs/poc-08-cross-cutting-build/tests/test_poc08_litellm_static.py::test_no_key_literal_in_any_file, packages/chassis/tests/test_no_key_leak.py::test_a_client_key_never_leaks_from_any_interface`
- `022 H-6`, box "In the `sidecar` lane, the workload container holds no secret": `· Delivered in PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_kind_sidecar_controls.py::test_h25_no_secret_value_reaches_the_workload, ::test_h26_chassis_environ_not_readable_from_the_workload (pocs/poc-05-sandboxed/notes/2026-10-08-sidecar-suite.md, "6 passed")`
- `022 H-6`, box "`AuthPort` and `GuardrailPort` each have a fake": `· Delivered in PoC-7: packages/chassis/tests/test_contracts_cross_cutting_ports.py::TestStaticTokens, ::TestKeywordGuardrails, packages/chassis/tests/test_oidc_adapter.py::TestOidcAuth, packages/chassis/tests/test_litellm_guardrails.py::TestLiteLLMGuardrails (against a LiteLLM stub)`
- `023 C-5`, box "An agent with `risk_class: limited`": `· Delivered in PoC-7, PoC-8: packages/chassis/tests/test_marking.py::test_limited_marks_every_interface_streaming_and_complete, packages/chassis/tests/test_marking_body.py::test_the_openai_body_carries_the_flags_complete_and_on_every_chunk, ::test_the_anthropic_body_carries_the_flags_complete_and_on_message_start (headers everywhere; ai_marking body field in OpenAI and Anthropic, contract-v7.md#e1-the-body-field)`
- `023 C-5`, box "A `limited` or `high` agent with `ai_generated_marking: false`": `· Delivered in PoC-7: packages/chassis/tests/test_marking.py::test_marking_false_is_refused_for_limited_and_high`
- `023 C-5`, box "A `minimal` agent with `ai_generated_marking: true`": `· Delivered in PoC-7: packages/chassis/tests/test_marking.py::test_minimal_marks_only_when_asked`
- `023 C-5`, box "Result events carry the same": `· Delivered in PoC-8: packages/chassis/tests/test_marking_body.py::test_result_events_carry_the_aigenerated_extension, docs/contracts/contract-v7.md#e2-the-cloudevent-extension`
- `023 C-5`, box "The marking holds in the `inprocess` and `sidecar` lanes": `· Delivered in PoC-7: packages/chassis/tests/test_marking.py::test_inprocess_and_sidecar_give_the_same_flags`
- `024 CH-3`, box "A chart that includes the library chart runs a pod": `· Delivered in PoC-9: pocs/poc-09-agent-mvp-template/tests/test_poc09_chart.py::test_sidecar_lane_renders_one_pod_with_the_chassis_and_the_workload, pocs/poc-09-agent-mvp-template/notes/2026-11-05-kind-hpa-drill.md (two pods, each 2/2 Running)`
- `024 CH-3`, box "No service account token is mounted": `· Delivered in PoC-5, PoC-9: pocs/poc-09-agent-mvp-template/notes/2026-11-05-kind-hpa-drill.md ("In the workload container: no service account token"), pocs/poc-05-sandboxed/tests/test_poc05_kind_sidecar_controls.py::test_h02_no_service_account_token_in_the_workload`
- `024 CH-3`, box "The chart runs on a local kind or k3d cluster": `· Delivered in PoC-9: pocs/poc-09-agent-mvp-template/notes/2026-11-05-kind-hpa-drill.md (sidecar lane, chassis as the native sidecar)`
- `025 H-10`, box "A new agent repo made from the template passes CI": `· Delivered in PoC-9: pocs/poc-09-agent-mvp-template/tests/test_poc09_scaffold.py::test_c4_ci_steps_pass_from_a_fresh_clone, pocs/poc-09-agent-mvp-template/notes/2026-10-30-open-items.md (hosted run 41087571278, all eight jobs)`
- `026 CH-4`, box "From the workload container, calls to LiteLLM": `· Delivered in PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py::test_litellm_refuses_the_workload_without_the_chassis_key, ::test_mcp_gateway_refuses_the_workload_without_the_chassis_key, ::test_valkey_refuses_the_workload_without_the_chassis_password, ::test_minio_refuses_the_workload_and_any_unsigned_call, ::test_kafka_refuses_the_workload_without_the_chassis_credential (pocs/poc-05-sandboxed/notes/2026-10-08-sidecar-suite.md "4 passed", pocs/poc-05-sandboxed/notes/2026-10-09-close-runs.md "1 passed")`

## Status bullets folded

- `017 H-4`, "Status after PoC-4": tick (boxes 3, 4, 5). The `http_429` against `budget_exhausted` finding goes to proposals (W2b-12). The TypeScript strict xfail: out of scope (the TypeScript echo has no tool loop).
- `017 H-4`, "Proposed in PoC-7": tick (boxes 2, 7). Fold to one "Decided in ADR-010" sentence; drop "the user accepts it at the PoC-7 pull request" (ADR-010 is accepted).
- `017 H-4`, "Status after PoC-8": tick (box 2, every lane). Timeout across attempts and judge tokens in `budget.max_tokens`: new open box (W2b-05). Default mode and sample rate: DEC-1 (ADR-010 open question; phase B's "done when" settles it).
- `017 H-4`, the "Open concern" paragraph in `## Why`: out of scope. ADR-010 settled it: sample rate by request id, async mode, the judge's tokens as one more `metrics` event.
- `018 H-18`, "Status after PoC-4": tick (boxes 1 to 4, 6 to 8). The principal in the store key and fingerprint: new open box. Per-caller cap on new keys: open box in 022 H-6. `ttl_s` ceiling: built (`TTL_S_MAX`, `config.py:218`), a fact. Default `ttl_s` 3600 and `max_entry_bytes` 65536: DEC-1 (built as 86400 and 1 MiB). TLS to Valkey: out of scope (038 X-1a). Memory alert: out of scope (117 X-3).
- `019 H-17`, "Status after PoC-4": tick (boxes 1, 4, 5, 7, 8). The DLQ bug: proposals (W2b-13). `input` opt-in: open box. `spec.events.consume` refused: out of scope (021 H-20).
- `020 X-8`, "Status after PoC-4": open boxes (topics from config, ACLs). Compose PLAINTEXT: out of scope (test-only stack, product plan "What is reused"); TLS and the controller listener: open boxes.
- `020 X-8`, "Status after PoC-5": open box 7 keeps only ACLs; the no-credential half is the 026 CH-4 tick. "Result events must not carry caller data on a shared broker until topic ACLs": joins the `input` opt-in box in 019 H-17.
- `022 H-6`, "Status after PoC-3": tick (box 4). Content parts per message, the shared event loop, raw exception text in logs and spans: owner list (no product phase names them). Public `/manifest`, `/openapi.json`, `/docs`: settled by the read scope (`test_read_scope_is_needed_for_the_manifest_and_docs`).
- `022 H-6`, "Status after PoC-4": open box (cap new idempotency keys per caller; the rate limit is a rate, not a cap, `rate_limit.py:20-21`).
- `022 H-6`, "Status after PoC-5": out of scope (per-replica uncorrelated cap; LiteLLM's key budget is the cluster cap, contract v4 Known gaps). The key-leak canary suffix and hash: open box, folded into box 9's evidence list once a test checks them.
- `022 H-6`, "Proposed in PoC-7": tick (boxes 2, 11). Fold to "Decided in ADR-008"; the 403 code is `forbidden` (contract v6 D.4), not `insufficient_scope`.
- `022 H-6`, "Status after PoC-8": tick (box 9 evidence). URL allow-list default (Q2) and rate limits with auth off (Q7): DEC-1. Prompt Guard 2 rerun: box 8, waits on the Mac run (011, section 1).
- `023 C-5`, "Proposed in PoC-7": tick (boxes 1, 2, 3, 7). Fold to "Decided in ADR-008".
- `023 C-5`, "Status after PoC-8": tick (boxes 1, 5). The older-consumer item: new open box, with Q3 to DEC-1. Legal on `minimal` with marking on: owner list (legal).
- `024 CH-3`, "Status after PoC-4": drain delay, grace period, exec probes: keep as `## What` values, cite the container-roles note. The container order: drift (W2b-06). Valkey sizing: out of scope (122 X-5).
- `024 CH-3`, "Status after PoC-5": the container order: tick-blocked box 8 (W2b-06). `podPidsLimit`: out of scope (a node setting, 038 X-1a). The deployer and pod-create: out of scope (055 CH-6, ADR-005).
- `024 CH-3`, "Status after PoC-9": tick (boxes 1, 9). "Rendered only" is stale. Admission rule 8 (image source): new open box in 026 CH-4 (W2b-02).
- `025 H-10`, "Status after PoC-2": fact (the template depends on `workload-a2a`). "The Python template server cannot interrupt `handle` mid-await": owner list.
- `025 H-10`, "Status after PoC-6": fact (TypeScript bearer flag). Hand-kept `trustedRepositories`: out of scope (055 CH-6).
- `025 H-10`, "Status after PoC-9": tick (box 10). Kyverno, Argo CD, the cluster run: the `later` half of the split (W2b-08). Publishing the platform packages: new open box (phase F).
- `026 CH-4`, "Status after PoC-1" and "PoC-2": tick (box 1 is the kind successor). `llama-cpp` with no key: settled by the PoC-5 kind suite (every model call needs the chassis key). The per-run credential: DEC-1 (suggested). The hostile cases for the public interfaces: open box 4.
- `026 CH-4`, "Status after PoC-3": open box 3 (per-key tool grants). The PoC-3 MCP demo listing 0 tools: owner list (rerun with a minted key).
- `026 CH-4`, "Status after PoC-4": open box 3 (one Valkey password; per-service user). Compose egress: out of scope (kind is the check). Dapr ports: out of scope (Dapr is parked).
- `026 CH-4`, "Status after PoC-5": tick (box 1). Cloud metadata on a real cloud: out of scope (038 X-1a). DNS in the sidecar lane (H20): open box 4. `serve --host 0.0.0.0` with no CLI rule (B6): proposal (W2b-26).
- `026 CH-4`, "Status after PoC-6": out of scope. The Mac command is a test-only stack; one scoped key per service stays box 3.
- `026 CH-4`, "Status after PoC-8": fact (`exfil-04` moved to 022 H-6). The drill's minted key: evidence for box 2, once MinIO joins it.
- PoC-7 draft `backlog-changes.md`: no issue in 017–026 has a "Status after PoC-7" bullet. The draft's facts for 017, 022, and 023 are folded above. Its open item for 022 ("a test that a workload's `guardrails` key never reaches LiteLLM") becomes an open box in 022 H-6.
- PoC-9 `2026-10-30-w2-w5-report.md`: nothing on 017–024 or 026. Its Copier and Helm items are folded into 024 CH-3 and 025 H-10 above.

## Findings

### W2b-01 [HIGH] [rule 4, 14] docs/planning/issues/026-CH-4-chassis-only-credentials-egress.md
Problem: 026 CH-4 is phase A, but it depends on 020 X-8 (`later`), 022 H-6 (E), and 025 H-10 (F), and it does not depend on 024 CH-3, which owns the chart that carries its NetworkPolicy.
Why it matters: phase A cannot close while its issue waits on issues in later phases; the critical path to the first production agent runs through it.
Fix: in `ROWS`, set CH-4 deps to `['G-1', 'CH-3']`; move the "topics" part of box 3 to 020 X-8 (topic ACLs are its box 7); drop CH-4 from 022's and 025's "Blocks".
Status: open

### W2b-02 [HIGH] [rule 14] docs/planning/issues/026-CH-4-chassis-only-credentials-egress.md
Problem: phase A's "done when" says "the chart carries the egress and admission rules", but no box in 026 CH-4 (or 024 CH-3) asks for the admission rules in the chart, and the chart's defaults still fail PoC-5 admission rule 8 (`pullPolicy: IfNotPresent`, empty `digest`); the admission rules live in `deploy/kind/poc05/admission/` and in 055 CH-6 and 056 CH-7, which are numbered later.
Why it matters: the product plan's P1 gap "the admission rules are not in the chart" has no issue that can close it in phase A.
Fix: add two boxes to 026 CH-4: "`agent-lib` carries the admission rules (untrusted must use `remote`; a minimum chassis version), and a render test plus a kind run refuse a pod that breaks them"; "the chart's defaults pass every PoC-5 admission rule, rule 8 (image by digest) included". Note under Out of scope that 055 CH-6 and 056 CH-7 extend them.
Status: open

### W2b-03 [HIGH] [rule 11] packages/chassis/src/chassis/server/evaluator_gate.py
Problem: an evaluator outage fails open (`evaluator_gate.py:326-330`, counted as `chassis.gate_failed_open`, `packages/chassis/tests/test_evaluator_gate.py::test_an_evaluator_outage_fails_open`, run in this session); 017 H-4 is silent on an outage, the pinned models spec states fail-open as the product behavior, and 042 A-2 wants fail-closed (baseline B-1).
Why it matters: phase B turns the gate on by default, so the outage rule becomes live behavior on every gated call.
Fix: 017 H-4 gets one sentence, "An evaluator outage fails open (ADR-010 item 4); keep or change is open in DEC-1", and no box changes until the user decides; the conflict goes to `proposals.md` with B-1.
Status: proposal

### W2b-04 [HIGH] [rule 11] packages/chassis/src/chassis/server/rate_limit.py
Problem: `spec.limits.rate_limit_on_unavailable` defaults to `fail_open` (`config.py:215`, `rate_limit.py:128`; [contract v7 C.2](../../contracts/contract-v7.md#c2-the-new-error-code)), so a `StatePort` outage turns off the per-caller rate limit, while the pinned security spec lists it as a "budget guard", the guard stages on the same outage fail closed, and keyed idempotent calls answer 503.
Why it matters: one store outage removes the only per-caller budget guard silently (a counter and a warning), and the three behaviors on one outage disagree.
Fix: 022 H-6 gets a box, "with the `cloud` profile, a store outage refuses counted calls with 503 `state_unavailable`", or the default goes to DEC-1 with the suggestion `fail_closed` in `cloud`, `fail_open` in `local`; the code change goes to `proposals.md`.
Status: proposal

### W2b-05 [HIGH] [rule 14, 3] docs/planning/issues/017-H-4-harness-features.md
Problem: phase B names "the retry stage on a model error (017 H-4)", but no box asks for it (box 8 mentions "scripted model errors" only as a test input); `evaluator_gate.py:319-322` sends an errored attempt out as it is, and box 1 (the output schema check) is needed by no phase.
Why it matters: phase B could tick every box of 017 H-4 and still lack the retry stage the product plan's P2 gap names.
Fix: add box "A scripted model error is retried up to `retries` times, then falls back to `fallback_route`, in both lanes, and the retry and fallback are counted"; add box "Across attempts the run keeps one `timeout_ms` and one `max_tokens`: each attempt gets what is left, and the judge's tokens count against the budget" (open from PoC-7, `evaluator_gate.py:45-47`); move box 1 and the `harness.timeout_ms` and `harness.budget` bullets (not in `HarnessSpec`) to Out of scope; resize L to M (two open boxes plus box 8).
Status: open

### W2b-06 [HIGH] [rule 5, 1] docs/planning/issues/024-CH-3-helm-library-chart.md
Problem: `## What` still says "suggested: the workload is the native sidecar" (PoC-4), but the chart makes the chassis the native sidecar (`_containers.tpl:19`, decided by [contract v4](../../contracts/contract-v4.md), "Bind order"), and the only evidence for boxes 7 and 8 (`pocs/poc-04-stateless-scalable/notes/2026-10-01-container-roles.md`, 0 failed calls) was measured with the reverse order.
Why it matters: with the chassis as the native sidecar it stops after the workload, so the drain reasoning that made the rolling restart lossless no longer holds as written; box 8 is on phase A's path.
Fix: replace the bullet with "Decided in contract v4 (Bind order): the chassis is the native sidecar"; keep boxes 7 and 8 open until they run on the chart on kind; carry the drain delay and grace values with the container-roles note as their source.
Status: open

### W2b-07 [HIGH] [rule 13, 4] docs/planning/tools/backlog.py
Problem: 017 H-4 (phase B) depends on 012 H-3, whose vLLM adapter half is parked by the product plan.
Why it matters: a live issue may not depend on a parked one.
Fix: keep the dependency only on the live half of 012 H-3 (the `litellm` `ModelPort`); if W2a splits 012 H-3, point H-4 at the live part, otherwise drop it (013 CH-2 already carries the model path).
Status: open

### W2b-08 [HIGH] [rule 9, 14] docs/planning/issues/025-H-10-template-repo.md
Problem: 025 H-10 has 9 open boxes, more than 5 days of work with two owners: phase F needs the template side (the chart in the generated repo, a published platform pin, the input-as-data test, the registration stub), while boxes 2 to 6 need a cloud-like cluster, Argo CD, Kyverno, and event-triggered runs from 021 H-20 (`later`).
Why it matters: phase F cannot close on an issue whose boxes wait on a `later` issue and on infrastructure the product plan does not schedule.
Fix: split into 025 H-10a (phase F: boxes 1, 7, 8, 9, 10, plus new boxes "the generated repo carries a chart on `agent-lib`" and "the template pins a published platform release, not the `.platform` link"), and 025 H-10b (`later`: boxes 2 to 6: Kyverno, Argo CD, the in-cluster interfaces, event start, the tag roll); 025 H-10b depends on 021 H-20.
Status: open

### W2b-09 [HIGH] [rule 11] packages/chassis/tests/test_metrics.py
Problem: 022 H-6 box 1 says every endpoint except `/health` and `/ready` needs a token, but `/metrics` is open by default (`packages/chassis/tests/test_metrics.py::test_metrics_need_no_token_by_default_even_with_auth_on`; contract v7 A.2); caller API keys are not built either (ADR-008 open question).
Why it matters: the code contradicts a criterion, and the product plan lists "`/metrics` open by default" as a P2 gap in phase E.
Fix: the box stays open; PoC-8 Q1 (`/metrics` open or behind a scope) goes to DEC-1; split box 1 so OIDC bearer tokens on every run and read route is ticked once Q1 is decided, and caller API keys get their own box.
Status: proposal

### W2b-10 [MEDIUM] [rule 1, 10] docs/planning/issues/018-H-18-idempotency.md
Problem: box 5 has no test that the client's key reaches the workload's `ctx` in the `sidecar` lane (`packages/chassis/tests/test_a2a.py::test_server_passes_chassis_input_and_ctx_as_is` is in memory), and the store key is `chassis:idem:v1:<agent>:<sha256(key)>` with no principal (`idempotency.py:176-182`), though PoC-8 built caller auth.
Why it matters: with auth on, two callers who pick the same key share one result.
Fix: box 5 is `tick-blocked` until a sidecar-lane test sends `Idempotency-Key` and asserts the echo workload returns it from `ctx`; add box "With auth on, the store key and the fingerprint include the principal: the same key from two callers runs twice".
Status: open

### W2b-11 [MEDIUM] [rule 5] docs/planning/issues/019-H-17-event-port.md
Problem: box 3 says the event's `idempotencykey` "matches the call's key", and `## What` says `run_id` is the partition key and payload schemas sit in the config store; contract v3 decided the key's sha256 hex for both the extension and the partition key (`server/results.py:130`), and the schemas are generated into `packages/chassis/schemas/`.
Why it matters: box 3 cannot be ticked as written, and an engineer would build the wrong partition key.
Fix: rewrite box 3 to "matches `key_hash` of the call's key" with [contract v3](../../contracts/contract-v3.md#result-events) as the source; replace the two `## What` bullets with "Decided in contract v3"; add a box that checks `traceparent` and `modelroute` on a real result event (box 2 today checks only two extensions).
Status: open

### W2b-12 [MEDIUM] [rule 11] packages/workloads/echo-python/src/echo_python/handle.py
Problem: a spent budget still reaches the client as error code `http_429` with `budget_exhausted` only in the message (`handle.py:96`, `echo_langgraph/mapping.py:121`; asserted at `test_timeout_budget_per_engine.py:257`), the PoC-4 finding that is open in 017 H-4 and PoC-8.
Why it matters: a client cannot tell a spent budget from a rate limit by its code.
Fix: proposal: the template A2A server maps the proxy's `budget_exhausted` to its own code; 017 H-4 keeps it as Out of scope pointing to `proposals.md`.
Status: proposal

### W2b-13 [MEDIUM] [rule 11] packages/chassis/src/chassis/adapters/kafka/events.py
Problem: a failed dead-letter publish raises out of `_deliver` and `_consume` (`events.py:302`, `:319`, `:339`), which ends the subscription's consumer task; it is still listed in contract v3 Known gaps.
Why it matters: one broker hiccup during a dead-letter write silently stops a consumer, which 021 H-20 and 040 D-0 will rely on.
Fix: proposal for the code; in the backlog, give it a box in 053 H-22 (dead-letter topics), not in 019 H-17.
Status: proposal

### W2b-14 [MEDIUM] [rule 10] docs/planning/issues/019-H-17-event-port.md
Problem: result events always carry the redacted `input` (`results.py:113`; `EventsSpec` has only `result_events` and `consume`, `config.py:263-264`), and 020 X-8 says caller data must not go on a shared broker until topic ACLs exist.
Why it matters: any client of a shared broker can read run inputs today.
Fix: add box "`input` is left out of a result event unless `spec.events.include_input` is true (suggested name)".
Status: open

### W2b-15 [MEDIUM] [rule 3] docs/planning/issues/020-X-8-event-broker.md
Problem: 6 of 7 boxes are open plus TLS and the controller listener; ACLs do not exist (`kafka.yaml:22`, `:105`, "no authorizer"); topics are two names in `seed.sh:312`, not the D.3 catalog; no lag metric; and box 2 asks for "its Helm chart" while the kind broker is a plain manifest.
Why it matters: size M (2.5 days) under-counts the work by about half.
Fix: resize M to L; rewrite box 2 to "installs on a Kubernetes cluster by the method DEC-1 picks (Strimzi, a managed service, or a chart)"; rewrite box 6 to "events with one partition key", as contract v3 decided.
Status: open

### W2b-16 [MEDIUM] [rule 3] docs/planning/issues/021-H-20-event-consumer-adapter.md
Problem: no box is met: `spec.events.consume` is refused at load (`config.py:266-272`), the `Interface` literal has no event value, and there is no consumer module; only `EventPort.subscribe` exists, and its suite tests two groups, not a split inside one group (box 6).
Why it matters: size S (1 day) for a new inbound adapter, request and reply, ack after `handle`, redelivery on kill, and a contract test is too small.
Fix: resize S to M; phase `later` (no product phase starts runs from events).
Status: open

### W2b-17 [MEDIUM] [rule 1] docs/planning/issues/022-H-6-security-middleware.md
Problem: box 3's "holds across two pods" rests on two replicas that share one `InMemoryState` (`packages/chassis/tests/test_rate_limit.py::test_two_replicas_share_the_limit_through_the_store`), and the Valkey test (`test_poc08_stack_metrics.py::test_a_burst_meets_the_rate_limit_kept_in_valkey`) uses one chassis.
Why it matters: a status claim passes for a cross-pod proof.
Fix: `tick-blocked` until a stack test runs two chassis processes on one Valkey and shows one bucket.
Status: open

### W2b-18 [MEDIUM] [rule 8] docs/planning/issues/022-H-6-security-middleware.md
Problem: box 5, `security.allowed_agents`, is built nowhere (no code or test matches; contract v6 lines 247 and 424 defer it here), and it only matters when one agent calls another, which the product plan puts after the MVP.
Why it matters: the box keeps a phase E issue open for orchestration work.
Fix: move box 5 to Out of scope, to the orchestrator wave (W89 session to name the issue).
Status: open

### W2b-19 [MEDIUM] [rule 1, 10] docs/planning/issues/022-H-6-security-middleware.md
Problem: boxes 6, 7, and 8 lack evidence: names are never masked (the Collector is regex only, PoC-8 Q6; the LiteLLM drill asserts only email, phone, and card), memory with redaction was never measured (PoC-4's 155–182 MiB predates `chassis/redaction.py`, 2026-10-28), and the injection fixture ran through the chassis guard stages on a stub, not inside a workload's model call on Prompt Guard 2.
Why it matters: three security boxes read as nearly done in the status bullets.
Fix: all three `tick-blocked`; box 6 waits on Q6 (DEC-1); box 7 joins phase E's "the OTel SDK's cost unmeasured" measurement; box 8 waits on the Mac run (011, section 1).
Status: open

### W2b-20 [MEDIUM] [rule 10] docs/planning/issues/022-H-6-security-middleware.md
Problem: two open items from PoC-4 and the PoC-7 draft have no box: a cap on new idempotency keys per caller (the rate limit is a rate, `rate_limit.py:20-21`), and a test that a workload's `guardrails` key never reaches LiteLLM.
Why it matters: both are known holes with no owner box.
Fix: add both as boxes.
Status: open

### W2b-21 [MEDIUM] [rule 5, 10] docs/planning/issues/023-C-5-ai-generated-marking.md
Problem: the boxes name an `ai_generated` field that does not exist as written: the marking is the `X-AI-Generated` and `X-AI-Disclosure-Required` headers on every interface, the `ai_marking` body field in OpenAI and Anthropic, and the `aigenerated` CloudEvent extension (contract v6 E, contract v7 E); boxes 4, 6, and 8 are open (no client guide, no contract-suite case, overwrite shown for headers only); the PoC-8 older-consumer item is not a box.
Why it matters: the next engineer would build a field the contract decided against.
Fix: rewrite boxes 1, 5, and 8 to the built names with "Decided in ADR-008 and contract v7 E"; add box "a consumer on a chassis older than PoC-8 reads an event with `aigenerated`" and send Q3 (`extra="forbid"`, `ports/events.py:28`) to DEC-1; box 8 adds the body field.
Status: open

### W2b-22 [MEDIUM] [rule 14] docs/planning/issues/023-C-5-ai-generated-marking.md
Problem: no product phase names 023 C-5, so it maps to `later`, but AI Act Art. 50(2) marking applies from 2026-12-02, 26 days from this review, and the client guide (box 4) does not exist.
Why it matters: the code meets the date; the guide that tells client teams to show the disclosure may not.
Fix: phase `later` per rule 14, with a note for the consolidation session: the user decides whether the guide (an S task) joins phase A before 2026-12-02.
Status: open

### W2b-23 [MEDIUM] [rule 3, 10] docs/planning/issues/024-CH-3-helm-library-chart.md
Problem: the "Status after PoC-9" bullet says the chart has not run on a cluster, but `pocs/poc-09-agent-mvp-template/notes/2026-11-05-kind-hpa-drill.md` ran it on kind (also stale: `deploy/helm/README.md:93`); 6 boxes stay open, all on kind (2, 3, 4, 5, 7, 8), and box 4 needs a CNI that enforces NetworkPolicy, which the drill did not have.
Why it matters: M under-counts six cluster checks, one under load; the stale bullet hides the drill.
Fix: tick boxes 1, 6, 9; resize M to L; rewrite box 4 to "on a CNI that enforces NetworkPolicy"; the README line goes to the docs owner (outside `docs/planning/`).
Status: open

### W2b-24 [MEDIUM] [rule 3] docs/planning/issues/026-CH-4-chassis-only-credentials-egress.md
Problem: 5 boxes stay open (2: MinIO through the chassis, PoC-5 criterion 3; 3: Valkey is one user `~*` for every agent, `seed.sh:99-100`; 4: no public-address or other-pod probe from the sidecar workload, DNS H20 open; 5: no revoke test; 6: `test_poc05_kind_hardreq1.py` and the tool-gateway suite are not in CI), plus the two boxes from W2b-02.
Why it matters: size M is half the work; this is phase A's main issue.
Fix: resize M to L; rewrite box 4 to run against `agent-lib`'s NetworkPolicy on an enforcing CNI, not the PoC-5 manifests; box 6 names the workflow that runs all four kind suites.
Status: open

### W2b-25 [MEDIUM] [rule 14] docs/planning/issues/024-CH-3-helm-library-chart.md
Problem: the chart already renders default-deny egress (DNS plus named `chassisEgress`, any `ipBlock` refused, `_validate.tpl:173`), but it does not name the metadata address or the API server, and no run has enforced it; 026 CH-4 `## What` asks for both names in a deny list.
Why it matters: the P1 gap "default-deny egress is not a product rule" is half closed in code and unproven on a cluster.
Fix: in 026 CH-4, replace the deny-list bullet with "Decided in the chart: no `ipBlock`, only named peers, so the metadata service and the API server are never reachable" and keep the static check `pocs/poc-05-sandboxed/tests/test_poc05_netpol_static.py::test_poc05_netpol_no_rule_can_reach_metadata_or_api` as its evidence once it runs against `agent-lib`.
Status: open

### W2b-26 [LOW] [rule 11] packages/chassis/src/chassis/server
Problem: `chassis serve --host 0.0.0.0` has no CLI rule (PoC-5 B6), so the pod-IP bind of the public port is held only by the manifests and the chart's argument allow-list.
Why it matters: a hand-written manifest could bind the public port on loopback beside the proxies.
Fix: proposal: `serve` refuses a loopback host for the public port in the sidecar lane; 026 CH-4 keeps it under Out of scope with a pointer to `proposals.md`.
Status: proposal

### W2b-27 [LOW] [rule 6, 5] docs/planning/issues/017-H-4-harness-features.md
Problem: "Fallback. suggested: retry with `ctx.model.route` set to `fallback_route`" is settled (ADR-010 item 4, `test_the_proxy_sends_every_call_of_the_fallback_attempt_to_the_fallback_route`), and the "Open concern" paragraph in `## Why` is answered by ADR-010 (sample rate, async mode).
Why it matters: open-looking text on a decided point.
Fix: drop the `suggested:` marker and cite ADR-010; replace the paragraph with "Decided in ADR-010; the default mode and sample rate are confirmed by phase B's judge numbers".
Status: open

### W2b-28 [LOW] [rule 7] pocs/poc-09-agent-mvp-template/notes/2026-11-05-kind-hpa-drill.md
Problem: the drill note says "PoC-5 runs Calico" for NetworkPolicy, and ADR-005 decision 7 says PoC-5 closed on kindnet, which enforces it.
Why it matters: box 4 of 024 CH-3 and 026 CH-4 depends on which CNI enforces the chart's policy.
Fix: the consolidation session checks `deploy/kind/poc05/cluster.yaml` and fixes the wrong text (a docs fix, not a backlog edit).
Status: open

### W2b-29 [LOW] [rule 4] docs/planning/tools/backlog.py
Problem: 020 X-8, 022 H-6, and 025 H-10 each list 026 CH-4 under "Blocks"; once W2b-01 applies, those three entries go (020 X-8 still blocks 037 M-2, which the W3 session checks).
Why it matters: generated `## Dependencies` sections would still show the old links.
Fix: no separate edit; `sync.py` regenerates them from the new `ROWS` deps.
Status: open
