---
title: "H-7: Observability: logs, traces, metrics, Langfuse"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:M", "layer:chassis"]
milestone: "W2 Chassis MVP"
index: 14
epic_id: H-7
depends_on: ["007 H-1", "009 CH-1"]
blocks: ["046 A-5", "056 CH-7", "086 R-10", "117 X-3", "129 CH-10"]
epic_refs: [G.2]
---

## Why

Every call must be traced, and the Phase 0 done-when says the echo agent appears in traces. Putting logs, metrics, and traces in the chassis gives every agent the same telemetry with no extra work. Under [ADR-001](../adr/001-chassis-delivery-model.md), each agent runs as two containers, the chassis and the workload, so one trace must join both. This issue also owns the `/metrics` endpoint.

## What

- Decided in [ADR-007](../adr/007-observability-otel-collector-langfuse.md) (accepted 2026-10-30; item 10 amended 2026-11-06), written in [contract v6](../../contracts/contract-v6.md) and [contract v7](../../contracts/contract-v7.md) part A.
- Structured JSON logs with `request_id` and `trace_id` on every line, from both containers (chassis and workload), with the same `trace_id`; `log_level` from `spec.observability`. Built in PoC-8: one redacted JSON log line per chassis record; `workload_a2a.logs` for every Python workload and `src/log.ts` for `echo-typescript`, with the chassis's field names. There is no redaction in the workload.
- OpenTelemetry traces with a chassis span per request and per model call. A caller's `traceparent` is continued, and passed on into the workload over A2A (009 CH-1). Built in PoC-7: `OtelTelemetry` (OTLP over HTTP) behind `TelemetryPort`, with the run span on the request's W3C trace id. The chassis sends `traceparent` to LiteLLM, so its spans join the run's trace.
- One trace joins the chassis spans, the workload's framework spans, and the model proxy's spans. The OpenInference instrumentors run in the workload process. The per-engine telemetry list is in contract v6, F.3. The TypeScript twin of `workload_a2a.telemetry` is not built.
- User and session ids travel as W3C `baggage` over A2A (`user.id`, the authenticated subject, and `session.id`), never as `Context` fields. Caller baggage is never forwarded.
- Every process exports over OTLP to one OpenTelemetry Collector, which fans out to Langfuse v4 and a trace backend: Jaeger in CI, Tempo in production. Switching a backend is the Collector's exporter list only.
- Sampling is a Collector policy (ADR-007 item 8). Today the chassis marks every run sampled. The epic's `trace_sample_rate` maps to that policy.
- Workload logs do not pass through the chassis, so the chassis cannot redact them. Built in PoC-8: regex masking in the Collector log pipeline (emails, IBANs, cards, SSNs, phones).
- `GET /metrics` in Prometheus format: requests, latency, errors, tokens, cost, and fallback rate, with every sample labelled `agent`.
- LLM tracing in Langfuse: prompt, tokens, cost, and latency for each model call.
- A `TelemetryPort` adapter, so the core never calls OpenTelemetry directly.
- A default per-agent Grafana dashboard (G.2): requests, latency, errors, tokens, cost, and fallback rate. Built in PoC-8: one dashboard per agent in `deploy/dashboards/` (`python -m chassis.dashboards`).
- Compose stacks in the ADR-007 shape, so telemetry can be seen locally: the Collector, Langfuse v4, and Jaeger (`deploy/compose/poc07`); Prometheus and Grafana (`deploy/compose/poc08`); LiteLLM's span in the run's trace (`deploy/compose/poc08-litellm`). Both PoC-8 stack drills passed in CI ([stack drills](../../../pocs/poc-08-cross-cutting-build/notes/2026-10-30-stack-drills.md), run 41011333394).
- Sent to 001 DEC-1 on 2026-11-06, as user decisions: whether `/metrics` stays open with no token when `spec.observability.metrics_scope` is unset (PoC-8 Q1); whether the feedback panel stays on the dashboard (Q4); Presidio for named entities in the Collector log pipeline, or the regex-only gap for workload logs (Q6); and whether a `remote` workload's spans are accepted (ADR-007).

## Reuse

