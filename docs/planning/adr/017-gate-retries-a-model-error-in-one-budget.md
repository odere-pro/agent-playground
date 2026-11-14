# ADR-017: The evaluator gate retries a model error, within the run's one budget

- **Date:** 2026-11-13
- **Deciders:** the user (decision of 2026-11-13); written by `developer`
- **Format:** Michael Nygard's template: Status, Context, Decision, Consequences
- **Related:** [017 H-4](../issues/017-H-4-harness-features.md), [ADR-013](013-model-call-retries-belong-to-the-router.md) (item 2 superseded), [ADR-010](010-evals-offline-tool-online-gate.md), [136 CH-16](../issues/136-CH-16-model-proxy-failures.md)

## Status

Proposed, 2026-11-13. The user decided the behavior on 2026-11-13; the user accepts this record by merging. It supersedes item 2 of ADR-013 and its second Revisit line. The rest of ADR-013 stands.

## Context

017 H-4 has one open box: "A scripted model error is retried up to `retries` times, then falls back to `fallback_route`, in both lanes, and the retry and fallback are counted." ADR-013 item 2 said the opposite: the gate's retry stage covers only what the router cannot see, and never reruns an attempt that ended in a model error. The gate shipped that way (`test_a_scripted_model_error_is_not_retried_by_the_gate`).

The router's retry and the gate's rerun are not the same thing. The router retries one model call on the same route, inside one proxy call (`num_retries`, suggested: 2). The gate reruns the whole agent, and its fallback attempt moves every call of that attempt to `spec.model.fallback_route`, which the model proxy enforces. A provider fault the router could not hide still ends the run, though the agent has a configured fallback route.

The gate sees only an attempt's events. A Python workload reports the model proxy's failed call as `error {code: http_<status>}` (017 H-4, Out of scope); the gate reads the event's `retryable` flag.

| Option | A model error in a gated run | Cost |
| ------ | ---------------------------- | ---- |
| 1. Keep ADR-013 item 2 | Ends the run; the fallback route is never tried | A configured fallback is unused on the path that needs it most |
| 2. The gate reruns a retryable `error`, within the one budget | `retries` reruns, then the fallback route | Up to `num_retries × (retries + 1)` upstream calls on the main route, each paid from one budget |
| 3. A new retry loop for model errors, outside the gate | Same as 2 | A second retry count, a second budget, new code |

## Decision

1. **The gate reruns an attempt that ends in a retryable `error`**, as it reruns a failed answer: up to `spec.harness.retries` more attempts on the same route, then one fallback attempt on `spec.model.fallback_route`, with `end.status: fallback`. The retry is counted as `chassis.gate_retries`, the fallback as `chassis.gate_fallbacks`. No new counter, no new setting.
2. **One budget.** Every rerun starts only with time and tokens left (017 H-4, `Allowance`). Else the run ends with `a2a.timeout` or `budget_exhausted` and `chassis.gate_stopped{reason}`, as for a failed answer.
3. **What goes out.** An errored attempt is never scored. Its `metrics` are kept with its `attempt` number. With no fallback route, the last attempt's `error` goes out as it is (not `evaluator_gate_failed`: the answer was never judged). An `error` with `retryable: false` goes out as it is, never rerun.
4. **The gate keys on `retryable`, not on the code.** The gate cannot tell a model error from another retryable workload error, and ADR-013 item 2 already gave the gate the workload error. suggested: so any retryable `error` is rerun.
5. **Only sync mode.** Async mode and streams never rerun (ADR-010); a model error there ends the run, as before.
6. ADR-013 item 1 stands: the adapter never retries a model call; one port call is one upstream call.

## Consequences

- Easier: a provider fault the router could not hide reaches the configured fallback route. The fallback rate (`chassis.gate_fallbacks` over `chassis.gate_runs{mode="sync"}`) now counts model-error fallbacks too.
- Harder: the two retry layers multiply. A dead main route costs up to `num_retries × (retries + 1)` upstream calls before the fallback; the one budget and the run's deadline cap the spend, not the count. The fallback rate no longer tells a bad answer from a model error; the per-attempt span and the `error` code in the log do.
- A workload that marks a non-transient error `retryable: true` gets it rerun. Each such rerun is still paid from the one budget and stops when it is spent.
- Contract: [contract v9](../../contracts/contract-v9.md) does not say how the gate treats an errored attempt. The contract owner should add one line: in sync mode, an attempt ending in a retryable `error` is rerun and can end with `status: fallback`.

## Revisit

- A measured run where a dead main route spent a whole run budget before the fallback (`chassis.gate_stopped{reason="budget"}` after model errors): then a separate, smaller rerun count for errors (`suggested:` `harness.error_retries`), or a fallback straight after the first model error.
- A workload error class that must never be rerun but is marked `retryable: true`: then the gate keys on the code.
