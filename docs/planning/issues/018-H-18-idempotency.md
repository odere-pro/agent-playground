---
title: "H-18: Idempotency: key check, optional Valkey result cache, key passed to write tools"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:M", "layer:chassis"]
milestone: "W2 Chassis MVP"
index: 18
epic_id: H-18
depends_on: ["007 H-1", "016 H-9", "009 CH-1"]
blocks: ["022 H-6", "053 H-22", "054 H-16", "102 O-7"]
epic_refs: [B.2, D.1]
---

## Why

A repeated call with the same `idempotency_key` must return the same result and never repeat a side effect; this is an epic acceptance criterion. It is what makes retries, event redelivery, and orchestrator resumes safe. It comes after the local debug profile (016 H-9) because it adds Valkey to Docker Compose.

## What

- A key check on every call. Native calls carry `idempotency_key` in the envelope; OpenAI and Anthropic calls use the `Idempotency-Key` header. A key the chassis minted is never checked.
- A call with no key gets one from the chassis, returned in the envelope.
- An optional result cache in Valkey, switched on by config, holding the complete result per key for a set time (`spec.idempotency.ttl_s`). Its ceiling is built: `TTL_S_MAX`, 30 days (`packages/chassis/src/chassis/server/config.py`).
- The Valkey credential is held only by the chassis. The workload cannot reach Valkey ([ADR-001](../adr/001-chassis-delivery-model.md) hard requirement 1).
- The same key with a different input refused as a conflict (422).
- Two calls with the same key at the same time run the core once; the second waits for the first result.
- A cached result replayed as a stream when the second call asks for streaming. A replay carries `Idempotent-Replayed: true`.
- The key kept on `Context`, so the event port and write tools can pass it on (B.3). It reaches the workload in `ctx` over A2A. The chassis tool proxy adds it to write-tool calls later (054 H-16).
- Valkey added to Docker Compose.
- Decided in [contract v3](../../contracts/contract-v3.md#idempotency): `StatePort` with an in-memory fake and a Valkey adapter; a lease with renew and takeover, and a fence that drops the claim before another replica can take the key.
- The default `ttl_s` and `max_entry_bytes` are 001 DEC-1's. They are built as 86400 s and 1 MiB. suggested: 3600 s and 65536 bytes.

## Out of scope

- Dropping duplicate events by event `id` in consumers, and dead-letter topics (053 H-22).
- Adding the key to write-tool calls in the chassis tool proxy (054 H-16).
- The per-service Valkey user, and the rule that blocks the workload from Valkey (026 CH-4).
- Idempotent orchestrator steps on resume (102 O-7).
- A cap on new keys per caller, so one caller cannot fill Valkey (022 H-6).
- TLS to Valkey (038 X-1a), and the Valkey memory alert (117 X-3).

## Acceptance criteria

- [x] Two calls with the same key and input return the same result, and with the cache on, the second makes no model call. · Delivered in PoC-4: `pocs/poc-04-stateless-scalable/tests/test_idempotency_replicas.py::test_a_repeated_key_returns_the_same_result_on_any_replica`, `::test_a_repeated_key_makes_no_second_model_call`
- [x] The same key with a different input returns a conflict error. · Delivered in PoC-4: `pocs/poc-04-stateless-scalable/tests/test_idempotency_replicas.py::test_same_key_other_input_is_a_conflict` (422)
- [x] Two concurrent calls with the same key make one model call. · Delivered in PoC-4: `pocs/poc-04-stateless-scalable/tests/test_idempotency_replicas.py::test_concurrent_duplicates_run_once`, `pocs/poc-04-stateless-scalable/tests/test_idempotency_valkey.py::test_concurrent_duplicates_run_once_through_valkey`
- [x] A cached result is returned as a stream when the second call sets `stream: true`. · Delivered in PoC-4: `pocs/poc-04-stateless-scalable/tests/test_idempotency_replicas.py::test_a_cached_result_replays_as_a_stream`
- [ ] The key is on `Context` for later use by events and write tools, and reaches the workload in `ctx` in the `sidecar` lane.
  - Proven offline by `packages/chassis/tests/test_idempotency_sidecar_ctx.py::test_the_openai_idempotency_key_header_reaches_the_workload_ctx_in_sidecar`, `::test_the_native_envelope_key_reaches_the_workload_ctx_in_sidecar`, `::test_a_minted_key_reaches_the_workload_ctx_and_the_envelope_in_sidecar`; the `Context` and write-tool half by `packages/chassis/tests/test_tool_idempotency.py::test_a_write_in_a_run_gets_the_derived_key`, `::test_the_same_write_twice_in_one_run_is_one_effect`.
  - The template A2A server (`packages/workload-a2a`) with a ctx-echo workload stands in for "the echo workload": it crosses the real A2A hop on Unix sockets and checks `ctx.idempotency_key` equals the envelope key.
  - Open only because ticking the last box needs the `WD` retirement in `docs/planning/tools/backlog.py` (`check.py --strict`), a separate planning change.
- [x] Only the chassis is given the Valkey credential. The workload container has none. · Delivered in PoC-4, PoC-5: `pocs/poc-04-stateless-scalable/tests/test_read_only_compose.py::test_no_key_in_any_workload_env`, `pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py::test_valkey_refuses_the_workload_without_the_chassis_password` (`pocs/poc-05-sandboxed/notes/2026-10-08-sidecar-suite.md`)
- [x] Cache entries expire after the configured time. · Delivered in PoC-4: `pocs/poc-04-stateless-scalable/tests/test_idempotency_replicas.py::test_the_cache_expires_after_ttl`, `packages/chassis/tests/test_idempotency.py::test_a_cached_result_lives_ttl_s`
- [x] The Valkey adapter and the in-memory fake pass the same `StatePort` contract suite, and are picked by `spec.adapters.state`. · Delivered in PoC-4: `packages/chassis/tests/test_state_contract.py::TestInMemoryState`, `packages/chassis/tests/integration/test_valkey_state_contract.py::TestValkeyState`, `packages/chassis/tests/test_profiles.py::test_overrides_pick_the_adapter_per_port`
- [x] With auth on, the store key and the fingerprint include the principal: the same key from two callers runs twice. · Delivered by: PR #89, `packages/chassis/tests/test_idempotency_scope.py::test_over_http_caller_b_never_gets_caller_a_stored_response`, `::test_caller_b_never_gets_caller_a_result_for_the_same_key`

No test checks the fingerprint on its own.

## Dependencies

- Depends on: [007 H-1](007-H-1-harness-library-ports-envelope.md), [016 H-9](016-H-9-local-debug-profile.md), [009 CH-1](009-CH-1-engine-connectors-a2a.md)
- Blocks: [022 H-6](022-H-6-security-middleware.md), [053 H-22](053-H-22-event-reliability.md), [054 H-16](054-H-16-tool-port.md), [102 O-7](102-O-7-idempotent-steps.md)

## References

- Epic story: [H-18 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [B.2](../slm-agent-platform-epic-v3.md#b2) · [D.1](../slm-agent-platform-epic-v3.md#d1)
- Backlog plan: [000-plan.md](000-plan.md)
