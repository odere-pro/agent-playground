# Key metrics

The numbers that say whether the container works: one north star, the input metrics that move it, and the counter metrics that must not get worse while it rises. The vault's recipe is "a north star, two input metrics, and one metric to measure failure" (`notes/SDLC factory/01 - SDLC factory.md`). Every formula below uses what the chassis already emits ([observability](observability.md)), except the two marked *new*.

## North star: good tasks per week

A good task is one run that ends `ok`, within its budget, and that the evaluator passed when it was scored. It counts value the container delivered, not traffic. It rises only when more agents run on the chassis, they answer well, and the controls stay out of the way.

- Formula: runs with `status="ok"` in `chassis_run_duration_seconds`, minus runs with `evaluator_calls{outcome="fail"}`, per week, summed over agents.
- The alternative, if adoption matters more than output: agents in production on the chassis, from the manifests in the service registry (*new*: the registry count). Which one is the north star is an open decision in [the plan](../plans/2026-11-06-agent-container-product.md).

## Input metrics

The levers. The first two are the primary pair.

| Metric | Formula | Lever | Target on record |
| ------ | ------- | ----- | ---------------- |
| Agents live on the chassis | distinct `agent` labels with traffic in the week | adoption | none yet |
| First-attempt pass rate | 1 − `evaluator_calls{outcome="fail"}` / `evaluator_calls{outcome in pass, fail}` (retries and fallbacks happen only in sync mode, so a ratio over gate runs would read 100% in async) | prompt and model quality, the route map | none yet |
| Task success rate | `ok` runs / `chassis_requests_total`, per agent and interface | reliability | the X-3 error-rate SLO |
| p95 run latency, and the chassis's share of it | `chassis_run_duration_seconds` p95; the stages' overhead | the pipeline's cost | suggested: a stage budget of 25 ms p50 and 50 ms p95 (022 H-6), recorded, not enforced |
| Time to first demo | hours from `agentctl new` to a passing demo, by someone new | the template | under a day |
| Chassis release lead time | hours from a chassis tag to the last ring | the update path | none yet |

## Counter metrics

What must not get worse. The first is the vault's "metric to measure failure".

| Metric | Formula | Why it counters | Target on record |
| ------ | ------- | --------------- | ---------------- |
| Escapes: passed the gate, then bad feedback | runs with `evaluator_calls{outcome="pass"}` and later `chassis_feedback_total{label="bad"}` (or a score below 0.5), per week | a cheap judge can pass bad answers | if online scores fall more than 10 points under offline, review the golden set (the vault's eval operations rule) |
| Cost per good task | `chassis_model_cost_microusd_total`, every route and the judge included / good tasks | the gate and the route map must not buy quality with money | the G-5 break-even; the judge at 0.1 is $5 per million |
| Guardrail false blocks | blocks on golden inputs / golden inputs; plus the `guardrail_unavailable` rate | more blocking is not more safety | below 1% false positives (ADR-008); revisit if a stage adds more than 150 ms p95 |
| Secret or credential leaks | stream secret guard hits that reached a client; red-team misses | hard requirement 1 | zero; red team 18 of 18 |
| Gate fallback rate and fail-open rate | `chassis_gate_fallbacks_total` / sync gate runs; `gate_failed_open` / gate runs | the gate must not become the answer path | below 10% (117 X-3) |
| Sidecar cost per replica | CPU and RSS per chassis container, from the load run | one image must stay thin | 0.05–0.1 vCPU, 128–256 MiB (ADR-001) |
| Rollbacks per chassis release | releases pinned back by any service / releases | ring rollouts must stay safe | none yet |

## The epic's metrics this set does not carry

The epic's success metrics include three this set leaves out on purpose, because they belong to parked or later work: token spend against the big model (the SLM track, parked), the fallback rate to the big model (the router and the fine-tuned student, 063 S-8), and run recovery across agents (the orchestrator, after the MVP). They return with that work.

## How the container shows them

- *new*: one container dashboard next to the per-agent ones, with three rows: the north star, the inputs, the counters. `python -m chassis.dashboards` writes it to `deploy/dashboards/container.json`.
- SLOs and burn-rate alerts on availability, p95, error rate, and the gate fallback rate are [117 X-3](../planning/issues/117-X-3-slos-alerts.md).
- The weekly numbers go to one note per week, by the `record-measurement` skill, so a number is never claimed without its run.
