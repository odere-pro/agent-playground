# PoC-8 offline demo: a fact-dropping change fails the eval gate; a live call retries, falls back, and takes a thumbs-down on its trace

Where it ran: localhost on Darwin 27.0.0. Steps 1 and 2: the `sidecar` lane in a `bakeoff` stack (the fake model server on localhost TCP as the engine's model and the judge). Step 3: the `sidecar` lane on Unix sockets, every PoC-8 stage on, the LiteLLM stub and the guardrail server (pattern stand-in) in process. No Docker, no kind, no key.
Commit: `7900863` (7 changed or untracked paths at the start). Date: 2026-10-29T13:56:20Z.
Script: `pocs/poc-08-cross-cutting-build/demo/demo.sh` (helper `poc08_demo.py`, which reuses `tests/poc08_harness.py`). The uv `UV_NATIVE_TLS` deprecation warning is dropped from the output; the temp folder is shown as `$TMP`.
Not shown here: a Grafana dashboard (no Grafana runs offline; step 3 shows the `/metrics` lines the dashboards read, including `chassis_feedback_total`, which the dashboards' feedback panel reads), the real Langfuse, LiteLLM, and Prompt Guard 2 (the Mac run). The judge's scores are scripted: they show the mechanics, not judge quality.

## 1. The eval gate passes the normal business logic (echo-python, sidecar)

$ make evals EVAL_OUT=$TMP/evals EVAL_ARGS=--engine echo-python --lane sidecar

```
uv run python -m bakeoff.golden.generate --check
uv run python -m bakeoff.evalrun --out $TMP/evals/normal --engine echo-python --lane sidecar
{"engine": "echo-python", "tool": "deepeval", "total": 50, "passed": 39, "failed": 11, "lane": "sidecar", "tool_exit": 1, "tool_wall_s": 7.45, "stack_start_s": 2.62, "model_calls": 71, "judge_calls": 50, "offline": false, "logic": "normal", "golden": "gate"}
uv run python -m bakeoff.evalgate --report $TMP/evals/normal --engine echo-python --lane sidecar
PASS simplifier echo-python sidecar 39/50 0.780 (baseline 0.780)
eval gate: 1 passed, 0 failed
```

exit code: 0

## 2. Business logic that drops facts fails the eval gate (the CI regression step)

The gate's own exit code is 1 here; `make evals-regression` turns exactly that into exit 0 and fails on anything else.

$ make evals-regression EVAL_OUT=$TMP/evals

```
uv run python -m bakeoff.evalrun --logic drop-facts --engine echo-python --lane sidecar --out $TMP/evals/drop-facts
{"engine": "echo-python", "tool": "deepeval", "total": 50, "passed": 0, "failed": 50, "lane": "sidecar", "tool_exit": 1, "tool_wall_s": 7.71, "stack_start_s": 2.1, "model_calls": 71, "judge_calls": 50, "offline": false, "logic": "drop-facts", "golden": "gate"}
FAIL simplifier echo-python sidecar 0/50 0.000 (baseline 0.780): pass rate 0.000 < 0.780; must-pass cases failed: g01, g02, g03, g04, g05, g06, g07, g13, g14, g15, g16, g17, g18, g21, g22, g23, g24, g25, g26, g27, g28, g29, g30, g31, g32, g33, g34, g35, g36, g37, g38, g42, g43, g44, g45, g46, g47, g48, g49
eval gate: 0 passed, 1 failed
ok: the gate failed the drop-facts logic
```

exit code: 0

## 3. A live call: low score, retry, fallback; its trace; a thumbs-down on it; /metrics

$ uv run python pocs/poc-08-cross-cutting-build/demo/poc08_demo.py live --engine echo-langgraph --lane sidecar

```
POST /v1/run  engine=echo-langgraph lane=sidecar  every stage on
  config: spec.harness.evaluator_gate=true retries=2 threshold=0.7, spec.model.route=big-default fallback_route=fallback-route
  input='simplify: gate-check The report was signed by Dana Ruiz on 4 May 2025.'
  http 200  status=fallback
  output='Dana Ruiz signed the report on 4 May 2025.'
  metrics: attempts=4 model_route=fallback-route
  request_id=79caad16c363496baca69aff3ac1db4e  trace_id=8aab214c8a5142c098b7256901995a89
model calls in order: ['big-default', 'big-default', 'big-default', 'fallback-route']
judge calls: 3 (the fallback answer is not scored)

### The trace: every chassis span of the run

span                       id       parent   trace id                          attributes
chassis.run                span-1   -        8aab214c8a5142c098b7256901995a89  {}
chassis.engine.run         span-2   span-1   8aab214c8a5142c098b7256901995a89  {}
chassis.model.route        span-3   684db7b8 8aab214c8a5142c098b7256901995a89  {"route": "big-default", "route.requested": "big-default", "route.forced": false}
chassis.model.call         span-4   684db7b8 8aab214c8a5142c098b7256901995a89  {"route": "big-default"}
chassis.evaluator.score    span-5   span-1   8aab214c8a5142c098b7256901995a89  {"attempt": 1}
chassis.engine.run         span-6   span-1   8aab214c8a5142c098b7256901995a89  {}
chassis.model.route        span-7   ba227710 8aab214c8a5142c098b7256901995a89  {"route": "big-default", "route.requested": "big-default", "route.forced": false}
chassis.model.call         span-8   ba227710 8aab214c8a5142c098b7256901995a89  {"route": "big-default"}
chassis.evaluator.score    span-9   span-1   8aab214c8a5142c098b7256901995a89  {"attempt": 2}
chassis.engine.run         span-10  span-1   8aab214c8a5142c098b7256901995a89  {}
chassis.model.route        span-11  ac8f699f 8aab214c8a5142c098b7256901995a89  {"route": "big-default", "route.requested": "big-default", "route.forced": false}
chassis.model.call         span-12  ac8f699f 8aab214c8a5142c098b7256901995a89  {"route": "big-default"}
chassis.evaluator.score    span-13  span-1   8aab214c8a5142c098b7256901995a89  {"attempt": 3}
chassis.engine.run         span-14  span-1   8aab214c8a5142c098b7256901995a89  {}
chassis.model.route        span-15  57598492 8aab214c8a5142c098b7256901995a89  {"route": "fallback-route", "route.requested": "fallback-route", "route.forced": false}
chassis.model.call         span-16  57598492 8aab214c8a5142c098b7256901995a89  {"route": "fallback-route"}

### Thumbs-down feedback on the same request id

POST /v1/feedback request_id=79caad16c363496baca69aff3ac1db4e score=0 label=thumbs-down
  http 202  trace_id=8aab214c8a5142c098b7256901995a89
  stored: request_id=79caad16c363496baca69aff3ac1db4e trace_id=8aab214c8a5142c098b7256901995a89 score=0.0 label=thumbs-down

### GET /metrics (no token by default), the gate and feedback lines

http 200
  chassis_requests_total{agent="simplifier",interface="native"} 1
  chassis_model_calls_total{agent="simplifier",route="big-default"} 3
  chassis_model_calls_total{agent="simplifier",route="fallback-route"} 1
  chassis_gate_runs_total{agent="simplifier",mode="sync"} 1
  chassis_gate_retries_total{agent="simplifier"} 2
  chassis_gate_fallbacks_total{agent="simplifier"} 1
  chassis_evaluator_calls_total{agent="simplifier",evaluator="fake",mode="sync",outcome="fail"} 3
  chassis_feedback_total{agent="simplifier",label="thumbs-down"} 1

check: http 200, status fallback: ok
check: the fallback answer, the bad one never left: ok
check: 3 scored attempts on the main route, then the fallback: ok
check: retries and fallbacks counted: ok
check: every span on the run's trace: ok
check: a score span per scored attempt: ok
check: a router span per model call: ok
check: feedback 202 on the run's trace: ok
check: /metrics shows the fallback: ok
check: /metrics shows the thumbs-down: ok
```

exit code: 0

## What this shows

- Criterion 5: the drop-facts logic takes the pass rate from 0.780 to 0.000 and fails every must-pass case; `bakeoff.evalgate` exits 1, the step that blocks a merge in `poc08-evals.yml`. The normal logic passes at the baseline.
- Criterion 6: the gate retries as many times as `spec.harness.retries` says (2), then the model proxy sends the next attempt to `spec.model.fallback_route`; the status is `fallback` and the low-scored answer never leaves.
- Criterion 3: every chassis span of the call (run, engine attempts, router, model proxy, evaluator score) carries the run's one trace id; `/metrics` counts the request, the model calls per route, the retries, and the fallback.
- Criterion 4: the thumbs-down with only the `request_id` lands on the run's trace and is counted in `chassis_feedback_total{label="thumbs-down"}`.
- Not shown: the dashboard panel. The planning doc's "on the dashboard" waits on Grafana loading `deploy/dashboards`; their `User feedback by label` panel reads `chassis_feedback_total`, shown in step 3.

Result: every step ok. Total time 30 s. Last command exit code: 0.
