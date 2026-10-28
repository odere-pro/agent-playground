# ADR-007: Observability: OpenTelemetry through one Collector, into Langfuse and a second backend

- **Date:** 2026-10-28
- **Deciders:** Oleksandr (epic owner) and the delivery team
- **Format:** Michael Nygard's template: Status, Context, Decision, Consequences
- **Related:** [ADR-001](001-chassis-delivery-model.md) (lanes, item 6: proxies keyed by `traceparent`), [ADR-006](006-agent-engines-default-supported-lanes.md) (criterion 10, instrumentors per engine), [ADR-008](008-security-identity-guardrails-defense-in-depth.md) (log redaction), [ADR-009](009-feedback-langfuse-scores-event.md) (feedback on the trace), [PoC-7](../poc/007-PoC-7-cross-cutting-decisions.md), [reuse analysis](../poc/010-reuse-analysis.md), [contract v6](../../contracts/contract-v6.md), [014 H-7](../issues/014-H-7-observability.md), [022 H-6](../issues/022-H-6-security-middleware.md), [026 CH-4](../issues/026-CH-4-chassis-only-credentials-egress.md)

## Status

Proposed, 2026-10-28. The user accepts it at the PoC-7 pull request.

Evidence that is not in yet:

- **The Collector, Langfuse, and Jaeger run only in CI or on the Mac.** The Docker registries need auth in the build container, so the stack in `deploy/compose/poc07/` runs in `poc07-stack.yml` or on the Mac. Every offline check uses the in-memory span exporter.
- EVIDENCE: the run of `poc07-stack.yml` that shows one trace in Langfuse and in Jaeger, with its run id.
- EVIDENCE: the test that joins the chassis and the workload spans in one trace over A2A, in `inprocess` and `sidecar`.

## Context

