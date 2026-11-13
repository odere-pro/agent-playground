---
title: "C-5: AI-generated marking on outputs and a disclosure flag for client apps"
labels: ["story", "priority:P1", "phase:9-governance", "area:governance", "size:S", "layer:agent-profile"]
milestone: "W2 Chassis MVP"
index: 23
epic_id: C-5
depends_on: ["010 H-12", "011 H-2", "019 H-17"]
blocks: []
epic_refs: [E.2, E.4, E.5, R10]
---

## Why

Agents with `limited` risk and above must mark every output as AI-generated (E.4), and client apps need a flag to disclose it (AI Act Art. 50). This is pulled forward from Phase 9: it is cheap to add in the chassis now, and AI Act Art. 50(2) marking applies to systems already on the market from 2 December 2026. Putting it in the chassis gives every agent the marking with no extra work. Every response leaves through the chassis ([ADR-001](../adr/001-chassis-delivery-model.md)), so the marking holds in every lane and the workload cannot remove it.

## What

- Decided in [ADR-008](../adr/008-security-identity-guardrails-defense-in-depth.md) and [contract v7](../../contracts/contract-v7.md#e-ai-generated-marking-the-rest-of-023-c-5) E, the marking is:
  - the `X-AI-Generated` and `X-AI-Disclosure-Required` headers on every public interface, complete and streaming, because `Response` is frozen (contract v5, part C);
  - the `ai_marking` body field in the OpenAI and Anthropic formats, complete and on every chunk;
  - the `aigenerated` CloudEvent extension on result events, so stored records keep it;
  - `Access-Control-Expose-Headers` for the two flags, so browser clients can read them.
- The chassis sets the marking on the way out, after the workload's events come back. Whatever the workload sends in those headers or that field is overwritten.
- Marking on when `governance.risk_class` is `limited` or `high`, or when `governance.ai_generated_marking` is `true`.
- A config check that refuses `ai_generated_marking: false` for `limited` and `high` agents.
- A short guide for client app teams: how to read the flags and show a disclosure. It is due before the AI Act Art. 50(2) date, 2026-12-02 (phase A, decided 2026-11-06).
- `CloudEvent` has `extra="forbid"` (`packages/chassis/src/chassis/ports/events.py`), so a consumer on a chassis older than PoC-8 rejects an event with `aigenerated`. Accept and drop unknown extensions from now on, or roll consumers out before producers for every new extension: 001 DEC-1 decides. suggested: accept and drop them.

## Out of scope

- Enforcing the governance block on activation (077 C-1).
- Instructions for use in the documentation pack (123 C-4).
- Legal classification of each use case: legal decides, and the platform records it.
- The `remote` lane (055 CH-6). It gets the same marking, because its responses leave through the same chassis.

## Acceptance criteria

- [x] An agent with `risk_class: limited` marks every output through all three APIs, streaming and complete: the two headers everywhere, and the `ai_marking` body field in OpenAI and Anthropic. · Delivered in PoC-7, PoC-8: `packages/chassis/tests/test_marking.py::test_limited_marks_every_interface_streaming_and_complete`, `packages/chassis/tests/test_marking_body.py::test_the_openai_body_carries_the_flags_complete_and_on_every_chunk`, `::test_the_anthropic_body_carries_the_flags_complete_and_on_message_start`, [contract v7 E.1](../../contracts/contract-v7.md#e1-the-body-field)
- [x] A `limited` or `high` agent with `ai_generated_marking: false` is refused at config load. · Delivered in PoC-7: `packages/chassis/tests/test_marking.py::test_marking_false_is_refused_for_limited_and_high`
- [x] A `minimal` agent with `ai_generated_marking: true` marks its outputs; with `false`, it does not. · Delivered in PoC-7: `packages/chassis/tests/test_marking.py::test_minimal_marks_only_when_asked`
- [x] Responses carry the disclosure flag (`X-AI-Disclosure-Required`), and the client guide is published. · Delivered in PoC-7: `packages/chassis/tests/test_marking.py::test_limited_marks_every_interface_streaming_and_complete`; guide: [show that an answer is AI-generated](../../guides/client-app-ai-disclosure.md), 2026-11-09, ahead of 2026-12-02
- [x] Result events carry the same marking as the `aigenerated` CloudEvent extension. · Delivered in PoC-8: `packages/chassis/tests/test_marking_body.py::test_result_events_carry_the_aigenerated_extension`, [contract v7 E.2](../../contracts/contract-v7.md#e2-the-cloudevent-extension)
- [ ] A contract test checks the marking on every endpoint.
- [x] The marking holds in the `inprocess` and `sidecar` lanes, with the same flags for the same input. · Delivered in PoC-7: `packages/chassis/tests/test_marking.py::test_inprocess_and_sidecar_give_the_same_flags`
- [ ] A test workload that sends its own marking, in the headers or in the `ai_marking` body field, still gets the chassis's marking on a `limited` agent's output, because the chassis sets it on the way out.
- [ ] A consumer on a chassis older than PoC-8 reads an event with `aigenerated`.

## Dependencies

- Depends on: [010 H-12](010-H-12-config-loader.md), [011 H-2](011-H-2-inbound-adapters.md), [019 H-17](019-H-17-event-port.md)
- Blocks: none

## References

- Epic story: [C-5 in Phase 9](../slm-agent-platform-epic-v3.md#phase-9-governance-and-compliance)
- Epic context: [E.2](../slm-agent-platform-epic-v3.md#e2) · [E.4](../slm-agent-platform-epic-v3.md#e4) · [E.5](../slm-agent-platform-epic-v3.md#e5) · [R10](../slm-agent-platform-epic-v3.md#r10)
- Backlog plan: [000-plan.md](000-plan.md)
