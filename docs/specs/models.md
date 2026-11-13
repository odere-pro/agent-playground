# Models

How a model is chosen, swapped, and judged. Why: [ADR-001](../planning/adr/001-chassis-delivery-model.md) item 6 (the proxies and the scoped key), [ADR-006](../planning/adr/006-agent-engines-default-supported-lanes.md) (the engines and the SLM section), [ADR-010](../planning/adr/010-evals-offline-tool-online-gate.md) (the judge and the gate).

## Routes, not models

A workload never names a provider model. It sends a **route**, and LiteLLM resolves the route to a provider model.

- A route is a name in LiteLLM's model list (`deploy/compose/litellm/config.yaml` and the kind copies): today `big-default`, `local-small`, `fake-chat`, all with `drop_params: true`.
- Each service has one LiteLLM virtual key, minted at deploy (`seed.sh`, `stack.sh`): the routes it may use, a budget per day, a request rate per minute, and the tag `agent:<name>`. suggested: `max_budget 1.0`, `budget_duration 1d`, `rpm_limit 600`.
- A route outside the key's list answers 403 `model_route_denied`. A call of a run that names a route the service's config does not list gets the same 403 from the chassis, before the router sees it (012 H-3).
- Swapping a provider is a LiteLLM config change and a key update. No agent changes.

## How a request gets its route

- `spec.model.route` is the agent's route, `spec.model.fallback_route` its fallback, and `spec.model.routes` its route per task (`chassis/server/config.py`, `ModelSpec`; schema `packages/chassis/schemas/chassis-config.v0.json`). `route`, `routes`, and `judge_route` reload without a restart; `fallback_route` is restart-only.
- The pipeline resolves the run's route into `ctx.model_route` (`context.v0.json`): the task's route from the map, else `route` (the next section). The workload sends it as the OpenAI `model` field to the chassis model proxy. `versions.model_route` on the response stays `route`, the agent's configured route.
- The proxy (`chassis/server/model_proxy.py`, `model_admission.py`) adds the service's key, forwards only the keys it allows, caps `max_tokens` at what the run has left, prices the call from `spec.observability.prices`, and keys the call to its request by `traceparent`. A call with no trace id counts against a per-replica cap and is logged.
- The proxy sends every model call of a run to the run's route when the `model` the workload named is one the config lists (counter `chassis.model_route_forced{route}`): the resolved route, or `fallback_route` for the evaluator gate's last attempt, which replaces it.
- A call of a run that names a route the config does not list (`route`, `fallback_route`, `judge_route`, the values of `routes`) is refused with 403 `model_route_denied`, never rewritten (012 H-3; user decision 2026-11-13). Counter `chassis.model_route_refused{route}`, by the run's route (suggested). The config stands in for the key's list, which the chassis does not hold.
- On the public OpenAI and Anthropic interfaces, `model` names the agent, never a route.

## The route map per task

Built (the product's phase B, first step). The shape:

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

- A task is named by the caller in `input.data.task` (suggested: the key), a string. The pipeline (`chassis/server/pipeline.py`, `context_for`) resolves `routes[task]`, else `route`, into `ctx.model_route`. No task, a task the map does not name, or a value that is not a string all mean `route`. The workload keeps sending `ctx.model_route`, so no workload code changes.
- The run's route is on its `RunRecord` from the moment the run opens, so `model_admission.py` forces every model call of the run that names a listed route to it, the way it forces the gate's fallback route, and refuses a route the config does not list with 403 `model_route_denied`. A workload cannot pick a route the map does not give it. The run span `chassis.run` carries `route` and, when the map named it, `task`.
- A task name is printable, has no space, and is at most 64 characters (`TASK_NAME` in `config.py`; suggested: the shape). An empty task or route name is refused at load.
- The map and `judge_route` reload without a restart, like `route` (`RELOADABLE`). A run keeps the route it opened with.

Still suggested, not built:

- Every route in the map must be in the service's key. A check at load needs the key, which only the deployment holds, so today a route in the config that the key does not list fails at the first call with 403 `model_route_denied`. The config schema and `agentctl` do not check it.
- The gate's defaults (the table below) wait on the judge's numbers from the Mac run.

## The judge model

suggested: one cheap route, `decision-small`, serves every routine judgment. The judge-evaluator behind `EvaluatorPort` is its first and main use. Today no LiteLLM config names a judge route.

- `JudgeEvaluator` (`chassis/adapters/evaluators.py`) calls the judge route through `ModelPort` with rubric `judge-facts-v1`, temperature 0, at most 200 output tokens. The route is `spec.model.judge_route` when set, read from the live config on every call (the gate binds it, `bind_route`), else `CHASSIS_JUDGE_ROUTE`, default `judge`.
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
