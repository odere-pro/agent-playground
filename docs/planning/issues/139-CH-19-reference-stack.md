---
title: "CH-19: One reference stack: make platform-up on kind from deploy/helm, with the example agent as smoke test"
labels: ["story", "priority:P1", "phase:8-production", "area:infra", "size:L", "layer:platform"]
milestone: "W10 Production readiness"
index: 139
epic_id: CH-19
depends_on: ["024 CH-3", "026 CH-4", "020 X-8"]
blocks: []
epic_refs: [G.4, H]
---

## Why

Each PoC has its own Compose or kind stack. Nothing brings up LiteLLM, the MCP gateway, Valkey, Kafka, the OTel Collector, a trace backend, and the example agent together. The chart's default chassis image is `kind.local/...:poc09`, not a published tag. The [product plan's gap review](../../plans/2026-11-06-agent-container-product.md#if-phases-a-to-f-are-all-built-what-is-still-missing) (2026-11-07) lists this under "Operating it": no phase owns it.

## What

- `make platform-up` on kind, from `deploy/helm`: LiteLLM, the MCP gateway, Valkey, Kafka, the OTel Collector, a trace backend, and the example agent. `make platform-down` removes it.
- The example agent's demo is the smoke test.
- The chart's default chassis image is a published tag.
- It reuses the stacks that exist: `deploy/kind/poc05/`, `deploy/kind/poc06/`, `deploy/compose/poc07/`, and `deploy/helm/`.

## Out of scope

- The first cloud (038 X-1a).
- Runbooks for an operator (119 X-6).

## Acceptance criteria

- [ ] `make platform-up` on a clean machine brings up all seven parts on kind, and `make platform-down` removes them.
- [ ] The example agent's demo passes as the smoke test, recorded in a dated note.
- [ ] One trace from the demo shows the chassis, the workload, and the model call in the trace backend.
- [ ] The chart's default chassis image is a published tag, and a render test fails on a `kind.local` default.
- [ ] A workflow runs `make platform-up` and the smoke test by hand or on a schedule.

## Dependencies

- Depends on: [024 CH-3](024-CH-3-helm-library-chart.md), [026 CH-4](026-CH-4-chassis-only-credentials-egress.md), [020 X-8](020-X-8-event-broker.md)
- Blocks: none

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 8](../slm-agent-platform-epic-v3.md#phase-8-production-readiness)
- Epic context: [G.4](../slm-agent-platform-epic-v3.md#g4) · [H](../slm-agent-platform-epic-v3.md#app-h)
- Backlog plan: [000-plan.md](000-plan.md)
