---
title: "CH-9: LangGraph checkpointer and interrupts: one ADR each"
labels: ["story", "priority:P2", "phase:0-harness", "area:harness", "size:S", "layer:chassis"]
milestone: "W5 Chassis completion"
index: 128
epic_id: CH-9
depends_on: ["058 CH-8"]
blocks: []
epic_refs: [F.1, B.5]
---

## Why

The default engine for new agents is LangGraph ([ADR-006](../adr/006-agent-engines-default-supported-lanes.md), amended 2026-11-06). A LangGraph agent keeps no state between runs today: it has no checkpointer. An interrupt has no place in the `handle` contract. The [product plan](../../plans/2026-11-06-agent-container-product.md) lists both as gap P3, owned by phase C. Each needs a decision before code, so this issue is two ADRs.

## What

- Two ADRs: where a LangGraph agent keeps state between runs (none, the store behind `StatePort`, or a LangGraph saver). The gap review of 2026-11-07 suggests a fourth: the chassis exposes the store as MCP tools scoped by `session.id`, writes keyed by the run, with LangGraph's saver over them in the template, and how an interrupt maps onto the `handle` contract (A2A `input-required`, or a refused feature).
- Each ADR closes one ADR-006 Revisit trigger. 100 O-2, 109 O-8, 095 P-4, and 125 L-1 cite them once they land.

## Out of scope

- Building a checkpointer store.
- The orchestrator's run state (100 O-2).

## Acceptance criteria

- [ ] The checkpointer ADR is accepted and linked from ADR-006.
- [ ] The interrupt ADR is accepted and linked from ADR-006 and the contract spec.
- [ ] The LangGraph template has a test for the chosen interrupt mapping, or a test that the chassis refuses an interrupt with a clear error.
- [ ] ADR-006's two Revisit triggers are closed or restated with a date.

## Dependencies

- Depends on: [058 CH-8](058-CH-8-framework-event-mappings.md)
- Blocks: none

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [F.1](../slm-agent-platform-epic-v3.md#f1) · [B.5](../slm-agent-platform-epic-v3.md#b5)
- Backlog plan: [000-plan.md](000-plan.md)
