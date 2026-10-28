# ADR-010: Evals: one offline tool in CI, and an online evaluator gate that wraps the engine

- **Date:** 2026-10-28
- **Deciders:** Oleksandr (epic owner) and the delivery team
- **Format:** Michael Nygard's template: Status, Context, Decision, Consequences
- **Related:** [ADR-001](001-chassis-delivery-model.md) (lanes; gap (e): the framework owns the model loop), [ADR-006](006-agent-engines-default-supported-lanes.md) (the engines), [ADR-007](007-observability-otel-collector-langfuse.md) (spans), [ADR-009](009-feedback-langfuse-scores-event.md) (scores on the trace), [PoC-7](../poc/007-PoC-7-cross-cutting-decisions.md), [reuse analysis](../poc/010-reuse-analysis.md), [contract v6](../../contracts/contract-v6.md), [017 H-4](../issues/017-H-4-harness-features.md), [037 M-2](../issues/037-M-2-eval-gate-ci.md), [042 A-2](../issues/042-A-2-evaluator-gate-fallback.md), [031 G-5](../issues/031-G-5-cost-model-break-even.md), [073 E-1](../issues/073-E-1-evaluator-slm.md), [074 E-2](../issues/074-E-2-evaluator-agreement-test.md), [075 E-3](../issues/075-E-3-evaluator-replaces-judge.md)

## Status

Proposed, 2026-10-28. The user accepts it at the PoC-7 pull request.

This draft decides the shape. Two of its parts wait on the trial:

- **The offline tool.** promptfoo or DeepEval, each run on the same 20 golden cases on two engines. EVIDENCE: the pick, TBD by the trial.
- **The gate's numbers.** No hosted model key is available here, so there are no real judge numbers. Hugging Face answers 403, so there is no encoder model. Offline runs use the fake model server and a stub classifier, and say so. EVIDENCE: every number in the table under Decision item 9, from CI or the Mac.

## Context

The epic's principle is that nothing ships without an eval. 037 M-2 makes it a CI gate for models and prompts. 017 H-4 puts an online evaluator gate in the chassis: score, retry, then fall back.

**The open concern (review 2026-09-29).** The online gate is the one pipeline stage that adds a model call to every request. With a big-model judge, every call pays a second model call and waits for it. That can double p95 latency and eat the saving the SLM route makes, until an encoder on CPU replaces the judge (075 E-3). The review asked PoC-7 to report cost and latency per gated call next to agreement, and to try a sample rate and an asynchronous gate.

The forces:

- **The framework owns the model loop** in the `sidecar` and `remote` lanes (gap (e)). A retry calls `handle` again. A fallback reruns with `ctx.model_route` set to the fallback route. Every supported engine already reads `ctx["model_route"]` for its model calls.
- **A stream cannot be taken back.** Once a delta has left, the gate cannot change the answer.
- **The events are frozen** (contract v5, C.2). `metrics` has `attempt`, `model_route`, `cost_usd`, and `latency_ms`. It has no `score` or `reason`. `end.status` already has `retry` and `fallback`. `Context.model_route` exists.
- **One report for every engine.** The offline tool must not need engine code. It calls the agent through the chassis, so the engine is invisible to it.
- **The epic's agent config** names the evaluator (`harness.evaluator: facts_kept`) and expects an evaluator agent (`evaluator-facts`) behind `EvaluatorPort` (042 A-2).

Options for the offline tool:

| # | Option | Verdict and why |
| - | ------ | --------------- |
| T1 | promptfoo | In the trial. The reuse analysis links an announcement that promptfoo is joining OpenAI: check its licence and governance at the trial. EVIDENCE: TBD by the trial |
| T2 | DeepEval | In the trial. EVIDENCE: TBD by the trial |
| T3 | Inspect AI | Rejected. Still beta (reuse analysis) |
| T4 | Langfuse experiments alone | Rejected alone. It holds the cases and the results, but CI still needs a runner that fails the build. Kept as the store |
| T5 | Our own runner | Rejected. Build and maintenance cost for what both tools do |

Options for the online gate:

| # | Option | Verdict and why |
| - | ------ | --------------- |
| G1 | A judge on every call, before the answer leaves | The epic's default. The cost the review flagged. Kept as one mode, with a sample rate |
| G2 | **A sample rate per request** | **Chosen** as a knob for every mode |
| G3 | **Score after the answer leaves (async); never change it** | **Chosen** as the second mode, and the only mode for a stream |
| G4 | **A small encoder on CPU instead of the judge** | **Chosen** as a second adapter. The target of 075 E-3 |
| G5 | Gate only when the model's own confidence is low | Rejected now. `ModelPort` carries no log-probabilities |
| G6 | The gate inside each workload | Rejected. Engine-specific code in every framework, and the workload could skip it |
| G7 | The evaluator as an agent over A2A (`evaluator-facts`) | Not now. A later `EvaluatorPort` adapter. OPEN: whether 042 A-2 keeps the agent or uses `judge` |

