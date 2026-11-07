---
title: "CH-10: LangSmith as a trace backend: Collector exporter, fake-langsmith, stack tests"
labels: ["story", "priority:P1", "phase:0-harness", "area:harness", "size:M", "layer:chassis"]
milestone: "W5 Chassis completion"
index: 129
epic_id: CH-10
depends_on: ["014 H-7"]
blocks: ["130 CH-11", "131 CH-12"]
epic_refs: [G.2]
---

## Why

The [product plan](../../plans/2026-11-06-agent-container-product.md) puts LangChain, LangGraph, and LangSmith first. LangSmith is in no code or config today (gap P1, phase D). [ADR-007](../adr/007-observability-otel-collector-langfuse.md) item 10 (amended 2026-11-06) adds it as one more exporter behind the one Collector, with its key in the Collector only. This issue builds that exporter, a fake to test it offline, and the stack tests that prove the trace join and the key rule.

## What

- The Collector gets an `otlphttp/langsmith` exporter (ADR-007 item 10), with the LangSmith key only in the Collector.
- `fake-langsmith` takes OTLP and records traces for offline tests.
- Two stack tests show one run as one trace in LangSmith, with LangGraph's steps under the run span, and no LangSmith key in any other container or log.

## Out of scope

- The feedback adapter (130 CH-11).
- Datasets (131 CH-12).
- Choosing the primary trace view (001 DEC-1).

## Acceptance criteria

- [ ] `deploy` holds the exporter behind a profile switch; the default stack runs without it.
- [ ] `fake-langsmith` (a package beside `fake-langfuse`) records an OTLP trace, and an offline test reads it back.
- [ ] A stack test: one run is one trace in `fake-langsmith`, with the graph steps under the run span.
- [ ] A stack test: the LangSmith key is in the Collector only; the chassis, the workload, and the logs hold none.
- [ ] The primary trace view per environment is written where 001 DEC-1 records it.

## Dependencies

- Depends on: [014 H-7](014-H-7-observability.md)
- Blocks: [130 CH-11](130-CH-11-langsmith-feedback-adapter.md), [131 CH-12](131-CH-12-golden-cases-datasets.md)

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.2](../slm-agent-platform-epic-v3.md#g2)
- Backlog plan: [000-plan.md](000-plan.md)
