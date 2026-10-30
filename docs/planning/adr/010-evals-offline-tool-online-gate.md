# ADR-010: Evals: one offline tool in CI, and an online evaluator gate that wraps the engine

- **Date:** 2026-10-28
- **Deciders:** Oleksandr (epic owner) and the delivery team
- **Format:** Michael Nygard's template: Status, Context, Decision, Consequences
- **Related:** [ADR-001](001-chassis-delivery-model.md) (lanes; gap (e): the framework owns the model loop), [ADR-006](006-agent-engines-default-supported-lanes.md) (the engines), [ADR-007](007-observability-otel-collector-langfuse.md) (spans), [ADR-009](009-feedback-langfuse-scores-event.md) (scores on the trace), [PoC-7](../poc/007-PoC-7-cross-cutting-decisions.md), [reuse analysis](../poc/010-reuse-analysis.md), [contract v6](../../contracts/contract-v6.md), [017 H-4](../issues/017-H-4-harness-features.md), [037 M-2](../issues/037-M-2-eval-gate-ci.md), [042 A-2](../issues/042-A-2-evaluator-gate-fallback.md), [031 G-5](../issues/031-G-5-cost-model-break-even.md), [073 E-1](../issues/073-E-1-evaluator-slm.md), [074 E-2](../issues/074-E-2-evaluator-agreement-test.md), [075 E-3](../issues/075-E-3-evaluator-replaces-judge.md)

## Status

Accepted, 2026-10-30, by the user. Proposed 2026-10-28. Its open questions stay open; a change to one of them needs a new ADR or an edit here.

The trial is done for the offline tool. Part of the gate's numbers waits:

