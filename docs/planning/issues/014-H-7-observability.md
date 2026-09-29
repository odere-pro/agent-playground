---
title: "H-7: Observability: logs, traces, metrics, Langfuse"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:S", "layer:chassis"]
milestone: "W2 Chassis MVP"
index: 14
epic_id: H-7
depends_on: ["007 H-1", "012 H-3", "009 CH-1"]
blocks: ["016 H-9", "046 A-5", "056 CH-7", "086 R-10", "117 X-3"]
epic_refs: [G.2]
---

## Why

Every call must be traced, and the Phase 0 done-when says the echo agent appears in traces. Putting logs, metrics, and traces in the chassis gives every agent the same telemetry with no extra work. Under [ADR-001](../adr/001-chassis-delivery-model.md), each agent runs as two containers, the chassis and the workload, so one trace must join both. This issue also owns the `/metrics` endpoint.

## What

- Structured JSON logs with `request_id` and `trace_id` on every line, from both containers (chassis and workload), with the same `trace_id`; `log_level` from `spec.observability`.
- OpenTelemetry traces with a chassis span per request and per model call. A caller's `traceparent` is continued, and passed on into the workload over A2A (009 CH-1). The sample rate comes from `trace_sample_rate`.
- One trace joins the chassis spans, the workload's framework spans, and the model proxy's spans. The OpenInference instrumentors run in the workload process.
- The workload sends its spans and logs over OTLP. suggested: straight to the OTel Collector, which the pod's egress policy allows.
- Workload logs do not pass through the chassis, so the chassis cannot redact them. suggested: redaction in the OTel Collector log pipeline (gap (f) in the [backlog plan](000-plan.md#adr-001-follow-ups)).
- `GET /metrics` in Prometheus format: requests, latency, errors, tokens, cost, and fallback rate.
- LLM tracing in Langfuse: prompt, tokens, cost, and latency for each model call.
- A `TelemetryPort` adapter, so the core never calls OpenTelemetry directly.
- A default per-agent Grafana dashboard (G.2): requests, latency, errors, tokens, cost, and fallback rate.
- Docker Compose services for Prometheus, Grafana, Loki, Tempo, and Langfuse, so telemetry can be seen locally.

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

## Acceptance criteria

- [ ] Every log line from the echo agent is JSON with `request_id` and `trace_id`, from both the chassis and the workload container.
- [ ] One call shows as one trace in Tempo, with spans for the request and the model call.
- [ ] In the `sidecar` lane, spans from the chassis and the workload appear in one trace.
- [ ] A `traceparent` header from the caller is continued, not replaced.
- [ ] `GET /metrics` returns Prometheus metrics for requests, latency, errors, and tokens.
- [ ] Each model call appears in Langfuse with prompt, tokens, cost, and latency.
- [ ] The core package has no OpenTelemetry import; it uses `TelemetryPort`.
- [ ] Tests assert on spans with the in-memory span exporter, and switching the OpenTelemetry exporter needs config only.

## Dependencies

- Depends on: [007 H-1](007-H-1-harness-library-ports-envelope.md), [012 H-3](012-H-3-model-port.md), [009 CH-1](009-CH-1-engine-connectors-a2a.md)
- Blocks: [016 H-9](016-H-9-local-debug-profile.md), [046 A-5](046-A-5-agent-dashboard.md), [056 CH-7](056-CH-7-chassis-release-rings.md), [086 R-10](086-R-10-health-checks.md), [117 X-3](117-X-3-slos-alerts.md)

## References

- Epic story: [H-7 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.2](../slm-agent-platform-epic-v3.md#g2)
- Backlog plan: [000-plan.md](000-plan.md)
