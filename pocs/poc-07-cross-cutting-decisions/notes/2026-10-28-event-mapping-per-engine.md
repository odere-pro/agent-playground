# What each engine must carry for observability (exit criterion 6, observability part)

Date: 2026-10-28. Work package W1. Evidence: `tests/test_poc07_obs_trace_join.py`.

## What lives where

- **Chassis, once for every engine.** `chassis.run` in the request's trace (a caller's `traceparent` is continued), `chassis.engine.run` (the A2A connector), `chassis.model.call` and `chassis.tool.call` continuing the workload's `traceparent`. `user.id` and `session.id` go on the run span and travel to the workload as W3C `baggage` next to `traceparent`. Adapter: `chassis.adapters.otel.OtelTelemetry`.
- **Template A2A server, once for every Python engine.** `workload_a2a.telemetry` (extra `workload-a2a[otel]`): the `workload.handle` span from the A2A `traceparent` and `baggage` headers (else `ctx["traceparent"]`), a span processor that copies `user.id` and `session.id` from baggage onto every workload span, and the request hook `inject_trace_headers`. Exporter: OTLP/HTTP from `OTEL_EXPORTER_OTLP_*`.
- **The engine's own wiring, per engine.** Two things only: register its OpenInference instrumentor (`workload_a2a.telemetry.register_instrumentor`) and put `inject_trace_headers` on its model and MCP HTTP clients. Neither touches the event mapping (`mapping.py`).

## Per engine

| Engine | Instrumentor (version) | One-line setup | traceparent and baggage on framework spans | Status, gaps |
| ------ | ---------------------- | -------------- | ------------------------------------------ | ------------ |
| echo-langgraph | `openinference-instrumentation-langchain` 0.1.80 | `LangChainInstrumentor().instrument(tracer_provider=tp)` in `echo_langgraph/telemetry.py` | Yes. Graph, chain, chat model, and tool spans are under `workload.handle` and carry `session.id`. | Run end to end. Gap: the instrumentor never makes its spans current (by design), so the model proxy span hangs off `workload.handle`, next to the LLM span, not under it. Same trace, correct ancestry. |
| echo-openai-agents | `openinference-instrumentation-openai-agents` 2.6.0 | `OpenAIAgentsInstrumentor().instrument(tracer_provider=tp, exclusive_processor=True)` plus `set_tracing_disabled(False)` in `echo_openai_agents/telemetry.py` | Yes. Agent, generation, and tool spans are under `workload.handle`; the processor makes each SDK span current, so the model call carries the generation span's `traceparent`. | Run end to end. The SDK's export to OpenAI stays off: the instrumentor is the only trace processor, and SDK tracing is on only while it is (`test_the_sdk_trace_export_is_off` still passes). |
| echo-pydanticai | `openinference-instrumentation-pydantic-ai` (suggested) or PydanticAI's own `instrument=True` (OTel native) | `Agent(..., instrument=InstrumentationSettings(tracer_provider=tp))` | Expected yes: PydanticAI emits OTel spans in the current context. | Not run: two engines were the target. Same two-line wiring. |
| echo-python | `openinference-instrumentation-openai` only if it moves to the `openai` client; today it calls `httpx` itself | none needed for spans; add `inject_trace_headers` to its `httpx` client | Its own calls only; it has no framework spans. | Not run. It does not depend on `workload-a2a`; it would need the extra. |
| echo-typescript | `@arizeai/openinference-instrumentation-openai` (JS) plus `@opentelemetry/sdk-node` | `registerInstrumentations(...)` at start; read `traceparent` and `baggage` in its A2A server | Needs its own copy of `workload_a2a.telemetry` in TypeScript. | Not run: no Python path; Node work is out of W1. Gap: the template server's tracing has no TS twin yet. |
| echo-claude-agent | `openinference-instrumentation-claude-agent-sdk` (suggested; check maturity) | register in its `telemetry.py` | Unknown: the Claude Agent SDK runs a CLI subprocess; spans inside it do not see the Python context. | Not run. Gap: the CLI's model calls may not carry our `traceparent`; check in PoC-8. |
| echo-smolagents | `openinference-instrumentation-smolagents` | `SmolagentsInstrumentor().instrument(tracer_provider=tp)` | Expected yes (spans in the current context). | Not run: two engines were the target. |
| kagent (remote, plain A2A) | none from us: kagent-adk (Google ADK) emits its own OTel spans | its own OTLP env (`OTEL_EXPORTER_OTLP_ENDPOINT`) | Only if kagent continues the A2A `traceparent` header; not checked. | Not run: kagent runs on kind, not here. Gap: plain-A2A mode cannot add `workload.handle`; trace join depends on kagent's propagation. |

## Not verified here, and why

- Real Langfuse v4, the OTel Collector, and Tempo: no image registry in this container. Criterion 1 uses two in-process OTLP/HTTP receivers (`tests/test_poc07_obs_telemetry_swap.py`).
- How Langfuse shows `session.id` and `user.id` from baggage: needs the real service.

Update 2026-10-30: the real Collector and Langfuse v4 ran in `poc07-stack` run 40735435727 on `dd1d594`, with Jaeger as the second backend, not Tempo. How Langfuse shows `session.id` and `user.id` waits on a new stack test in `tests/test_poc07_stack.py`.
