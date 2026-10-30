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

- An `ai_generated` flag in the output metadata of every response, in a machine-readable form.
- The chassis sets the flag on the way out, after the workload's events come back. Whatever the workload sends in that field is overwritten.
- Marking on when `governance.risk_class` is `limited` or `high`, or when `governance.ai_generated_marking` is `true`.
- A config check that refuses `ai_generated_marking: false` for `limited` and `high` agents.
- The flag in all three APIs, streaming and complete (suggested: a response header plus a metadata field for the OpenAI and Anthropic formats, which have no standard field for it).
- A disclosure flag for client apps that tells them to show the user they are dealing with AI (suggested name: `disclosure_required`).
- The same flag on result events, so stored records keep it.
- A short guide for client app teams: how to read the flags and show a disclosure.
- Proposed in PoC-7 ([ADR-008](../adr/008-security-identity-guardrails-defense-in-depth.md), [contract v6](../../contracts/contract-v6.md); the user accepts it at the PoC-7 pull request): the marking is two response headers, `X-AI-Generated` and `X-AI-Disclosure-Required`, on every public interface, complete and streaming, because `Response` is frozen (contract v5, part C). Still open here: a body field in the OpenAI and Anthropic formats, and the flag on result events (suggested: a CloudEvent extension attribute).
- Status after PoC-8 ([contract v7](../../contracts/contract-v7.md) part E): built: the `ai_marking` body field in the OpenAI and Anthropic formats, complete and every chunk; the CloudEvent extension `aigenerated` on result events; `Access-Control-Expose-Headers` for the two flags (`packages/chassis/tests/test_marking_body.py`). Add to acceptance: a consumer on a chassis older than PoC-8 must read an event with `aigenerated`; `CloudEvent` has `extra="forbid"`, so an older one rejects it. Open, user decision: (a) accept and drop unknown extensions in `CloudEvent` from now on, or (b) keep `forbid` and roll consumers out before producers for every new extension. suggested: (a). Still open from PoC-7: legal on disclosure for `minimal` with marking on.

## Out of scope

- Enforcing the governance block on activation (077 C-1).
- Instructions for use in the documentation pack (123 C-4).
- Legal classification of each use case: legal decides, and the platform records it.
- The `remote` lane (055 CH-6). It gets the same marking, because its responses leave through the same chassis.

## Acceptance criteria

- [ ] An agent with `risk_class: limited` returns `ai_generated: true` on every output, through all three APIs, streaming and complete.
- [ ] A `limited` or `high` agent with `ai_generated_marking: false` is refused at config load.
- [ ] A `minimal` agent with `ai_generated_marking: true` marks its outputs; with `false`, it does not.
- [ ] Responses carry the disclosure flag, and the client guide is published.
- [ ] Result events carry the same `ai_generated` flag.
- [ ] A contract test checks the marking on every endpoint.
- [ ] The marking holds in the `inprocess` and `sidecar` lanes, with the same flags for the same input.
- [ ] A test workload that sends `ai_generated: false` still gets `ai_generated: true` on a `limited` agent's output, because the chassis sets the flag on the way out.

## Dependencies

- Depends on: [010 H-12](010-H-12-config-loader.md), [011 H-2](011-H-2-inbound-adapters.md), [019 H-17](019-H-17-event-port.md)
- Blocks: none

## References

- Epic story: [C-5 in Phase 9](../slm-agent-platform-epic-v3.md#phase-9-governance-and-compliance)
- Epic context: [E.2](../slm-agent-platform-epic-v3.md#e2) · [E.4](../slm-agent-platform-epic-v3.md#e4) · [E.5](../slm-agent-platform-epic-v3.md#e5) · [R10](../slm-agent-platform-epic-v3.md#r10)
- Backlog plan: [000-plan.md](000-plan.md)
