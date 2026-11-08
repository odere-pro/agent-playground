# ADR-013: Model-call retries and fallbacks belong to the model router, not the chassis

- **Date:** 2026-11-08
- **Deciders:** the user; `chassis-architect` reviewed
- **Format:** Michael Nygard's template: Status, Context, Decision, Consequences
- **Related:** [136 CH-16](../issues/136-CH-16-model-proxy-failures.md), [017 H-4](../issues/017-H-4-harness-features.md), [ADR-001](001-chassis-delivery-model.md), [contract v9](../../contracts/contract-v9.md), the [product plan's gap review](../../plans/2026-11-06-agent-container-product.md#if-phases-a-to-f-are-all-built-what-is-still-missing)

## Status

Proposed, 2026-11-08. Written with the CH-16 PR; the user accepts it by merging.

## Context

Three model-proxy paths failed quietly (136 CH-16). LiteLLM's budget refusal reached the workload as `http_400`. The proxy's upstream call had no per-call timeout, so a cancelled run kept paying for it. And the retry stage of 017 H-4 is planned as a rerun of the whole agent, while the model router can retry a provider error and take another route on its own, per route and without a chassis change.

Two places could own a model call's retry and fallback:

| Option | Where the retry runs | What it sees | Cost |
| ------ | -------------------- | ------------ | ---- |
| 1. The router (LiteLLM `num_retries`, `fallbacks` per route) | Inside one proxy call | Provider errors, rate limits, a dead deployment | None in the chassis; config per route |
| 2. The chassis (a retry loop in the model proxy or the adapter) | Around the port call | The same errors, one layer later | Multiplies the router's retries; every retry is a second reservation on the run's budget; the chassis learns provider semantics |

Both at once is the worst case: a provider outage then costs `num_retries × chassis retries` calls per workload call, and the run's budget reservation is held for all of them.

## Decision

1. **The adapter never retries a model call.** One port call is one upstream call (`ModelPortContract.test_a_failed_call_goes_upstream_once`). The retries and the fallback routes are the model router's: every LiteLLM config under `deploy/` sets `num_retries` (suggested: 2) and a `router_settings.fallbacks` entry per route; an empty list states that a route has no safe fallback (a hosted bake-off route never falls back to the fake).
2. **The chassis retry stage (017 H-4) covers only what the router cannot see:** a bad answer (the evaluator gate's low score, 042 A-2), a schema failure, a workload error. It reruns the agent, not the model call.
3. **The port carries the run's time.** `ModelPort.complete` and `.stream` take `timeout_s`, the wall clock of the whole call; the proxy passes what the run has left of `budget.timeout_ms`. Past it the adapter raises `timeout` and closes the upstream connection. A call whose run ends first is cut and answered 403 `run_ended`.
4. **The service's budget refusal is one code**, `service_budget_exhausted`, 429 on both proxy routes with `x-should-retry: false`, counted `chassis.model_budget_exhausted{route}`, with an alert on the counter. It is not retryable: the budget resets on its period.
5. These are additive; they are recorded as [contract v9](../../contracts/contract-v9.md).

## Consequences

- Easier: a provider fault is handled where the provider is known, per route, with no deploy of the chassis. A spent service budget is one code and one alert, not a 400 nobody reads. A run that is over stops paying for its model call.
- Harder: the router's retry count is a deploy-time value in six config files (`packages/chassis/tests/test_litellm_route_resilience.py` keeps them equal). A fallback route's answer carries the fallback's `model` name, which a workload may show.
- Cost: one more header on every chat-route error (`x-should-retry`), one `asyncio` task pair per streamed chunk of a correlated call, and a new 4xx class (`run_ended`) the remote listener passes through.

## Revisit

- A measured provider outage where `num_retries: 2` and the fallback route together did not keep the error rate inside the SLO (117 X-3): then the value, or a chassis-side circuit breaker, is the question.
- 017 H-4's retry stage, when built: if it ever retries a `ModelError` the router already retried, this ADR is being broken.
