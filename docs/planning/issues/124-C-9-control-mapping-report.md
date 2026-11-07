---
title: "C-9: Control mapping report: ISO 42001 Annex A and AI Act articles to platform evidence"
labels: ["story", "priority:P1", "phase:9-governance", "area:governance", "size:S", "layer:platform"]
milestone: "W11 Compliance evidence"
index: 124
epic_id: C-9
depends_on: ["072 C-3", "123 C-4"]
blocks: []
epic_refs: [E.5, R12]
---

## Why

Auditors ask how each control is met and where the evidence is. The control mapping report answers that from the platform itself: ISO 42001 Annex A controls and AI Act articles, mapped to platform features and live evidence, ready for the organization's Statement of Applicability. It is the last issue of Phase 9, so it also carries the Phase 9 done-when check.

## What

- A report built from the E.5 control mapping: each requirement, the platform feature, and the evidence produced.
- Links to live evidence per agent version and time range: registry export and inventory report, impact assessment files, MLflow runs and model cards, dataset lineage, documentation pack, config history, audit log export, eval reports, monitoring dashboards, and incident records.
- Each control marked as covered by the platform, shared, or owned by the organization (for example Art. 17 quality management, which runs in the AI management system).
- The AI Act Art. 50(2) row is covered by the platform. Evidence: `docs/specs/contract.md` section 8, "AI-generated marking (`server/marking.py`, `adapters/ai_marking`)", and `docs/contracts/contract-v7.md`, "E. AI-generated marking"; the tests `packages/chassis/tests/test_marking.py::test_limited_marks_every_interface_streaming_and_complete`, `::test_marking_false_is_refused_for_limited_and_high`, `packages/chassis/tests/test_marking_body.py::test_the_openai_body_carries_the_flags_complete_and_on_every_chunk`, and `::test_result_events_carry_the_aigenerated_extension` (run 2026-11-06, passed). Its listed gap: no A2A route is marked (the public A2A endpoint is open, 061 H-5), and the native envelope and `/v1/mcp` carry the headers only, no body field.
- Gaps listed openly. Suggested first gap: the Art. 9 risk register per agent, which no story builds yet.
- A format ready for a Statement of Applicability (suggested: one row per control, with applicability and justification fields for the organization to fill).
- Generated on demand and stored with its date and scope.

## Reuse

- **Use:** buy Vanta or Drata (ISO 42001 frameworks) for the audit workflow and evidence tracking. VerifyWise (Business Source License, self-hosted) if the tool must be self-hosted.
- **Build:** the export of platform evidence into the chosen tool.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- The certification and the Statement of Applicability itself; the organization owns them (a non-goal of the epic).
- The documentation pack (123 C-4).
- Legal classification of use cases.

## Acceptance criteria

- [ ] The report covers every row of the E.5 table.
- [ ] For a chosen agent version, every evidence link opens the right item.
- [ ] Controls with no platform evidence are marked as organization-owned, not left blank.
- [ ] The compliance owner reviews the report and signs off the format.
- [ ] The Phase 9 done-when check passes: activating an agent without a `governance` block is refused, a `high` risk agent without an impact assessment and oversight is refused, and an audit export and a documentation pack are produced for one agent version.

## Dependencies

- Depends on: [072 C-3](072-C-3-audit-log-agent.md), [123 C-4](123-C-4-documentation-pack.md)
- Blocks: none

## References

- Epic story: [C-9 in Phase 9](../slm-agent-platform-epic-v3.md#phase-9-governance-and-compliance)
- Epic context: [E.5](../slm-agent-platform-epic-v3.md#e5) · [R12](../slm-agent-platform-epic-v3.md#r12)
- Backlog plan: [000-plan.md](000-plan.md)
