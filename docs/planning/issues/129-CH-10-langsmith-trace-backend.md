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

- [x] `deploy` holds the exporter behind a profile switch; the default stack runs without it. · Delivered by: Offline, 2026-11-16: `packages/chassis/tests/test_collector_langsmith_switch.py::test_docker_compose_renders_the_default_stack_with_no_langsmith_key`, `::test_default_collector_config_has_no_langsmith_exporter_or_variable`, `::test_switch_sends_traces_to_langfuse_jaeger_and_langsmith`, `::test_langsmith_exporter_reads_its_key_from_the_environment_only`, `::test_docker_compose_renders_the_switch_with_the_key_in_the_collector_only`, `::test_stack_sh_refuses_an_endpoint_off_the_allow_list_before_any_docker_call`. `uv run pytest packages/chassis/tests/test_collector_langsmith_switch.py --disable-socket -q` gives `18 passed`. The switch is the `--langsmith` flag of `deploy/compose/poc07/stack.sh`, which adds `deploy/compose/poc07/docker-compose.langsmith.yaml` and `deploy/compose/poc07/otel-collector.langsmith.yaml`. No live Collector run yet.
  - OPEN: LangSmith key scope not checked. Until it is, use a service key in its own workspace, holding only this project.
- [x] `fake-langsmith` (a package beside `fake-langfuse`) records an OTLP trace, and an offline test reads it back · Delivered by: `uv run pytest packages/fake-langsmith -q` gives `8 passed`; `packages/fake-langsmith/tests/test_fake_langsmith_server.py` posts OTLP protobuf and OTLP/JSON (hex ids) and reads the spans back by trace id, parent links included.
- [ ] A stack test: one run is one trace in `fake-langsmith`, with the graph steps under the run span.
- [ ] A stack test: the trace key `LANGSMITH_API_KEY` is in the Collector only; the workload and the logs hold none; the chassis holds only the 130 CH-11 feedback key.
- [x] The primary trace view per environment is written where 001 DEC-1 records it. · Delivered by: [001 DEC-1](001-DEC-1-resolve-open-decisions.md#decisions), decision 5 (2026-11-15), and the 2026-11-15 amendments to `docs/planning/adr/007-observability-otel-collector-langfuse.md` and `docs/planning/adr/009-feedback-langfuse-scores-event.md`.

## Dependencies

- Depends on: [014 H-7](014-H-7-observability.md)
- Blocks: [130 CH-11](130-CH-11-langsmith-feedback-adapter.md), [131 CH-12](131-CH-12-golden-cases-datasets.md)

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.2](../slm-agent-platform-epic-v3.md#g2)
- Backlog plan: [000-plan.md](000-plan.md)
