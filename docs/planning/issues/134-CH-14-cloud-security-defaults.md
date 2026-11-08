---
title: "CH-14: Security defaults in the cloud profile: no uncorrelated tokens, unknown tools refused, auth required"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:M", "layer:chassis"]
milestone: "Done: delivered by the PoC track"
index: 134
epic_id: CH-14
depends_on: ["013 CH-2", "022 H-6"]
blocks: []
epic_refs: [G.3]
---

## Why

The [product plan's gap review](../../plans/2026-11-06-agent-container-product.md#if-phases-a-to-f-are-all-built-what-is-still-missing) (2026-11-07) found defaults in the `cloud` profile that a hijacked agent would use. Each one is small, mostly a profile value. The review ranks them first: they ship in one PR before a first production agent. The vault note `wiki/security/Agent security strategy.md` (least agency, deny by default) is the rule they follow.

## What

- `spec.limits.uncorrelated_tokens_per_minute: 0` in the `cloud` profile. Today the loopback model proxy serves 20k tokens a minute per replica to a call with no run (`packages/chassis/src/chassis/server/model_admission.py`), so a hijacked sidecar can spend the key with no run.
- A tool name that is not in the gateway's cached list. The adapter refreshes the list once; if there is still no definition, it answers `unknown_tool` before any request. Today the name skips the write-key and schema checks (`packages/chassis/src/chassis/adapters/mcp/gateway.py`, `if definition is not None`).
- The `cloud` profile refuses `auth: none` at load, as it already refuses `static`. Under `auth: none` the rate limit is off too. This part is built (PR #89).
- Three values the user decided on 2026-11-08, each option 1 in the [backlog review proposals](../../reviews/2026-11-06-backlog-review/proposals.md):
  - `/metrics` behind `agent:read` in `cloud` (`metrics_scope`): P-4;
  - `rate_limit_on_unavailable: fail_closed` in `cloud`: P-26, which reopens P-2 for `cloud` only;
  - `user.id` in baggage hashed with the rate limit's `sha256[:32]`, in every lane: P-27 (ADR-007 open question 1, ADR-008 open question 1).

## Out of scope

- The other security gaps of the same review: the LiteLLM key's expiry and rekey (026 CH-4), two keys in the workload token Secret (055 CH-6), image signatures at admission (056 CH-7), a pin on tool descriptions (054 H-16), and write tools off unless named, with an approval checkpoint (P-28).
- A cluster-wide uncorrelated cap (022 H-6). LiteLLM's key budget is the cluster cap.

## Acceptance criteria

- [x] With the `cloud` profile, a call on the loopback model proxy with no run in flight is refused with a fixed code (suggested: `uncorrelated_refused`), and the `local` and `fake` profiles still serve it. · Delivered by: `packages/chassis/tests/test_cloud_profile_defaults.py::test_cloud_refuses_an_uncorrelated_chat_call_before_the_model`, `::test_cloud_refuses_an_uncorrelated_messages_call_before_the_model`, `::test_fake_and_local_still_serve_an_uncorrelated_call`
- [x] A call to a tool name the gateway's list lacks gets one refresh, then `unknown_tool`, and the fake gateway sees no tool request. · Delivered by: `packages/chassis/tests/test_tool_gateway_contract.py::test_an_unlisted_name_gets_one_refresh_then_unknown_tool_and_no_tool_request`, `::test_an_unlisted_name_inside_the_cooldown_is_unknown_with_no_request`
- [x] The `cloud` profile refuses `auth: none` at load, with a test next to the `static` refusal. · Delivered by: PR #89, `packages/chassis/tests/test_profiles_cloud_transport.py::test_cloud_refuses_auth_none`, `::test_cloud_refuses_auth_static`
- [x] With the `cloud` profile and auth on, `/metrics` needs a token with `agent:read`. P-4, decided 2026-11-08. · Delivered by: `packages/chassis/tests/test_cloud_profile_defaults.py::test_cloud_metrics_need_a_token_with_agent_read`, `::test_fake_and_local_metrics_need_no_token_with_auth_on`
- [x] With the `cloud` profile, a rate-limit store outage refuses counted calls with 503 `state_unavailable`. P-26, decided 2026-11-08; `local` and `fake` stay `fail_open`. · Delivered by: `packages/chassis/tests/test_cloud_profile_defaults.py::test_cloud_refuses_a_counted_call_when_the_rate_limit_store_is_down`, `::test_fake_and_local_let_a_counted_call_through_an_outage`
- [x] `user.id` in baggage is the `sha256[:32]` of the subject. P-27, decided 2026-11-08, in every lane. · Delivered by: `packages/chassis/tests/test_security_auth.py::test_user_id_on_the_run_span_is_the_hash_of_the_callers_subject`
- [x] `docs/specs/security.md` lists each `cloud` default of this issue in one line. · Delivered by: [the security spec](../../specs/security.md), "The `cloud` profile defaults (CH-14)"

Delivered by: the CH-14 PR (2026-11-08). The `cloud` profile also refuses a document that weakens one of these defaults (`packages/chassis/tests/test_cloud_profile_defaults.py::test_cloud_refuses_a_document_that_weakens_a_default`).

## Dependencies

- Depends on: [013 CH-2](013-CH-2-outbound-model-proxy.md), [022 H-6](022-H-6-security-middleware.md)
- Blocks: none

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.3](../slm-agent-platform-epic-v3.md#g3)
- Backlog plan: [000-plan.md](000-plan.md)