The epic asks for logs, metrics, and traces for every call, and LLM tracing in Langfuse ([G.2](../slm-agent-platform-epic-v3.md#g2)). ADR-001 splits each agent into two containers, so one trace must join both. The forces:

- **One trace across two processes and two lanes.** The chassis span, the engine's steps in the workload, the model call through LiteLLM, and the tool calls through the MCP gateway must share one trace id. The chassis already makes one `traceparent` per run and sends it over A2A as a header and in `ctx` (contract v1). Every engine forwards it on its model and MCP calls, because the proxies key budgets on it (ADR-001 item 6).
- **Framework depth lives in the workload.** Token deltas, graph steps, and handoffs can be read only in the framework's own process. The chassis imports no framework.
- **Swappable backend.** The PoC-7 exit criterion asks for the same spans in Langfuse and a second backend by exporter config only.
- **No product SDK in the chassis core.** `chassis.core` never imports OpenTelemetry. It calls `TelemetryPort`.
- **Who the call is for.** User and session ids belong on the spans, so Langfuse can group a user's traces. They must not become `Context` fields: `context.v0.json` is frozen (contract v5, C.2).
- **The conventions are moving.** The OpenTelemetry GenAI conventions are still in "Development" status. OpenInference uses its own attribute names. Langfuse reads both.

Facts about the engines, from ADR-006 criterion 10: PydanticAI has an OpenInference instrumentor and native OpenTelemetry. The OpenAI Agents SDK has an instrumentor. LangGraph has one under the LangChain name. Plain Python has none, so it instruments its HTTP client.

Options considered:

| # | Option | In short | Verdict |
| - | ------ | -------- | ------- |
| O1 | OTLP from the chassis and the workload to one Collector; the Collector fans out to Langfuse v4 and the second backend | One endpoint per pod; backends and their credentials live in the Collector | **Chosen** |
| O2 | Each process exports straight to Langfuse and to the second backend | Two exporters in every process, and a Langfuse key in every pod. A backend swap is a chassis config change in every service. The workload would hold a credential, which ADR-001 hard requirement 1 forbids | Rejected |
| O3 | The Langfuse SDK in the chassis and the workload | A product SDK in the chassis; Langfuse becomes the only backend. The OTLP route does the same with a standard | Rejected |
| O4 | Arize Phoenix as the LLM trace backend | Elastic License 2.0, not OSI open source (reuse analysis, "Avoid") | Rejected |
| O5 | The chassis relays the workload's spans over A2A | A second telemetry channel inside the wire contract. It changes the A2A mapping, which is frozen in chassis mode | Rejected |
| O6 | Chassis spans only, no framework instrumentors | No engine steps in the trace. Fails the PoC-7 question | Rejected |

## Decision

**We choose O1: every process exports OTLP to one OpenTelemetry Collector, and the Collector sends the same spans to Langfuse v4 and to a second backend.**

1. **The real adapter is `OtelTelemetry`** behind `TelemetryPort` (`spec.adapters.telemetry: otel`). It uses the OpenTelemetry SDK and exports OTLP over HTTP. The fake stays `InMemoryTelemetry`. Both pass the same suite. The endpoint and headers come from the standard `OTEL_EXPORTER_OTLP_*` variables, not from `spec`.
2. **The run span sits on the request's W3C trace id.** The chassis continues a caller's `traceparent`. Otherwise the trace id is `trace_id_hex(ctx.trace_id)`, as today. The connector's run span is the parent id of the `traceparent` sent to the workload, so the workload's spans hang under it. The chassis adds spans per stage it owns: the run, each model and tool proxy call, each guard stage (ADR-008), and the evaluator gate (ADR-010). suggested: `chassis.*` names, as today; GenAI attributes only where they are stable enough to read.
3. **User and session ids travel as W3C baggage, not as `Context` fields.** The chassis drops any inbound `baggage` header from the caller. It sets its own: `user.id` is the caller's `Principal.subject` from `AuthPort` (ADR-008), and `session.id` is the value of the header named by `spec.observability.session_header` (suggested: `x-session-id`). The A2A connectors send `baggage` next to `traceparent` on every A2A request of the run. The chassis also sets both as attributes on its own spans. OPEN: whether `user.id` is the subject itself or a keyed hash of it, and whether an `untrusted` remote gets `user.id` at all (ADR-008 owns the answer).
4. **Where each hook lives:**

   | Hook | Lives in | Why there |
   | ---- | -------- | --------- |
   | Run, proxy, guard, and gate spans | The chassis (`OtelTelemetry`) | The chassis owns those stages in every lane |
   | Framework spans (agent steps, LLM calls, tool calls) | The workload: the OpenInference instrumentor for its framework | Only the framework's process can see them |
   | Baggage to span attributes in the workload | The template A2A server's telemetry setup (`workload_a2a.telemetry`, suggested), not each engine | One copy for every Python engine |
   | Model call spans and spend | LiteLLM's own OpenTelemetry callback | LiteLLM sees the provider call; the chassis sees only the proxy hop |
   | Fan-out, sampling, and log redaction | The OTel Collector | The backends and their credentials stay out of the pods |
   | Backend credentials | The Collector's environment | Hard requirement 1: no workload holds one |

5. **What each engine must expose.** Nothing new in its event mapping. In its process it must: forward `traceparent` and `baggage` on every model and MCP call (`traceparent` is already required by contract v1); run the OpenInference instrumentor for its framework, or native OpenTelemetry; export OTLP to the Collector endpoint it is given; and switch off any vendor trace export (the OpenAI Agents SDK's own tracing, as in PoC-6). The list per engine is in contract v6, part F. EVIDENCE: the W1 table that shows each of the two PoC-7 engines in one trace.
6. **The second backend is Jaeger v2 in PoC-7** (`deploy/compose/poc07/`). It proves the swap. Tempo stays the pick for production in 014 H-7. The swap is the Collector's exporter list only. EVIDENCE: the Collector config diff and the two screenshots or API reads.
7. **Workload logs are redacted in the Collector's log pipeline.** The chassis redacts its own logs and events with regex (`chassis.redaction`, ADR-008). OPEN: named-entity redaction in the Collector. The contrib `redaction` processor matches patterns; names need a Presidio call from the pipeline or a custom processor. If neither is ready in PoC-8, names in workload logs are a recorded gap with an owner.
8. **Sampling.** The chassis marks every run sampled today (`traceparent` flags `01`). suggested: sample in the Collector (tail sampling), so one decision covers the chassis, the workload, and LiteLLM. The epic's `observability.trace_sample_rate` maps to that policy. OPEN: head or tail sampling, decided in PoC-8 with the volume numbers.
9. **The egress rule.** The workload pod may reach the Collector's OTLP port and nothing else of the telemetry stack. That rule belongs to 026 CH-4. OPEN: what an `untrusted` remote may send. suggested: a remote exports to a separate Collector receiver that requires the remote's token and marks its spans as untrusted data, or it exports nothing and the trace holds only the chassis and LiteLLM spans for that remote.

## Consequences

Easier:

- One trace from the client to the model call, for every engine that has an instrumentor, with no engine-specific code in the chassis.
- A backend swap is a Collector change. No service is rebuilt or restarted.
- Feedback (ADR-009) lands on the same trace id the caller already knows.
- Langfuse gets user and session grouping from attributes it reads.

Harder:

- One more service to run and secure. If the Collector is down, spans are dropped after the SDK's queue fills. The answer is never delayed by telemetry.
- Baggage goes to every downstream HTTP call the instrumentation touches, including LiteLLM and a `remote` workload. A user id in baggage is personal data in transit. ADR-008 decides its form.
- Attribute names will move while the GenAI conventions are in development. OpenInference names are the ones the workloads emit. A rename reaches dashboards, not the contract.
- Plain Python and the TypeScript agent have no framework instrumentor. They show only HTTP client spans and their own manual spans.

Costs:

- The OpenTelemetry SDK in the chassis image and in every workload image. EVIDENCE: the image size and idle RSS delta of the chassis with `otel` against `memory`.
- Per run: one span export batch from each container. EVIDENCE: the p50 latency delta of `/v1/run` with `otel` on, on the fake model.
- In every lane:

  | Lane | Cost |
  | ---- | ---- |
  | `inprocess` | The in-memory exporter. The `baggage` header rides the ASGI transport. No new hop |
  | `sidecar` | The workload exports to the Collector over the pod network. About 100 bytes more per A2A request for `baggage` (suggested estimate) |
  | `remote` | The same header over the network. Whether the remote's spans are accepted is OPEN (item 9) |

## Revisit

Reopen this decision on any of these:

- **The trace does not join.** If an engine on the supported list cannot put its spans under the run span through `traceparent` over A2A, it is listed with the reason, and its mapping or telemetry setup changes. If two or more engines fail, reopen O5.
- **The Collector costs too much.** If the Collector's CPU or memory per pod-equivalent exceeds the chassis sidecar's own reservation (ADR-001, suggested: 0.05–0.1 vCPU), reopen O2 for the chassis only.
- **Langfuse's self-hosted edition loses a needed feature** (the reuse analysis watch-out on roles and audit), or its licence changes.
- **The GenAI conventions become stable.** Then move the chassis attributes to them and record the rename.

## Sources

- OpenInference: <https://github.com/Arize-ai/openinference>
- Langfuse and OpenTelemetry: <https://langfuse.com/integrations/native/opentelemetry>
- W3C Baggage: <https://www.w3.org/TR/baggage/>
- OpenTelemetry Collector: <https://opentelemetry.io/docs/collector/>