- **The offline tool is DeepEval 4.3.2.** Both tools ran on the same 20 golden cases on `echo-python` and `echo-langgraph` and gave the same verdict on every case ([eval tool trial](../../../pocs/poc-07-cross-cutting-decisions/notes/2026-10-28-eval-tool-trial.md)). CI: `poc07-evals` passed on commit `9d942ee`, [run 40730858164](https://github.com/odere-pro/agent-playground/actions/runs/40730858164) (DeepEval on the 20 golden cases, two engines).
- **The gate's numbers.** No hosted model key is available here, so there are no real judge latency or agreement numbers. Hugging Face answers 403, so there is no encoder model. What was measured: the gate's own overhead on fakes, and the token count of the real judge prompt, with a cost model ([evaluator gate note](../../../pocs/poc-07-cross-cutting-decisions/notes/2026-10-28-evaluator-gate.md)). Waits on: the Mac run for judge and encoder latency, cost, and agreement (item 9).

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
| T1 | promptfoo 0.125.1 | Rejected after the trial. Same verdicts as DeepEval and faster (3.8 s against 7.1 s warm per engine), but it joined OpenAI, a model vendor the gate scores, and it needs Node 22.22 or later: 620 packages, 743 MB |
| T2 | **DeepEval 4.3.2** | **Chosen.** Apache-2.0, independent maintainer; runs from `uv run --no-project --with deepeval==4.3.2` (69 packages, 118 MB, no `uv.lock` change); the checks are Python we own |
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
| G7 | The evaluator as an agent over A2A (`evaluator-facts`) | Not now. A later `EvaluatorPort` adapter. OPEN (user): does 042 A-2 keep the agent or use `judge`? |

## Decision

**We choose DeepEval as the one offline tool for CI, and an online gate in the chassis that wraps the engine, with two modes, a sample rate, and two evaluator adapters.**

1. **The offline tool is DeepEval 4.3.2.** The trial ran both tools on the same 20 golden cases (12 simplifier, 8 lookup; 14 labelled pass, 6 fail) on `echo-python` and `echo-langgraph`, through the chassis in the `sidecar` lane, against the fake model server and a scripted judge. The three reasons:
   - **Vendor neutrality.** DeepEval is Apache-2.0 from an independent maintainer. promptfoo is MIT, but it joined OpenAI. The CI gate scores every model vendor, so it should not belong to one.
   - **One toolchain, a small install.** 69 packages, 118 MB, from a pinned `uv run --with`, no Node. promptfoo needs Node 22.22 or later: 620 packages, 743 MB, a 28 s cold start.
   - **The checks are Python code we own.** Three small `BaseMetric` classes (facts kept, banned words, tool calls in order) and `GEval` with any OpenAI-compatible judge. The 037 M-2 metrics can be one module for this gate and the online gate.

   The numbers, per engine: both tools passed 13 and failed 7 (the 14 cases labelled pass minus g08, where the judge disagrees on purpose). Warm wall time: promptfoo 3.89 s and 3.77 s, DeepEval 7.13 s and 7.18 s. Both exit non-zero when a case fails (promptfoo 100, DeepEval 1), so a CI gate reads the report, not the exit code. One report format for every tool and engine: `bakeoff.evalreport` writes `eval-report.v0` (`pocs/poc-07-cross-cutting-decisions/tests/test_poc07_evals_report_format.py::test_both_engines_give_the_same_report_format`, `::test_the_picked_tool_report_is_eval_report_v0_with_one_row_per_case[echo-python]`, `[echo-langgraph]`). What promptfoo does better is recorded in the trial note, so the pick can be revisited. The criteria (suggested), each met by DeepEval in the trial unless noted:
   - the same report format for every engine, with no engine code;
   - it calls the agent through the chassis (`POST /v1/run` or the OpenAI interface), so the lane and the engine are invisible;
   - it runs in CI against the fake model server with no key (`.github/workflows/poc07-evals.yml`, run 40730858164), and against LiteLLM with a scoped key on the Mac (waits on the Mac run);
   - its judge calls go through LiteLLM, never to a provider directly (waits on: the Mac run; the trial's judge was the fake model server, through `JUDGE_URL`);
   - it reads cases from and writes results to Langfuse datasets and experiments (through `bakeoff.langfuse_datasets`, tested against a stub only);
   - licence, release pace, and the run time of the 20 cases.

   The trial note has the commands and the reports: [eval tool trial](../../../pocs/poc-07-cross-cutting-decisions/notes/2026-10-28-eval-tool-trial.md), reports in `pocs/poc-07-cross-cutting-decisions/notes/evals/`.
2. **Langfuse datasets hold the golden cases. Langfuse experiments hold the results**, one run per engine and per version. 037 M-2's gate reads the same dataset. Each case's `trace_id` is in the report, so `bakeoff.langfuse_datasets` links it to a dataset run (`packages/bakeoff/tests/test_bakeoff_langfuse_datasets.py::test_bakeoff_langfuse_creates_the_dataset_and_one_run_per_report`, against a stub). Waits on: a new stack test in `pocs/poc-07-cross-cutting-decisions/tests/test_poc07_stack.py` (not written yet; `poc07-stack` run 40735435727 has none), for the dataset name and the two experiment runs in a real Langfuse.
3. **The online gate is `GatedEngine`**, a wrapper around the `EngineConnector`, built in the lifespan when `spec.harness.evaluator_gate` is on. It runs in the chassis, in every lane, with no engine code. It refuses to start with `spec.adapters.evaluator: none` (`packages/chassis/tests/test_evaluator_gate.py::test_gate_on_with_no_evaluator_refuses_to_start`).
4. **Sync mode** (`spec.harness.evaluator_mode: sync`, complete calls only):
   - The gate buffers the attempt's events, then calls `EvaluatorPort.score` on the answer.
   - A score at or above `spec.harness.threshold` passes, and the answer leaves.
   - Below it, the gate retries: it calls `handle` again with the same `ctx`, up to `spec.harness.retries` times.
   - Still below, it falls back: one more attempt with `ctx.model_route = spec.model.fallback_route`. The fallback's answer is not scored: it is the last resort, and scoring it adds a judge call to the worst path.
   - `end.status` is `ok` when an attempt on the agent's route passes, the first or a retry (`metrics.attempts` says which), and `fallback` when the fallback answers (`packages/chassis/tests/test_evaluator_gate.py::test_a_retry_that_passes_is_ok_not_fallback`, `::test_a_failing_answer_retries_once_then_falls_back`).
   - As built, an evaluator outage fails open: the attempt goes out as it is, counted as `chassis.gate_failed_open` (`packages/chassis/tests/test_evaluator_gate.py::test_an_evaluator_outage_fails_open`). This differs from 042 A-2's suggestion that an error counts as a failed attempt. OPEN (user): keep fail-open, or make an outage a failed attempt?
   - An attempt that ends in an `error` event is sent as it is, not scored and not retried (`packages/chassis/tests/test_evaluator_gate.py::test_an_engine_error_is_sent_as_is_and_not_scored`). Retry on a model error is the H-4 retry stage.
   - With no `fallback_route`, the run ends with one `error {code: evaluator_gate_failed}` and no answer (`packages/chassis/tests/test_evaluator_gate.py::test_no_fallback_route_ends_in_evaluator_gate_failed`). Native: a 200 envelope with `status: error`; OpenAI and Anthropic: 502 by the frozen `status_for` rule.
   - The client sees one stream: one `start`, the chosen attempt's `delta` and `tool_call` events, one `metrics` per attempt and per scoring, one `end`. The events of a failed attempt are never sent; they stay on the trace.
5. **Async mode** (`evaluator_mode: async`), and **every stream in either mode**: the answer leaves unchanged. After the run's `end`, the gate scores it in the background. The score goes on a `chassis.evaluator.score` span (`evaluator.score`, `evaluator.passed`) and the counter `chassis.evaluator_calls{evaluator, mode, outcome}`. This holds in both modes. Gate results are span attributes today. Langfuse scores for them are not built yet (owner: 017 H-4). Async never retries and never falls back (`packages/chassis/tests/test_evaluator_gate.py::test_async_mode_never_changes_the_answer_and_records_the_score`, `::test_a_stream_is_always_async`).
6. **The sample rate is per request id**, so the decision is the same on every replica and on a client's retry. suggested: a call is gated when the first 8 bytes of `sha256(request_id)`, read as a number below 1, are below `spec.harness.evaluator_sample_rate`. A call that is not gated makes no evaluator call. The rate reloads without a restart. As built (`packages/chassis/tests/test_evaluator_gate.py::test_sampling_is_deterministic_per_request_id`, `::test_sample_rate_zero_makes_no_evaluator_call`).
7. **Two evaluator adapters behind `EvaluatorPort`:**
   - `judge`: an LLM judge through `ModelPort`, so through LiteLLM with the chassis's key, on its own route (`CHASSIS_JUDGE_ROUTE`, suggested: `judge`). The key must list that route. Fixed rubric `judge-facts-v1`, temperature 0; the reply must be exactly `{"score", "reason"}`, else `evaluator_bad_reply`.
   - `encoder`: a small encoder model behind an HTTP classifier, served outside the chassis on CPU, like `guardrail-server`: the Hugging Face TEI `POST /predict` API (`CHASSIS_ENCODER_URL`) with an NLI cross-encoder (suggested: `cross-encoder/nli-distilroberta-base`, not run here). No model runs in the chassis (ADR-001's memory estimate).

   Both pass `EvaluatorPortContract` offline, as the fake does (`packages/chassis/tests/test_evaluators.py::TestJudgeEvaluator`, `::TestEncoderEvaluator`; `pocs/poc-07-cross-cutting-decisions/tests/test_poc07_evals_adapters.py::TestPoc07EvalsJudgeEvaluator`, `::TestPoc07EvalsEncoderEvaluator`).

   Both report tokens, cost, and latency on the `Score`.
8. **The gate's own cost is on the record**, using existing fields only:
   - Sync: the judge's tokens are one more `metrics` event in the run, with `model_route` set to the judge's route and `attempt` set to the attempt it scored. The collector sums it into the response's `usage`, so 031 G-5 and 006 G-6 see the net saving, not the gross. This changes what `usage` covers (it includes the judge), and contract v6 says so.
   - The chassis sets `metrics.attempt` on the workload's own `metrics` events to the attempt number. Workloads keep writing `attempt: 1`.
   - Async: the answer has left, so the judge's tokens never reach the response's `metrics`. They are on the span. (`packages/chassis/tests/test_evaluator_gate.py::test_a_passing_answer_goes_out_once_with_the_judge_metrics` checks the sync half.)
   - The score and its reason are never on `metrics` (no such field; a new one is a schema bump). They are span attributes (item 5).
9. **The gate's cost, per gated call.** For `retries: 1`, with sample rate `r`, judge cost `Cj`, one more attempt `Ca`, the fallback attempt `Cf`, and fail rates `p1` (first attempt) and `p2` (retry):

   - sync: extra cost per request = `r × (Cj + p1 × (Ca + Cj) + p1 × p2 × Cf)`; extra latency on a gated call = `Lj`, plus `La + Lj` when the first attempt fails, plus `Lf` on fallback; and time to first byte becomes the whole run plus `Lj`, because the answer is buffered.
   - async: extra cost per request = `r × Cj`; extra latency on the answer = 0.

   | Per gated call | `judge` | `encoder` |
   | -------------- | ------- | --------- |
   | Tokens | 254 in, 22 out: the exact `JudgeEvaluator` prompt for a typical simplifier answer (counted with `js-tiktoken`, `o200k_base` and `cl100k_base`) | 0 model tokens |
   | Cost | suggested list prices: $0.0000513 on a small hosted judge ($0.15 / $0.60 per million tokens), $0.000855 on a large one ($2.50 / $10.00) | Waits on: CPU cost per call, from the Mac run |
   | p50 and p95 latency | Waits on: the Mac run. On fakes the gate's own overhead is 0.33 ms mean and 0.30 ms p95 at sync rate 1.0 | Waits on: the Mac run |
   | Agreement with human labels on the 20 cases | Waits on: the Mac run (the scripted judge's agreement is written into its script, so it is not reported) | Waits on: the Mac run (no encoder model here) |
   | Agreement between the two | Waits on: the Mac run | |

   Per 1M requests, judge only: the small judge costs $51 at rate 1.0, $13 at 0.25, and $5.1 at 0.1. The large one costs $855, $214, and $86. The simplifier answer itself costs about $0.0000198 on the small hosted route, so a small judge at rate 1.0 is about 2.6 times the answer's own cost. On an SLM route the judge is almost the whole model bill. The failing path costs two judge calls and three engine attempts: 884 tokens against 78 on fakes. Source: [evaluator gate note](../../../pocs/poc-07-cross-cutting-decisions/notes/2026-10-28-evaluator-gate.md).

10. **The recommendation, to confirm with the numbers above** (suggested):
    - With a big-model `judge`: `async`, sample rate 0.1, for monitoring. `sync` only where a hard quality bar is the point (the simplifier's `facts_kept`, 042 A-2), and the owner accepts the cost.
    - With an `encoder` that passes 074 E-2's agreement bar: `sync` at rate 1.0, with a sampled `judge` in `async` for drift (075 E-3).
    - The numbers so far back this reading: sync at rate 1.0 with an LLM judge undoes the SLM saving. OPEN (user): the code's defaults are `sync` and 1.0 (`HarnessSpec`). The gate is off by default, so they matter only when it is switched on. Change them to `async` and 0.1 now, or wait for the Mac numbers?
11. **Where each hook lives:**

    | Hook | Lives in |
    | ---- | -------- |
    | The gate, retry, fallback, sample rate | The chassis (`GatedEngine`) |
    | The judge model | LiteLLM, on its own route |
    | The encoder model | Its own CPU service, behind an HTTP API |
    | The fallback route | The workload uses `ctx.model_route`; the chassis's LiteLLM key must list the route. The model proxy also enforces it: for the fallback attempt the gate sets the run's `RunRecord.model_route`, and the proxy sends every model call of that run there, whatever `model` the workload names (`chassis.model_route_forced`; `packages/chassis/tests/test_evaluator_gate.py::test_the_proxy_sends_every_call_of_the_fallback_attempt_to_the_fallback_route`). Both proxy routes do this, OpenAI and Anthropic |
    | The golden cases and results | Langfuse datasets and experiments |
    | The offline gate | CI, with DeepEval (`.github/workflows/poc07-evals.yml`) |

12. **What each engine must expose:** nothing in its event mapping. It must read `ctx["model_route"]` for every model call (every supported engine does), and it must be safe to run twice for one request: write tools go through the idempotency key (054 H-16). Evidence: `pocs/poc-07-cross-cutting-decisions/tests/test_poc07_evals_gate.py::test_poc07_evals_gate_retries_once_then_falls_back[echo-python]`, `[echo-langgraph]`, `::test_poc07_evals_async_mode_never_changes_the_answer`, `::test_poc07_evals_sample_rate_zero_makes_no_evaluator_call`, `::test_poc07_evals_gate_off_makes_no_evaluator_call`, in the `sidecar` lane over a Unix socket; the chassis tests in `packages/chassis/tests/test_evaluator_gate.py` run in process. The list per engine is in [the per-engine note](../../../pocs/poc-07-cross-cutting-decisions/notes/2026-10-28-per-engine-mapping.md).

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
- The fallback needs the engine to call models through the chassis proxy with `traceparent`. Then the proxy enforces the route even when the engine hard-codes another. An engine that calls a model directly is outside every chassis control anyway.
- Known gaps, as built: the gate does not shrink `budget.timeout_ms` across attempts, and the judge's tokens are not charged to the run's `budget.max_tokens`.

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

## Open questions for the user

1. Keep the evaluator outage as fail-open in sync mode, or count it as a failed attempt, as 042 A-2 suggests (item 4)?
2. Change the gate's defaults from `sync` and 1.0 to `async` and 0.1 now, or wait for the Mac numbers (item 10)?
3. Does 042 A-2 keep the `evaluator-facts` agent behind `EvaluatorPort`, or use the `judge` adapter (G7)?
