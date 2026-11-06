# Models

How a model is chosen, swapped, and judged. Why: [ADR-001](../planning/adr/001-chassis-delivery-model.md) item 6 (the proxies and the scoped key), [ADR-006](../planning/adr/006-agent-engines-default-supported-lanes.md) (the engines and the SLM section), [ADR-010](../planning/adr/010-evals-offline-tool-online-gate.md) (the judge and the gate).

## Routes, not models

A workload never names a provider model. It sends a **route**, and LiteLLM resolves the route to a provider model.

- A route is a name in LiteLLM's model list (`deploy/compose/litellm/config.yaml` and the kind copies): today `big-default`, `local-small`, `fake-chat`, all with `drop_params: true`.
- Each service has one LiteLLM virtual key, minted at deploy (`seed.sh`, `stack.sh`): the routes it may use, a budget per day, a request rate per minute, and the tag `agent:<name>`. suggested: `max_budget 1.0`, `budget_duration 1d`, `rpm_limit 600`.
- A route outside the key's list answers 403 `model_route_denied`.
- Swapping a provider is a LiteLLM config change and a key update. No agent changes.

## How a request gets its route

- `spec.model.route` is the agent's route and `spec.model.fallback_route` its fallback (`chassis/server/config.py`, `ModelSpec`; schema `packages/chassis/schemas/chassis-config.v0.json`). Both reload without a restart.
- The pipeline copies the route into `ctx.model_route` (`context.v0.json`). The workload sends it as the OpenAI `model` field to the chassis model proxy.
- The proxy (`chassis/server/model_proxy.py`, `model_admission.py`) adds the service's key, forwards only the keys it allows, caps `max_tokens` at what the run has left, prices the call from `spec.observability.prices`, and keys the call to its request by `traceparent`. A call with no trace id counts against a per-replica cap and is logged.
- The evaluator gate's last attempt forces every model call to `fallback_route` (counter `chassis.model_route_forced`).
- On the public OpenAI and Anthropic interfaces, `model` names the agent, never a route.

## The route map per task

suggested: the shape; it is the product's phase B and is not built yet.

```yaml
spec:
  model:
    route: big-default            # the default for every task
    fallback_route: small-default
    routes:                       # which route per task
      simplify: small-default
      lookup: big-default
      classify: decision-small
    judge_route: decision-small   # the judge model
```

- A task is named by the caller in `input.data.task` (suggested) or by the agent's declared operation. The pipeline resolves `routes[task]`, else `route`, into `ctx.model_route`. The workload keeps sending `ctx.model_route`, so no workload code changes.
- `model_admission.py` enforces the resolved route the way it enforces the forced route today, so a workload cannot pick a route the map does not give it.
- Every route in the map must be in the service's key. The config schema and `agentctl` refuse a config that names a route the key does not have.
- The map reloads without a restart, like `route`.

## The judge model

suggested: one cheap route, `decision-small`, serves every routine judgment. The judge-evaluator behind `EvaluatorPort` is its first and main use. Today no LiteLLM config names a judge route: `JudgeEvaluator` reads `CHASSIS_JUDGE_ROUTE`, default `judge`.

- `JudgeEvaluator` (`chassis/adapters/evaluators.py`) calls the judge route through `ModelPort` with rubric `judge-facts-v1`, temperature 0, at most 200 output tokens. The route becomes `spec.model.judge_route` once the map lands.
- The gate (`chassis/server/evaluator_gate.py`, `GatedEngine`) wraps the engine connector in every lane. In `sync` mode it scores the buffered answer, retries once on the same route below `threshold`, then makes one fallback attempt on `fallback_route`, which is not scored and ends with `end.status: fallback`; with no fallback route the run ends with `error evaluator_gate_failed`. In `async` mode, and for every stream, it scores after `end` and puts the score on a span; the answer never changes. The sample is deterministic by `sha256(request_id)`. An evaluator outage fails open. The judge's tokens are added as a `metrics` event.
- `EncoderEvaluator` (an NLI cross-encoder behind the TEI `/predict` API) stays in the code and off, with the SLM track.

The defaults as built (`chassis/server/config.py`, `HarnessSpec`; `profiles.py`), and the product's target, which is not built and is the plan's phase B (ADR-010 items 9 to 11 give the reasons):

| Setting | Built today | Target, suggested | Reason for the target |
| ------- | ----------- | ----------------- | --------------------- |
| `spec.harness.evaluator_gate` | `false` | `true` | every answer can be scored |
| `evaluator_mode` | `sync` | `async` | a sync judge adds its latency to every answer |
| `evaluator_sample_rate` | `1.0` | `0.1` | a small judge at rate 1.0 costs about 2.6 times the answer; at 0.1 it is $5 per million requests |
| `spec.adapters.evaluator` | `none` in every profile | `judge` on the judge route | the cheapest route that passes the agreement test |
| `threshold`, `retries` | `0.7`, `1` | the same | as built |

The gate is not a security boundary: "No prompt, classifier or judge is a security boundary" (the vault's agent security strategy). Sync mode stays for agents that opt in. Before the target defaults are final, the judge's latency, cost, and agreement with people on the gate set (`packages/eval-kit`, `golden/cases-gate.yaml`, 30 cases) must be measured; async becomes the default anyway if a sync judge adds more than 50% to p95.

## What is parked

The SLM track: the vLLM adapter for `ModelPort` (012 H-3), the router SLM (127 L-3), and the fine-tuned student (063 S-8). The chassis runs any model LiteLLM routes to, so a small model is a route, not a code change: the SLM section of ADR-006 shows every supported engine passing on Qwen3-1.7B with only `spec.model.route` changed.
