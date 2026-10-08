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

- A key check on every call. Native calls carry `idempotency_key` in the envelope; OpenAI and Anthropic calls use a header (suggested: `Idempotency-Key`).
- A call with no key gets one from the chassis, returned in the envelope.
- An optional result cache in Valkey, switched on by config, holding the complete result per key for a set time (suggested config key: `harness.idempotency.ttl_s`).
- The Valkey credential is held only by the chassis. The workload cannot reach Valkey ([ADR-001](../adr/001-chassis-delivery-model.md) hard requirement 1).
- The same key with a different input refused as a conflict.
- Two calls with the same key at the same time run the core once; the second waits for the first result.
- A cached result replayed as a stream when the second call asks for streaming.
- The key kept on `Context`, so the event port and write tools can pass it on (B.3). It reaches the workload in `ctx` over A2A. The chassis tool proxy adds it to write-tool calls later (054 H-16).
- Valkey added to Docker Compose.
- Status after PoC-4: built ([contract v3](../../contracts/contract-v3.md#idempotency)). `StatePort` with the in-memory fake and a Valkey adapter, both bound to `StatePortContract`; the key check on all four interfaces (`Idempotency-Key` header or the native body; a minted key is never checked); a lease with renew and takeover, and a fence that drops the claim when no renew got through for `0.8 * lease_s` (suggested), before another replica can take the key; replay in any interface and mode with `Idempotent-Replayed: true`; 422 for the same key with another input. The config key is `spec.idempotency.ttl_s`. Tests: `pocs/poc-04-stateless-scalable/tests/test_idempotency_replicas.py`, `test_killed_replica.py`, `test_idempotency_valkey.py` (`network`), and the Compose kill drill (section 2 of [the drills note](../../../pocs/poc-04-stateless-scalable/notes/2026-10-01-drills.md)). Not ticked here: the gate run goes in the PoC-4 README. Open before production: the scope is per agent, not per caller, until auth arrives (PoC-8), so the store key and the fingerprint must then include the principal; no per-caller quota, so any caller can fill Valkey (256 MB with `noeviction` filled at 186,218 entries of about 1.5 KB, then every keyed call is 503, with a message that says unreachable when the store is full); no ceiling on `ttl_s` (default 86400 s, reloadable) and no retention decision for the request, events, and response cached in the clear; no TLS to Valkey (`valkey://`). suggested: defaults `ttl_s` 3600 and `max_entry_bytes` 65536, and a memory alert.

## Out of scope

- Dropping duplicate events by event `id` in consumers, and dead-letter topics (053 H-22).
- Adding the key to write-tool calls in the chassis tool proxy (054 H-16).
- The per-service Valkey user, and the rule that blocks the workload from Valkey (026 CH-4).
- Idempotent orchestrator steps on resume (102 O-7).

## Acceptance criteria

- [ ] Two calls with the same key and input return the same result, and with the cache on, the second makes no model call.
- [ ] The same key with a different input returns a conflict error.
- [ ] Two concurrent calls with the same key make one model call.
- [ ] A cached result is returned as a stream when the second call sets `stream: true`.
- [ ] The key is on `Context` for later use by events and write tools, and reaches the workload in `ctx` in the `sidecar` lane.
- [ ] Only the chassis is given the Valkey credential. The workload container has none.
- [ ] Cache entries expire after the configured time.
- [ ] The Valkey adapter and the in-memory fake pass the same `StatePort` contract suite, and are picked by `spec.adapters.state`.

## Dependencies

- Depends on: [007 H-1](007-H-1-harness-library-ports-envelope.md), [016 H-9](016-H-9-local-debug-profile.md), [009 CH-1](009-CH-1-engine-connectors-a2a.md)
- Blocks: [022 H-6](022-H-6-security-middleware.md), [053 H-22](053-H-22-event-reliability.md), [054 H-16](054-H-16-tool-port.md), [102 O-7](102-O-7-idempotent-steps.md)

## References

- Epic story: [H-18 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [B.2](../slm-agent-platform-epic-v3.md#b2) · [D.1](../slm-agent-platform-epic-v3.md#d1)
- Backlog plan: [000-plan.md](000-plan.md)
