# Key metrics

The numbers that say whether the container works: one north star, the input metrics that move it, and the counter metrics that must not get worse while it rises. The vault's recipe is "a north star, two input metrics, and one metric to measure failure" (`notes/SDLC factory/01 - SDLC factory.md`). Every formula below uses what the chassis already emits ([observability](observability.md)), except the two marked *new*. The first-attempt counter was added for this set (046 A-5, an additive contract change).

## North star: good tasks per week

A good task is one run that ends `ok`, within its budget, and that the evaluator passed when it was scored. It counts value the container delivered, not traffic. It rises only when more agents run on the chassis, they answer well, and the controls stay out of the way.

- Formula: runs with `status="ok"` in `chassis_run_duration_seconds`, minus runs with `evaluator_calls{outcome="fail"}`, per week, summed over agents.
- The alternative, if adoption matters more than output: agents in production on the chassis, from the manifests in the service registry (*new*: the registry count). Which one is the north star is an open decision in [the plan](../plans/2026-11-06-agent-container-product.md).

## Input metrics

The levers. The first two are the primary pair.

| Metric | Formula | Lever | Target on record |
| ------ | ------- | ----- | ---------------- |
| Agents live on the chassis | distinct `agent` labels with traffic in the week | adoption | none yet |
| First-attempt pass rate | `chassis_gate_first_attempt_passes_total{mode}` / `chassis_gate_runs_total{mode}`, per agent, exact (046 A-5, decided 2026-11-13; suggested: the counter name). The counter adds one per gated run that passed the gate on attempt 1: never for a retry that passes, a fallback, a fail-open, an engine error, or a structured answer; in async mode, when the run's one background score passes. The per-scoring pass rate, 1 − `evaluator_calls{outcome="fail"}` / `evaluator_calls{outcome in pass, fail}`, counts every scored attempt, so it is not this rate. Neither is 1 − (retries + fallbacks) / runs: a run that retries and then falls back adds to both | prompt and model quality, the route map | none yet |
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
| Judge agreement and stored gate scores (138 CH-18) | suggested: `chassis_evaluator_agreement_total{agree="true"}` / `chassis_evaluator_agreement_total`, by `pair` (`main:shadow`), when `spec.harness.shadow_evaluator` is set at `shadow_sample_rate`; suggested: `chassis_gate_feedback_failed_total{code}` counts gate scores the `FeedbackPort` did not store (each is stored under `spec.harness.rubric`) | a judge change must not move the verdict unseen; the escapes query needs every gate score | none yet |
| Gate fallback rate and fail-open rate | `chassis_gate_fallbacks_total` / sync gate runs; `gate_failed_open` / gate runs; suggested: `chassis_gate_stopped_total{reason}` (`timeout`, `budget`) counts reruns the run's one budget stopped (017 H-4) | the gate must not become the answer path | fallback rate below 10%, alert above it (117 X-3). Fail-open rate: alert above suggested: 1% over 15 minutes (117 X-3, P-1). The two differ on purpose: a fallback is the gate working, a fail-open is the judge down and answers shipped unscored, because an evaluator outage fails open everywhere (ADR-010 item 4, decided 2026-11-06) |
| Sidecar cost per replica | CPU and RSS per chassis container, from the load run | one image must stay thin | 0.05–0.1 vCPU, 128–256 MiB (ADR-001) |
| Rollbacks per chassis release | releases pinned back by any service / releases | ring rollouts must stay safe | none yet |

## The epic's metrics this set does not carry

The epic's success metrics include three this set leaves out on purpose, because they belong to parked or later work: token spend against the big model (the SLM track, parked), the fallback rate to the big model (the router and the fine-tuned student, 063 S-8), and run recovery across agents (the orchestrator, after the MVP). They return with that work.

## How the container shows them

- *new*: one container dashboard next to the per-agent ones, with three rows: the north star, the inputs, the counters. `python -m chassis.dashboards` writes it to `deploy/dashboards/container.json`.
- SLOs and burn-rate alerts on availability, p95, error rate, and the gate fallback rate are [117 X-3](../planning/issues/117-X-3-slos-alerts.md).
- The weekly numbers go to one note per week, by the `record-measurement` skill, so a number is never claimed without its run.
