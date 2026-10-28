# What each engine must carry, per concern (exit criterion 6)

Date: 2026-10-28. Exit criterion 6: "Anything that must live in the workload's event mapping is listed per engine." This note merges the four notes, one per concern:

- observability: [event-mapping-per-engine](2026-10-28-event-mapping-per-engine.md)
- security: [security-per-engine](2026-10-28-security-per-engine.md)
- feedback: [feedback](2026-10-28-feedback.md), "What each engine must expose"
- evals: [evals-per-engine](2026-10-28-evals-per-engine.md)

## The answer

**Nothing lives in any engine's event mapping (`mapping.py`) for any of the four concerns.** Security, feedback, and evals need no engine code at all. Observability needs two lines of wiring in the engine's process, outside the mapping: register its OpenInference instrumentor, and put `inject_trace_headers` on its model and MCP HTTP clients.

What every engine already had to do before PoC-7 still holds. It calls models and tools only through `CHASSIS_MODEL_URL` and `CHASSIS_TOOL_URL`. It forwards `traceparent`. It reads `ctx["model_route"]`. It holds no key.

## Per engine

"Run" means a PoC-7 test ran that engine for that concern. "Not run" means the cell is read from the code and the other notes, not tested here.

| Engine | Observability | Security | Feedback | Evals (gate) | Event mapping change |
| ------ | ------------- | -------- | -------- | ------------ | -------------------- |
| echo-python | Not run. No framework, so no instrumentor. Add `inject_trace_headers` to its `httpx` client; it needs the `workload-a2a[otel]` extra | **Run** (`inprocess`, `sidecar`). Nothing | **Run** (`sidecar`). Nothing | **Run** (`sidecar`). Nothing | None |
| echo-pydanticai | Not run. `openinference-instrumentation-pydantic-ai` (suggested) or PydanticAI's native `instrument=True` | Not run. Nothing; the same chassis code | Not run. Nothing | Not run. Nothing; same connector path | None |
| echo-langgraph | **Run.** `openinference-instrumentation-langchain` 0.1.80 in `echo_langgraph/telemetry.py`. Gap: the model proxy span hangs off `workload.handle`, next to the LLM span, not under it | **Run** (`inprocess`, `sidecar`). Nothing | **Run** (`sidecar`). Nothing | **Run** (`sidecar`). Nothing | None |
| echo-openai-agents | **Run.** `openinference-instrumentation-openai-agents` 2.6.0, `exclusive_processor=True`, in `echo_openai_agents/telemetry.py`. The SDK's own export to OpenAI stays off | Not run. Nothing | Not run. Nothing | Not run. Nothing | None |
| echo-typescript | Not run. Needs a TypeScript twin of `workload_a2a.telemetry` (read `traceparent` and `baggage` in its A2A server) and `@arizeai/openinference-instrumentation-openai` | Not run. Nothing | Not run. Nothing | Not run. Nothing; reads the route in `handle.ts` | None |
| echo-claude-agent (`remote`) | Not run. Instrumentor suggested; the SDK's CLI subprocess may not carry our `traceparent` (check in PoC-8) | Not run. Nothing | Not run. Nothing | Not run. Nothing | None |
| echo-smolagents (`remote`) | Not run. `openinference-instrumentation-smolagents` | Not run. Nothing | Not run. Nothing | Not run. Nothing | None |
| kagent (`remote`, plain A2A) | Not run (kind only). Its own OTel export; the join depends on kagent continuing the A2A `traceparent` | Not run. Nothing | Not run. Nothing | Not run. It sends no `metrics`, so only the judge's tokens show | None |

## Engine behavior that is not a mapping item

- **A blocked tool output** reaches the engine as an MCP tool error (`guardrail_blocked`). `echo-python` ends the run with its own `tool_error` event. `echo-langgraph` was not checked for its exact event. In both, the injected text reaches neither the model nor the answer.
- **The gate reruns the agent.** A retry or fallback runs the whole agent again. Write tools through the chassis `/mcp` are deduped by the run's idempotency key. An engine that writes outside the chassis tools must keep the gate in async mode or off. No echo engine does.
- **The fallback route is enforced by the model proxy**, so an engine that ignores `ctx.model_route` still reaches the fallback route, as long as it calls models through the chassis proxy with `traceparent`.

## Evidence

All offline, from `scripts/check_offline.sh pocs/poc-07-cross-cutting-decisions -q -rA` (`105 passed, 8 skipped`):

- Observability: `tests/test_poc07_obs_trace_join.py::test_one_trace_joins_chassis_workload_framework_model_and_tool[echo-langgraph]`, `[echo-openai-agents]`, `::test_the_chassis_has_no_engine_specific_branch`.
- Security: `tests/test_poc07_sec_engines.py` (`echo-python` and `echo-langgraph`, `inprocess` and `sidecar`), `tests/test_poc07_sec_marking.py::test_marking_and_redaction_hold_on_every_engine`.
- Feedback: `tests/test_poc07_fb_engines.py::test_poc07_fb_feedback_lands_on_the_runs_trace_in_the_sidecar_lane[echo-python]`, `[echo-langgraph]`.
- Evals: `tests/test_poc07_evals_gate.py` (four tests, `echo-python` and `echo-langgraph`), `tests/test_poc07_evals_report_format.py::test_both_engines_give_the_same_report_format`.