- **Use:** the OpenTelemetry SDK with OpenInference instrumentors (they cover PydanticAI, LangGraph, OpenAI Agents SDK, Claude Agent SDK, smolagents, Google ADK, LiteLLM, and MCP), an OTel Collector, Langfuse v4 for LLM traces (`/api/public/otel`, HTTP only), and Prometheus, Grafana, Loki, and Tempo for the rest.
- **Build:** chassis spans, trace context passed to the workload over A2A so one trace covers both containers, the JSON log format with `request_id` and `trace_id`, and user and session IDs on spans through OTel baggage. In the `sidecar` lane, the OpenInference instrumentors run in the workload process.
- **Watch:** the OTel GenAI conventions are still in "Development" status. OpenInference uses its own attribute names; Langfuse reads both.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- PII redaction before logging in the chassis (022 H-6).
- The egress policy that lets the workload reach the OTel Collector (026 CH-4).
- The simplifier's own dashboard (046 A-5).
- SLOs and alerts (117 X-3).
- LangSmith as a trace backend behind the Collector (ADR-007 item 10): phase D, in 129 CH-10.

## Acceptance criteria

- [x] Every log line from the echo agent is JSON with `request_id` and `trace_id`, from both the chassis and the workload container. · Delivered in PoC-8: `packages/chassis/tests/test_json_logs.py::test_inside_a_run_a_line_carries_the_request_id_and_a_32_hex_trace_id`; `pocs/poc-08-cross-cutting-build/tests/test_poc08_workload_logs.py::test_the_workload_logs_json_with_the_chassis_runs_trace_id`.
- [x] One call shows as one trace in the Collector's trace backend (Jaeger in CI, Tempo in production; Decided in ADR-007), with spans for the request and the model call. · Delivered in PoC-8: `pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_litellm.py::test_one_run_is_one_trace_with_a_litellm_span_under_the_model_proxy` (`network`); `pocs/poc-08-cross-cutting-build/notes/2026-10-30-stack-drills.md` (CI run 41011333394, passed).
- [x] In the `sidecar` lane, spans from the chassis and the workload appear in one trace. · Delivered in PoC-7: `pocs/poc-07-cross-cutting-decisions/tests/test_poc07_obs_trace_join.py::test_one_trace_joins_chassis_workload_framework_model_and_tool`; `pocs/poc-08-cross-cutting-build/tests/test_poc08_c3_trace.py::test_a_tool_call_has_one_trace_across_run_model_and_tool_spans`.
- [x] A `traceparent` header from the caller is continued, not replaced. · Delivered in PoC-7: `packages/chassis/tests/test_otel_telemetry.py::test_traceparent_continues_the_callers_trace`; `packages/chassis/tests/test_otel_wiring.py::test_run_span_continues_the_callers_traceparent_and_names_the_session`.
- [x] `GET /metrics` returns Prometheus metrics for requests, latency, errors, and tokens. · Delivered in PoC-8: `packages/chassis/tests/test_metrics.py::test_a_run_is_counted_by_interface_and_status_with_its_latency`, `::test_a_run_error_is_counted_by_code`, `::test_model_tokens_and_cost_come_from_the_proxy_usage`; [contract v7](../../contracts/contract-v7.md#a-get-metrics).
- [ ] Each model call appears in Langfuse with prompt, tokens, cost, and latency.
- [x] The core package has no OpenTelemetry import; it uses `TelemetryPort`. · Delivered in PoC-7: `pocs/poc-01-walking-skeleton/tests/test_day0.py::test_import_rules_hold` (`opentelemetry` is forbidden in `chassis.core` and `chassis.ports`, and allowed only under `chassis.adapters`, `pyproject.toml`).
- [x] Tests assert on spans with the in-memory span exporter, and switching the OpenTelemetry exporter needs config only. · Delivered in PoC-7: `pocs/poc-07-cross-cutting-decisions/tests/test_poc07_obs_telemetry_swap.py::test_switching_the_backend_is_exporter_config_only`; `packages/chassis/tests/test_otel_telemetry.py::test_traceparent_continues_the_callers_trace`.
- [ ] Trace sampling is a Collector policy (ADR-007 item 8), and `trace_sample_rate` sets it.
- [ ] The guard stage emits its own span under the run span.
- [ ] The OTel SDK's CPU and latency cost is measured on the PoC-8 stack and recorded in a dated note.

## Dependencies

- Depends on: [007 H-1](007-H-1-harness-library-ports-envelope.md), [009 CH-1](009-CH-1-engine-connectors-a2a.md)
- Blocks: [046 A-5](046-A-5-agent-dashboard.md), [056 CH-7](056-CH-7-chassis-release-rings.md), [086 R-10](086-R-10-health-checks.md), [117 X-3](117-X-3-slos-alerts.md), [129 CH-10](129-CH-10-langsmith-trace-backend.md)

## References

- Epic story: [H-7 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.2](../slm-agent-platform-epic-v3.md#g2)
- Backlog plan: [000-plan.md](000-plan.md)