## Decision

**We choose one offline tool for CI (TBD by the trial), and an online gate in the chassis that wraps the engine, with two modes, a sample rate, and two evaluator adapters.**

1. **The offline tool is TBD by the trial.** EVIDENCE: the pick, between promptfoo and DeepEval, with its numbers. The trial runs both on the same 20 golden cases on two engines. The criteria (suggested):
   - the same report format for every engine, with no engine code;
   - it calls the agent through the chassis (`POST /v1/run` or the OpenAI interface), so the lane and the engine are invisible;
   - it runs in CI against the fake model server with no key, and against LiteLLM with a scoped key on the Mac;
   - its judge calls go through LiteLLM, never to a provider directly;
   - it reads cases from and writes results to Langfuse datasets and experiments;
   - licence, release pace, and the run time of the 20 cases.

   EVIDENCE: the trial note in `pocs/poc-07-cross-cutting-decisions/notes/`, with the commands and the two reports.
2. **Langfuse datasets hold the golden cases. Langfuse experiments hold the results**, one run per engine and per version. 037 M-2's gate reads the same dataset. EVIDENCE: the dataset name and the two experiment runs.
3. **The online gate is `GatedEngine`**, a wrapper around the `EngineConnector`, built in the lifespan when `spec.harness.evaluator_gate` is on. It runs in the chassis, in every lane, with no engine code. It refuses to start with `spec.adapters.evaluator: none`.
4. **Sync mode** (`spec.harness.evaluator_mode: sync`, complete calls only):
   - The gate buffers the attempt's events, then calls `EvaluatorPort.score` on the answer.
   - A score at or above `spec.harness.threshold` passes, and the answer leaves.
   - Below it, the gate retries: it calls `handle` again with the same `ctx`, up to `spec.harness.retries` times.
   - Still below, it falls back: one more attempt with `ctx.model_route = spec.model.fallback_route`. The fallback's answer is not gated (suggested); it is scored after, for the record.
   - `end.status` is `ok` when the first attempt passes, `retry` when a retry passes, and `fallback` when the fallback answers.
   - An evaluator error counts as a failed attempt (042 A-2's suggestion), so no unchecked answer leaves.
   - OPEN: with no `fallback_route`, the run ends with `evaluator_gate_failed` (suggested), or the last answer leaves with `end.status: retry`.
   - The client sees one stream: one `start`, the chosen attempt's `delta` and `tool_call` events, one `metrics` per attempt, one `end`. OPEN: whether the discarded attempts' `tool_call` events are dropped from the client's view (suggested: dropped; they stay on the trace).
5. **Async mode** (`evaluator_mode: async`), and **every stream in either mode**: the answer leaves unchanged. After the run's `end`, the gate scores it in the background. The score goes onto the trace: a `chassis.evaluator` span with the score as an attribute, and a Langfuse score of its own name when the feedback adapter is `langfuse` (suggested; OPEN for W4). Async never retries and never falls back.
6. **The sample rate is per request id**, so the decision is the same on every replica and on a client's retry. suggested: a call is gated when the first 8 bytes of `sha256(request_id)`, read as a number below 1, are below `spec.harness.evaluator_sample_rate`. A call that is not gated makes no evaluator call. The rate reloads without a restart.
7. **Two evaluator adapters behind `EvaluatorPort`:**
   - `judge`: an LLM judge through `ModelPort`, so through LiteLLM with the chassis's key, on its own route (suggested: `judge-default`). The key must list that route.
   - `encoder`: a small encoder model behind an HTTP classifier, served outside the chassis on CPU, like `guardrail-server`. No model runs in the chassis (ADR-001's memory estimate).

   Both report tokens, cost, and latency on the `Score`.
8. **The gate's own cost is on the record**, using existing fields only:
   - Sync: the judge's tokens are one more `metrics` event in the run, with `model_route` set to the judge's route and `attempt` set to the attempt it scored. The collector sums it into the response's `usage`, so 031 G-5 and 006 G-6 see the net saving, not the gross. This changes what `usage` covers (it includes the judge), and contract v6 says so.
   - The chassis sets `metrics.attempt` on the workload's own `metrics` events to the attempt number. Workloads keep writing `attempt: 1`.
   - Async: the answer has left, so the judge's tokens go to counters (suggested: `chassis.evaluator.tokens{route}`) and to the span.
   - The score and its reason are never on `metrics` (no such field; a new one is a schema bump). They are span attributes and Langfuse scores.
9. **The gate's cost, per gated call.** For `retries: 1`, with sample rate `r`, judge cost `Cj`, one more attempt `Ca`, the fallback attempt `Cf`, and fail rates `p1` (first attempt) and `p2` (retry):

   - sync: extra cost per request = `r × (Cj + p1 × (Ca + Cj) + p1 × p2 × Cf)`; extra latency on a gated call = `Lj`, plus `La + Lj` when the first attempt fails, plus `Lf` on fallback; and time to first byte becomes the whole run plus `Lj`, because the answer is buffered.
   - async: extra cost per request = `r × Cj`; extra latency on the answer = 0.

   | Per gated call | `judge` | `encoder` |
   | -------------- | ------- | --------- |
   | Tokens | EVIDENCE: TBD by the trial | 0 model tokens |
   | Cost | EVIDENCE: TBD by the trial | EVIDENCE: CPU cost per call, TBD |
   | p50 and p95 latency | EVIDENCE: TBD by the trial | EVIDENCE: TBD by the trial |
   | Agreement with human labels on the 20 cases | EVIDENCE: TBD by the trial | EVIDENCE: TBD (no encoder model here) |
   | Agreement between the two | EVIDENCE: TBD by the trial | |

10. **The recommendation, to confirm with the numbers above** (suggested):
    - With a big-model `judge`: `async`, sample rate 0.1, for monitoring. `sync` only where a hard quality bar is the point (the simplifier's `facts_kept`, 042 A-2), and the owner accepts the cost.
    - With an `encoder` that passes 074 E-2's agreement bar: `sync` at rate 1.0, with a sampled `judge` in `async` for drift (075 E-3).
    - OPEN: the code's defaults are `sync` and 1.0 (`HarnessSpec`). The gate is off by default, so they matter only when it is switched on. Change them to `async` and 0.1 if the numbers agree.
11. **Where each hook lives:**

    | Hook | Lives in |
    | ---- | -------- |
    | The gate, retry, fallback, sample rate | The chassis (`GatedEngine`) |
    | The judge model | LiteLLM, on its own route |
    | The encoder model | Its own CPU service, behind an HTTP API |
    | The fallback route | The workload uses `ctx.model_route`; the chassis's LiteLLM key must list the route. OPEN: whether the model proxy also refuses or rewrites a call whose `model` is not the run's route |
    | The golden cases and results | Langfuse datasets and experiments |
    | The offline gate | CI, with the tool TBD by the trial |

12. **What each engine must expose:** nothing in its event mapping. It must read `ctx["model_route"]` for every model call (every supported engine does), and it must be safe to run twice for one request: write tools go through the idempotency key (054 H-16). EVIDENCE: the gate's retry and fallback tests on two engines, in `inprocess` and `sidecar`.

## Consequences

Easier:

- One gate for every engine and lane. The workload never knows it is gated, except that it may run twice.
- The cost of the gate is visible in the response's `usage` and in counters, so the savings dashboards show the net number.
- Switching from judge to encoder is one `spec.adapters.evaluator` change.
- One offline tool and one report format for every engine.

Harder:

- Sync mode turns a stream into a buffered answer. A client that wants streaming gets async scoring only.
- A retry reruns the whole agent, tool calls included. That costs a full run, and write tools must be idempotent.
- `usage` now includes the judge's tokens in sync mode. A consumer that reads `usage` as the agent's own model spend must subtract the judge's route.
- The fallback works only if the engine honors `ctx.model_route`. An engine that hard-codes its route breaks the fallback silently, until the proxy enforces the route (OPEN above).

Costs, per lane:

| Lane | Cost of one retry or fallback |
| ---- | ----------------------------- |
| `inprocess` | A second in-memory A2A run |
| `sidecar` | A second A2A run on localhost |
| `remote` | A second A2A run over the network, in gVisor, with the remote's token. The highest cost; suggested: async by default for `remote` engines |

## Revisit

Reopen this decision on any of these:

- **The trial's numbers.** If a sync `judge` adds more than 50% to p95 latency (suggested) at the sample rate the owner wants, make `async` the default. If the encoder reaches 074 E-2's agreement bar, make sync with the encoder the default.
- **The picked tool fails one engine**, or cannot give the same report format for every engine.
- **`ModelPort` gains log-probabilities.** Then G5, gating on low confidence, is worth a trial.
- **042 A-2 keeps the `evaluator-facts` agent.** Then add the A2A adapter (G7) behind `EvaluatorPort`.
- **A licence or ownership change** in the picked tool.
