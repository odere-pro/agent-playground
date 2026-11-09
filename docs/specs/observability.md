# Observability

What the container records, and where it goes. Why: [ADR-007](../planning/adr/007-observability-otel-collector-langfuse.md) (one Collector, the backends, baggage), [ADR-009](../planning/adr/009-feedback-langfuse-scores-event.md) (feedback on the trace).

## The trace model

- The chassis and the workload export OTLP to one OpenTelemetry Collector. The Collector redacts and fans out to the backends. The backends' keys live in the Collector's environment, never in a pod.
- The run continues the caller's `traceparent`. Otherwise the trace id is `trace_id_hex(ctx.trace_id)`. The connector's run span is the parent of the `traceparent` sent to the workload, so the workload's spans hang under it, in every lane.
- The workload runs the OpenInference instrumentor for its framework, or native OpenTelemetry, and forwards `traceparent` and `baggage` on every model and MCP call. LiteLLM adds its own span under the model call. The chassis imports no framework.
- Baggage carries `user.id` (`sha256(subject)[:32]` of the caller's subject from `AuthPort`, in every lane; P-27, CH-14) and `session.id` (from the header `spec.observability.session_header`, suggested `x-session-id`). An inbound `baggage` header from the caller is dropped.
- `TelemetryPort` is `OtelTelemetry` in `local` and `cloud` (`chassis/adapters/otel/`, configured by `OTEL_EXPORTER_OTLP_*`) and `InMemoryTelemetry` in `fake`.

## Spans

| Span | Attributes |
| ---- | ---------- |
| `chassis.run` | `request_id`, `trace_id`, `agent`, `interface`, `route`, `task` (when `spec.model.routes` named it), `user.id`, `session.id` |
| `chassis.engine.run` | `lane`, `request_id`, `trace_id` |
| `chassis.model.call` | `format`, `route`, `trace_id`, `request_id`, `agent`, `correlated` |
| `chassis.model.route` | the above plus `route.requested`, `route.forced` |
| `chassis.tool.call` | `tool`, `trace_id`, `request_id` |
| `chassis.evaluator.score` | `evaluator`, `mode`, `attempt`, `evaluator.score`, `evaluator.passed`, `evaluator.threshold`, `evaluator.latency_ms` |
| `chassis.feedback` | `request_id`, `feedback_id`, `trace_id` |
| `chassis.idempotent_replay` | |

Any span can carry `error.code`. The guard stages emit counters and logs, not a span.

## Metrics

`GET /metrics` on the public port serves the Prometheus text format (`chassis/metrics.py`, `chassis/server/observability.py`). Every sample is labeled `agent`.

| Metric | Labels |
| ------ | ------ |
| `chassis_requests_total` | `interface` |
| `chassis_run_duration_seconds` (histogram) | `interface`, `status` |
| `chassis_run_errors_total` | `interface`, `code` |
| `chassis_engine_run_duration_seconds` (histogram) | `lane`, `status` |
| `chassis_model_calls_total` | `route` |
| `chassis_model_call_duration_seconds` (histogram) | `route`, `status` |
| `chassis_model_tokens_total` | `route`, `direction` |
| `chassis_model_cost_microusd_total` | `route` |
| `chassis_tool_calls_total`, `chassis_tool_call_duration_seconds` | `tool` (`status` on the histogram) |
| `chassis_gate_runs_total` | `mode` |
| `chassis_gate_retries_total`, `chassis_gate_fallbacks_total` | |
| `chassis_evaluator_score_duration_seconds` (histogram) | `mode`, `status` |
| `chassis_feedback_total` | `label` |

Every refusal and failure has a counter: `guardrail_blocked{stage,reason}`, `guardrail_unavailable{stage}`, `auth_refused{code,path_class}`, `rate_limited{path_class}`, `output_url_masked`, `evaluator_calls{evaluator,mode,outcome}`, `gate_failed_open`, `evaluator_dropped`, `feedback_failed{code}`, `events.published|publish_failed{topic}`, `model_calls_uncorrelated|refused{route}`, `model_route_forced{route}`, `model_upstream_denied{route}`, `model_stream_timeout{route}`, `requests_refused{reason}`, `remote.auth_failed{reason}`, `config.reloaded|rejected{reason}|poll_failed{reason}`, `client_disconnected`, `trace_id_reminted`, and the `chassis.idempotency.*` family. The full list is `chassis/metrics.py`.

Model tokens and cost come from the proxy's usage and `spec.observability.prices`.

## Logs

One redacted JSON line per record, with `request_id` and `trace_id` inside a run, at `spec.observability.log_level` (`chassis/logs.py`). The workload side writes the same shape (`workload_a2a.logs`). The Collector's log pipeline masks emails, IBANs, card numbers, US SSNs, and phone numbers by regex.

## Dashboards

`python -m chassis.dashboards` writes one Grafana dashboard per agent to `deploy/dashboards/`, a 5-minute window, seven panels: requests by interface and status, latency p50 and p95, errors by code, model tokens by direction, model cost per hour, the evaluator gate fallback rate, user feedback by label. The container dashboard that shows the [key metrics](metrics.md) is specified there.

## Feedback

`POST /v1/feedback` on the public port, scope `agent:feedback`, mounted when `spec.feedback.enabled` and `spec.adapters.feedback` is not `none`.

- Body: `request_id` (required), `score` in [0, 1], `label` (at most 64 characters), `comment` (at most 2000), `feedback_id` (optional). Unknown fields are 422. The answer is 202 `{feedback_id, trace_id}`.
- The trace is found from `request_id` in `StatePort`, never from the body: one record per run, written at run end, kept `spec.feedback.lookup_ttl_s` (suggested: 7 days). A missing or expired record, or another agent's, is 404 `unknown_request`.
- Idempotent by `feedback_id`: a repeat is one record with the first timestamp; an id used for another request is 409 `feedback_id_conflict`. With no id, the chassis derives one from the `request_id` and the caller's subject.
- `FeedbackPort.submit` writes the score where the trace is: `langfuse` (`POST /api/public/scores`, Basic auth, the label in metadata). A `langsmith` adapter is decided (ADR-009 item 3a; the run found from the W3C trace id) and not built. Plain `httpx`, no SDK. The key is the chassis's.
- With `spec.feedback.events: true` (default `false`), the chassis publishes `agents.feedback.received.v1` (`packages/chassis/schemas/feedback.v0.json`) after a successful write, with the comment redacted.

## The backends

| Backend | Role | Where the key lives |
| ------- | ---- | ------------------- |
| Langfuse v4 | LLM traces, scores, datasets | the Collector (OTLP) and the chassis (scores) |
| LangSmith | LLM traces, feedback, datasets. Decided (ADR-007 item 10, ADR-009 item 3a), not built: no exporter and no adapter exist yet | the Collector (OTLP) and the chassis (feedback) |
| Jaeger v2, Tempo in production | the second trace backend | the Collector |

Which backends an environment lists is a deploy choice in the Collector's exporter list (`deploy/compose/poc07/otel-collector.yaml`). The workload never exports to a backend itself: `LANGSMITH_TRACING` stays unset, vendor trace export is switched off, and the egress rule stops a direct call. Sampling is a Collector policy, not a chassis setting.
