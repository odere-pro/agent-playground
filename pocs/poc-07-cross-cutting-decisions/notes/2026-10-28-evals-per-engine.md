# Evals per engine: what must live in the workload (PoC-7, criterion 6, evals part)

Date: 2026-10-28. Work package W4.

**Expected and found: nothing for the gate or the evaluators.** The evaluator gate is a chassis wrapper around the connector (`chassis.server.evaluator_gate.GatedEngine`). It sees only the chassis events every engine already sends (`start`, `delta`, `metrics`, `end`, `error`). It reruns `handle` through the same connector for a retry or the fallback. It has no engine-specific code.

What an engine must already do, and does:

| Engine | Route from `ctx.model_route` (the fallback needs it) | Answer as `delta` text | Rerun is safe | Gate test |
| ------ | --- | --- | --- | --- |
| echo-python | yes (`handle.py`) | yes | yes, read-only tools | `test_poc07_evals_gate.py`, sidecar over a Unix socket |
| echo-langgraph | yes (`handle.py`) | yes | yes, read-only tools | `test_poc07_evals_gate.py`, sidecar over a Unix socket |
| echo-pydanticai | yes | yes | yes | not run here; same connector path |
| echo-openai-agents | yes | yes | yes | not run here |
| echo-typescript | yes (`handle.ts`) | yes | yes | not run here |
| echo-smolagents (remote) | yes | yes | yes | not run here |
| echo-claude-agent (remote) | yes | yes | yes | not run here |

Notes:

- **Route enforcement does not trust the engine.** An engine that ignored `ctx.model_route` would still reach the fallback route. The gate sets the run's `RunRecord.model_route`, and the model proxy sends every call of that run there (`test_the_proxy_sends_every_call_of_the_fallback_attempt_to_the_fallback_route`). This holds only for engines that call models through the chassis proxy with `traceparent`, which every trusted engine does (PoC-2). An engine that calls a model directly is outside every chassis control anyway.
- **Rerun and write tools.** A retry or fallback reruns the whole agent, tool calls included. A write tool called through the chassis `/mcp` is deduped by the run's idempotency key. `RunRecord.keyed_run_key` is the same for every attempt, so a second attempt's write is a replay, not a second write. A write the engine makes outside the chassis tools (its own HTTP, a file) is not idempotent: such an engine must keep the gate in async mode or off. None of the echo engines does this.
- **Each attempt costs a full run.** The run's budget (`budget.max_tokens`, enforced in the proxy) is shared by all attempts, so a retry can hit `budget_exhausted`. The time budget is per attempt (gap, in the gate note).
- **Plain-A2A remotes** (PoC-6, `spec.engine.protocol: a2a`) send no `metrics`, so only the judge's own tokens show in the response. The gate still works on their text.
- **Engine-side evals are not needed.** OpenInference spans from the workload (W1) help debug a low score, but scoring never reads them.
