---
title: "CH-11: LangSmith feedback adapter behind FeedbackPort"
labels: ["story", "priority:P1", "phase:0-harness", "area:harness", "size:S", "layer:chassis"]
milestone: "W5 Chassis completion"
index: 130
epic_id: CH-11
depends_on: ["129 CH-10"]
blocks: []
epic_refs: [G.2]
---

## Why

Langfuse is the only `FeedbackPort` backend today (the [product plan](../../plans/2026-11-06-agent-container-product.md), gap P1, phase D). Once traces go to LangSmith (129 CH-10), a score must land where the trace is. [ADR-009](../adr/009-feedback-langfuse-scores-event.md) item 3a (amended 2026-11-06) adds a `langsmith` adapter for that.

## What

- A `langsmith` adapter behind `FeedbackPort` (ADR-009 item 3a) writes a score onto the run's LangSmith trace.
- It binds the `FeedbackPort` contract suite with the fake and the Langfuse adapter.
- Where the score goes when both backends run follows 001 DEC-1.

## Out of scope

- A reviewer role and comment redaction (ADR-009 open questions, the user's). Subject binding is no longer open: contract v8 G binds feedback to the run's caller (`packages/chassis/tests/test_feedback_route.py::test_feedback_refuses_another_caller_with_404_when_the_run_ended_for_alice`).

## Acceptance criteria

- [ ] The adapter passes the `FeedbackPort` contract suite against `fake-langsmith`.
- [ ] `POST /v1/feedback` with the adapter selected writes one score that shows on the run's trace.
- [ ] The adapter is picked by `spec.adapters` only, and the key stays in the chassis.
- [ ] With both backends on, the score goes where 001 DEC-1 says, and a test shows it.

## Dependencies

- Depends on: [129 CH-10](129-CH-10-langsmith-trace-backend.md)
- Blocks: none

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.2](../slm-agent-platform-epic-v3.md#g2)
- Backlog plan: [000-plan.md](000-plan.md)
