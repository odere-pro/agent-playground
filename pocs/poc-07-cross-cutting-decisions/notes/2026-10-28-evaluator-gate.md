# The evaluator gate: cost and latency per gated call (PoC-7, open concern)

Date: 2026-10-28. Work package W4. Backlog: 017 H-4. Criteria: 2 (evaluator adapters), 5 (evals part).

The open concern (planning doc, review 2026-09-29): the online gate adds a model call to every request. This note gives the numbers that can be measured here, a cost model with the judge prompt's real token count, and what waits on the Mac.

## What was built

- `chassis.adapters.evaluators.JudgeEvaluator`: an LLM judge through `ModelPort` (the chassis's LiteLLM adapter; route `CHASSIS_JUDGE_ROUTE`, suggested `judge`). Fixed rubric `judge-facts-v1` as the system message, the case in `<input>`, `<reference>`, and `<answer>` tags with closing tags escaped, temperature 0, `max_tokens` 200. The reply must be exactly `{"score": 0..1, "reason": str}`; anything else is `evaluator_bad_reply`. A `ModelError` is `evaluator_unavailable`, retryable when the model error is. Cost comes from `CHASSIS_JUDGE_PRICE_{IN,OUT}_PER_MTOK`, or is `null`.
- `chassis.adapters.evaluators.EncoderEvaluator`: an NLI cross-encoder behind the Hugging Face TEI `POST /predict` API (`CHASSIS_ENCODER_URL`). The premise is the answer. The hypothesis is the input without its `simplify:` prefix. The score is P(`entailment`). It bills zero tokens and measures latency. suggested model: `cross-encoder/nli-distilroberta-base`. It is not run here.
- `chassis.server.evaluator_gate.GatedEngine`: wraps the lane's connector when `spec.harness.evaluator_gate` is on. The module docstring has the full behavior. Sync mode buffers, scores, retries `retries` times, then runs one fallback attempt on `spec.model.fallback_route` with `end.status: fallback`. Async mode, and every stream, passes events through and scores in the background.

## Decisions to confirm (suggested)

1. **No fallback route:** the run ends with `error {code: evaluator_gate_failed}` and no answer. The public text is "the answer did not pass the quality check" (`PUBLIC_MESSAGES`).
2. **Fallback answer is not scored.** It is the last resort; scoring it adds a judge call to the worst path.
3. **Evaluator outage in sync mode fails open.** The attempt goes out as is, counted as `chassis.gate_failed_open`.
4. **An attempt that ends in `error` is sent as is.** It is not scored and not retried. Retry on a model error is the H-4 retry stage, not this gate.
5. **Sampling** is `sha256(request_id)` below `evaluator_sample_rate`. The same request id always decides the same. The rate is reloadable.
6. **The judge's own `metrics`** (tokens, cost, latency, `model_route` = the judge route) are sent after the answer's `start`, with one `metrics` per failed attempt. So the response's `metrics` count the whole gate, and `model_route` stays the answer's route. Async scores never reach `metrics`. They go to the `chassis.evaluator.score` span and `chassis.evaluator_calls{evaluator, mode, outcome}`.
7. **The fallback route is enforced by the model proxy.** The gate sets `RunRecord.model_route` for the fallback attempt, and `admit` sends every call of that run there (`chassis.model_route_forced`). Both proxy routes do this: OpenAI and Anthropic.
8. **Known gaps:** the gate does not shrink `budget.timeout_ms` across attempts, and the judge's tokens are not charged to the run's `budget.max_tokens`.

## Measured offline: overhead of the gate mechanics

```bash
uv run python pocs/poc-07-cross-cutting-decisions/notes/evals/measure_gate_cost.py 400
```

The setup is all in process, over ASGI. The engine is a `FakeEngine` and the judge is `JudgeEvaluator` → `LiteLLMModel` → the fake model server. Each row has 400 requests after 20 warm-up requests. **These latencies are of the fakes.** They show the gate's own overhead (buffering, one more HTTP round trip, the parse), not a real judge. The tokens use the fake's scripted judge usage (300 + 25). The real count is below.

| Row | Judge calls / request | Mean ms | p95 ms | Added mean ms | Added p95 ms | Tokens in response metrics / request |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| gate off | 0.0 | 0.46 | 0.71 | 0.00 | 0.00 | 78.0 |
| sync rate 1.0 | 1.0 | 0.79 | 1.01 | 0.33 | 0.30 | 403.0 |
| sync rate 0.25 | 0.267 | 0.55 | 0.91 | 0.09 | 0.20 | 164.9 |
| sync rate 0.1 | 0.093 | 0.58 | 1.01 | 0.12 | 0.30 | 108.9 |
| async rate 1.0 | 1.0 | 0.83 | 1.08 | 0.37 | 0.37 | 78.0 |
| async rate 0.25 | 0.267 | 0.55 | 0.92 | 0.09 | 0.21 | 78.0 |
| async rate 0.1 | 0.093 | 0.46 | 0.77 | 0.00 | 0.06 | 78.0 |
| sync rate 1.0, failing answer | 2.0 | 1.14 | 1.36 | 0.68 | 0.65 | 884.0 |

How to read it:

- Judge calls per request track the sample rate (0.267 and 0.093 for 0.25 and 0.1 over 420 ids).
- In sync mode the judge's tokens are in the response's `metrics`: 78 → 403 at rate 1.0. In async mode they never are.
- The async rows still show added time here. The background score runs on the same event loop as the next in-process request, and the fake judge takes the same tens of microseconds either way. With a real judge, the async gate adds no wait to the answer, and sync adds the judge's whole latency. A real judge's latency waits on the Mac run.
- The failing path costs two judge calls and three engine attempts: 884 tokens against 78.

## Cost model with the real judge prompt

```bash
bash pocs/poc-07-cross-cutting-decisions/notes/evals/count_judge_tokens.sh
```

```
o200k_base: prompt 254 tokens, reply 22 tokens
cl100k_base: prompt 254 tokens, reply 22 tokens
```

This is the exact `JudgeEvaluator` prompt for a typical simplifier answer: a 17-word input and a 13-word answer. Python `tiktoken` could not fetch its vocabulary here (403), so the count uses npm `js-tiktoken@1.0.21`, which ships the vocabularies in the package.

Prices are `suggested:` list prices per million tokens (input/output), to check before they are used: a small hosted judge at $0.15 / $0.60 (gpt-4o-mini class) and a large one at $2.50 / $10.00 (gpt-4o class). The simplifier call itself is suggested at 60 + 18 tokens.

| Judge | Per gated call | Rate 1.0, per 1M requests | Rate 0.25 | Rate 0.1 | Worst path (fail, 2 judge calls) |
| ----- | ---: | ---: | ---: | ---: | ---: |
| small hosted ($0.15 / $0.60) | $0.0000513 | $51 | $13 | $5.1 | $0.000103 per request (judge only) |
| large hosted ($2.50 / $10.00) | $0.000855 | $855 | $214 | $86 | $0.00171 per request (judge only) |
| encoder on CPU (TEI) | 0 tokens | CPU only | CPU only | CPU only | CPU only |

For scale: the simplifier answer on the small hosted route costs about $0.0000198 per call. A small judge at rate 1.0 is about 2.6 times the answer's own cost. On an SLM route the answer is near free, so the judge is almost the whole model bill. That is the open concern, now with numbers. At rate 0.1 the small judge costs about a quarter of a hosted answer.

suggested reading for the decision: sync gating at rate 1.0 with an LLM judge does undo the SLM saving. The options that keep the saving are async at a low rate, which feeds the golden set and never delays an answer, or sync with the encoder once its agreement is known.

## Judge vs encoder: agreement with human labels

Neither a hosted judge nor an encoder model can run here: no LLM key, and Hugging Face answers 403. The scripted judge's scores are written into the test script, so their "agreement" means nothing. They are not reported. The golden set and its human labels belong to the eval-tool work package (`packages/bakeoff/src/bakeoff/golden/cases.yaml`). This table waits on that set and on the Mac run.

| Evaluator | Agreement with human labels | Cost per call | p50 / p95 latency |
| --------- | --------------------------- | ------------- | ----------------- |
| `JudgeEvaluator`, small hosted judge | waits on the Mac run | $0.0000513 (254 + 22 tokens) | waits on the Mac run |
| `JudgeEvaluator`, large hosted judge | waits on the Mac run | $0.000855 | waits on the Mac run |
| `EncoderEvaluator`, NLI cross-encoder on CPU | waits on the Mac run | 0 tokens | waits on the Mac run |

Mac run (suggested). It needs a LiteLLM with a `judge` route and TEI with the encoder:

```bash
docker run -p 8088:80 ghcr.io/huggingface/text-embeddings-inference:cpu-1.8 \
  --model-id cross-encoder/nli-distilroberta-base
LITELLM_BASE_URL=http://127.0.0.1:4000/v1 LITELLM_API_KEY=<virtual key> CHASSIS_JUDGE_ROUTE=judge \
CHASSIS_ENCODER_URL=http://127.0.0.1:8088 \
  uv run python agreement.py  # suggested; the script waits on the golden set
# For each golden case: score the reference with both evaluators, compare score >= 0.7 with
# human_label == "pass", and print agreement, Cohen's kappa, cost, and p50/p95 latency.
```

Agreement method (suggested): binarize at the gate's `threshold` (0.7). Report raw agreement and Cohen's kappa against the human label, plus the confusion matrix. With 20 cases a single disagreement moves agreement by 5 points, so report the counts, not only the rate. Whether TEI serves this model's architecture is not checked here.
