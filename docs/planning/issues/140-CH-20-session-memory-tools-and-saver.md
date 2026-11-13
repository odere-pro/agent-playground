---
title: "CH-20: Session memory tools on the tool proxy, and the LangGraph saver over them"
labels: ["story", "priority:P2", "phase:0-harness", "area:harness", "size:M", "layer:platform"]
milestone: "W10 Production readiness"
index: 140
epic_id: CH-20
depends_on: ["128 CH-9", "054 H-16"]
blocks: []
epic_refs: [F.1, B.5]
---

## Why

[ADR-014](../adr/014-langgraph-state-between-runs.md) decides where a LangGraph agent keeps state between runs: nowhere by default, and when it must, behind the chassis's `StatePort`, reached only through chassis tools on the workload's tool proxy, keyed by the principal and `session.id`. The ADR decides; this issue builds. Until it lands, the template has no checkpointer and an interrupt ends the run ([ADR-015](../adr/015-interrupts-end-the-run.md)).

## What

- A second tool source behind `/mcp` on the proxy port, next to `ToolPort`'s gateway tools (or a `ToolPort` extension): `memory.get`, `memory.put`, `memory.delete` (suggested names). The chassis scopes every call to the run's session and principal; the workload names no session. `memory.put` and `memory.delete` are write tools under the tool endpoint's derived-key rule; each write is keyed by the run key, so a retried or taken-over run repeats its own write.
- The store key `chassis:mem:v1:<agent>:<user.id>:<session.id>` (suggested), `user.id` the `sha256(Principal.subject)[:32]` hash the rate limit uses. Only the latest checkpoint per session is kept (suggested).
- `spec.memory` keys (suggested): `enabled` (false), `ttl_s` (86400), `max_bytes` (262144); a write past the cap is a clear tool error. With auth off in `cloud`, the config loader refuses `enabled: true` (suggested; `platform-security` to confirm); `fake` and `local` allow it.
- The idempotency fingerprint gains `session.id` when session memory is on (suggested; a `spec.idempotency` touch), so two sessions with the same input and client key never replay each other's answer.
- The template: a `BaseCheckpointSaver` over the three tools, opt-in (suggested: `agentctl new --memory session`), with one constant `thread_id`; `InMemorySaver` stays tests-only.
- Measured in the `sidecar` lane on the loopback proxy: the saver's cost per super-step (a `put` and a `put_writes`); ADR-014's Revisit reopens item 4 above 10 ms p50 (suggested) or 20% of the lookup task's latency.

## Out of scope

- A pause or resume in a run (ADR-015 item 5, a later ADR).
- Long-term memory across sessions (125 L-1) and the orchestrator's memory and checkpoints (113 O-9, 100 O-2).
- The platform MCP server's memory tools on `/v1/mcp` (114 P-2b): the orchestrator's, for outside clients.

## Acceptance criteria

- [ ] The three memory tools are on the workload's tool proxy, behind a port with an in-memory fake, and the fake and the Valkey adapter pass one contract suite: a principal cannot read or write another principal's session, a write past the cap is refused, the TTL holds.
- [x] The idempotency fingerprint includes `session.id` when session memory is on; a test shows two sessions with the same input and client key get their own answers. · Delivered by: `packages/chassis/tests/test_idempotency_session.py::test_the_fingerprint_includes_the_session_id`, `::test_with_memory_on_two_sessions_get_their_own_answers`, `::test_with_memory_off_the_session_header_does_not_scope_the_key`, `::test_the_fingerprint_with_no_session_is_byte_for_byte_as_before`, `::test_with_memory_on_and_no_usable_session_the_key_is_unscoped`, `::test_with_memory_on_two_sessions_under_one_key_get_their_own_run_key`, `::test_the_run_key_with_no_session_is_byte_for_byte_as_before` (the session also scopes the store key and the tool run key; suggested)
- [x] With auth off in `cloud`, `spec.memory.enabled: true` is refused at config load; `local` allows it. · Delivered by: `packages/chassis/tests/test_memory_config.py::test_cloud_with_auth_off_refuses_memory_enabled_at_config_load`, `::test_local_and_fake_with_auth_off_allow_memory`, `::test_cloud_with_a_real_authenticator_allows_memory`, `::test_memory_defaults_are_off_one_day_and_256_kib` (`spec.memory` in `chassis.server.config.MemorySpec`; the refusal is suggested, `platform-security` to confirm)
- [ ] The template renders the saver on opt-in; a rendered LangGraph agent keeps a thread across two runs with the same `x-session-id` and not across two sessions; the lane contract still passes.
- [ ] The saver's cost per super-step is measured in the `sidecar` lane and recorded against ADR-014's suggested 10 ms p50, in a dated note.
- [ ] Text a memory tool returns passes the `tool_output` guard stage, and a test shows a planted instruction in a saved memory is flagged (from [132 CH-13](132-CH-13-owasp-agentic-map.md), ASI06).

## Dependencies

- Depends on: [128 CH-9](128-CH-9-langgraph-checkpointer-interrupts.md), [054 H-16](054-H-16-tool-port.md)
- Blocks: none

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [F.1](../slm-agent-platform-epic-v3.md#f1) · [B.5](../slm-agent-platform-epic-v3.md#b5)
- Backlog plan: [000-plan.md](000-plan.md)
