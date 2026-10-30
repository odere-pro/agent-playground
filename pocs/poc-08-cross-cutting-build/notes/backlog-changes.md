# Backlog changes when PoC-8 closes

Status: **applied, 2026-10-30** with the skill `planning-sync`. Each change below is a "Status after PoC-8" bullet at the end of the issue's `## What`. Open questions 1, 2, 3, 4, 6, and 7 are carried as "open, user decision" in the issue each belongs to; question 5 is answered by the build. The PoC-7 changes in [`../../poc-07-cross-cutting-decisions/notes/backlog-changes.md`](../../poc-07-cross-cutting-decisions/notes/backlog-changes.md) are still a draft and not applied; this file only adds what PoC-8 built or found. The live drills' CI runs on `bc67179` had not finished when this was applied: `poc08-stack` run 41011346864, `poc08-litellm` run 41011346881.

Evidence: [contract v7](../../../docs/contracts/contract-v7.md); the PoC-8 build notes in [ADR-007](../../../docs/planning/adr/007-observability-otel-collector-langfuse.md) and [ADR-008](../../../docs/planning/adr/008-security-identity-guardrails-defense-in-depth.md); the dated notes in this folder: the [red team](2026-10-29-redteam.md) and the [eval gate](2026-10-29-eval-gate.md); the [demo record](../demo/). Test paths below are under `pocs/poc-08-cross-cutting-build/` unless they start with `packages/`. "Every cell" means the 11 cells of `tests/poc08_harness.py`: `echo-python`, `echo-pydanticai`, `echo-langgraph`, `echo-openai-agents` in `inprocess` and `sidecar`, and `echo-python`, `echo-smolagents`, `echo-claude-agent` in `remote`.

No order, size, or dependency change is proposed, so `backlog.py` `ROWS` stays as it is.

## Per issue

### 022 H-6 Security middleware

- Rate limits are built: a token bucket per agent and per caller in `StatePort`, shared by every replica; 429 `rate_limited` with `Retry-After`; `spec.limits.rate_limit`, `rate_limit_on_unavailable` (`fail_open` by default) (contract v7, part C). Evidence: `packages/chassis/tests/test_rate_limit.py`; every cell: `tests/test_poc08_c7_stages_redteam.py::test_every_stage_runs_in_every_lane`.
- The stream secret check is built: a streamed answer with a secret-shaped value ends in `guardrail_blocked` at stage `output_stream` (contract v7, D.1). Regex only. LiteLLM's after-call guardrail stays the hard control.
- The output URL allow-list is built: image and link targets to hosts not on `spec.guardrails.output_url_allow` are removed (contract v7, D.2). The default is empty, so default deny. See open question 2.
- Red team with the PoC-8 controls: 14/18 on the `litellm` stack (pattern stand-in), 13/18 on `keyword`, the same on all 16 engine and lane targets of `bakeoff.redteam` and in all 11 scenario cells ([red-team note](2026-10-29-redteam.md)). `exfil-04-markdown-image` and `secret-04-streamed-answer-has-key` are now stopped. Still missed, with owners unchanged: `input-04-spaced-out` and `input-05-french` (022 H-6, rerun on Prompt Guard 2), `tool-04-base64` and `exfil-03-write-tool-gets-secret` (054 H-16); on `keyword` only, `secret-01-input-asks-for-key`. Evidence: `tests/test_poc08_c7_stages_redteam.py::test_the_runner_with_the_poc08_controls_misses_only_the_four`.
- The red team is a CI job now (`poc08-evals.yml`, job `redteam`), checked against `evals/redteam/baseline.json`.
- LiteLLM's real Presidio hook is built into a drill stack (PR 39): `deploy/compose/poc08-litellm` runs LiteLLM v1.103.0 with the `pii-mask` guardrail (`guardrail: presidio`, `default_on`) against the Presidio analyzer and anonymizer, and guardrail-server with the pattern stand-in for `prompt-guard` and `secret-guard`. The chassis's virtual key is minted with `allowed_routes: ["openai_routes", "/guardrails/apply_guardrail"]`: LiteLLM refuses that route to a non-admin key, and without it the chassis's guard stages fail closed with `guardrail_unavailable`. Evidence: `tests/test_poc08_stack_litellm.py::test_pii_reaches_the_model_masked_by_litellms_presidio_hook` (through the chassis and straight to LiteLLM), `::test_plain_text_reaches_the_model_unchanged`, `::test_the_pii_and_secret_redteam_cases_report_their_numbers` (five PII and secret cases, numbers recorded, not gated); static `tests/test_poc08_litellm_static.py::test_pii_mask_is_litellms_presidio_hook_on_every_call`, `::test_the_virtual_key_may_call_the_model_and_the_guardrail_route_only`. CI evidence: waits on run 41011346881 on `bc67179`.
- p50 and p95 added by the PoC-8 request stages, the Valkey rate limit included, are measured in CI by `tests/poc08_stack_latency.py` on `deploy/compose/poc08` (stages on against stages off, one request at a time). suggested: p50 overhead at most 25 ms and p95 at most 50 ms on a GitHub runner, recorded, never enforced. CI evidence: waits on run 41011346864 on `bc67179`.
- Known limit, carried to acceptance: no rate limit when auth is off (contract v7, C.1); see open question 7. Waits on: the Prompt Guard 2 rerun (the Mac; no Hugging Face token in CI).

### 014 H-7 Observability

- `GET /metrics` on the public port, Prometheus text, every sample labelled `agent` (contract v7, part A). Families for requests, latency, errors, model calls, tokens, estimated cost, tool calls, and the gate. See open question 1 on the default access.
- One redacted JSON log line per chassis record, with `request_id` and `trace_id` inside a run (`chassis/logs.py`).
- The router span `chassis.model.route`, one per proxied model call, in the run's trace.
- One Grafana dashboard per agent, generated into `deploy/dashboards/` by `python -m chassis.dashboards`: requests, latency, errors, tokens, cost, fallback rate. Evidence: `packages/chassis/tests/test_dashboards.py::test_every_panel_names_a_metric_the_chassis_exports`.
- Every cell: `tests/test_poc08_c3_trace.py::test_a_tool_call_has_one_trace_across_run_model_and_tool_spans`, `::test_a_call_without_a_tool_has_no_tool_span`, `::test_every_call_has_metrics_and_json_logs_with_the_trace_id`.
- The Collector's log pipeline masks emails, IBANs, card numbers, US SSNs, and phone numbers by regex (`packages/chassis/tests/test_collector_log_redaction.py`).
- Workload-side JSON logs are built (PR 39). `workload_a2a.logs` writes one JSON object per line with the chassis's field names; inside an A2A request the lines carry `request_id` (from `ctx`) and a 32-hex `trace_id` (the `traceparent` header, else `ctx`). `workload-a2a serve` sets it up at start, uvicorn included. Every Python workload uses that server. `echo-typescript` has the same format in `src/log.ts`. No redaction in the workload: the Collector's log pipeline does it (ADR-007 item 7). Evidence: `packages/workload-a2a/tests/test_workload_a2a_logs.py`, `packages/workloads/echo-typescript/test/log.test.ts`; through the full stack, `tests/test_poc08_workload_logs.py::test_the_workload_logs_json_with_the_chassis_runs_trace_id` (the four trusted engines in `sidecar`, `echo-python` in `remote`).
- A Prometheus and Grafana stack drill is built (PR 39): `deploy/compose/poc08` runs three chassis, the fake model server, Valkey, Prometheus, and Grafana. `tests/test_poc08_stack_metrics.py` checks that Prometheus scrapes every chassis at `/metrics`, that every panel query of `deploy/dashboards/echo.json` returns data, that `User feedback by label` shows `thumbs-down`, that Grafana loads every dashboard file on the provisioned Prometheus, and that a burst meets 429 `rate_limited`. Static: `tests/test_poc08_stack_static.py`. CI: `.github/workflows/poc08-stack.yml`; evidence waits on run 41011346864 on `bc67179`.
- A real LiteLLM span in the trace (PR 39): `LiteLLMModel` sends the current span's `traceparent` (`packages/chassis/tests/test_litellm_traceparent.py`), and LiteLLM's `otel` callback exports to the Collector. `tests/test_poc08_stack_litellm.py::test_one_run_is_one_trace_with_a_litellm_span_under_the_model_proxy` checks one Jaeger trace with a `litellm` span under `chassis.model.call`. CI: `.github/workflows/poc08-litellm.yml`; evidence waits on run 41011346881 on `bc67179`.
- Add to acceptance, still open:
  - Named-entity masking (names, places) in the Collector log pipeline. Regex covers only the fixed-format entities. See open question 6.
  - The feedback panel (`User feedback by label`, on `chassis_feedback_total{label}`) is built and in the stack drill. See open question 4.
  - Sampling (ADR-007 item 8), carried.

### 017 H-4 Harness features

- The evaluator gate config is proven in every lane: `spec.harness.evaluator_gate`, `threshold`, `retries`, and `spec.model.fallback_route` from config, with `retries: 2` so the count is the config's. Built in PoC-7; PoC-8 only adds the tests. Evidence, every cell: `tests/test_poc08_c6_gate.py::test_the_gate_retries_then_falls_back_to_the_configured_route`, `::test_with_no_fallback_route_the_run_ends_in_the_gate_error`. The demo shows the same call with its spans and `/metrics` lines.
- Carried from PoC-7, still open: the gate does not shrink `budget.timeout_ms` across attempts; judge tokens are not charged to `budget.max_tokens`; the default mode and sample rate (`sync`/1.0 against the suggested `async`/0.1) is a user question in ADR-010.

### 037 M-2 Eval gate in CI

- The golden set is 50 cases for the `simplifier` agent: the 20 PoC-7 cases plus 30 in `packages/bakeoff/src/bakeoff/golden/cases-gate.yaml`; ten labelled `fail` on purpose. Evidence: `packages/bakeoff/tests/test_bakeoff_gate_golden.py::test_bakeoff_gate_golden_has_50_labelled_cases_holding_the_poc07_set`.
- The baseline is `evals/gate/baseline.json` (`eval-baseline.v0`): a pass rate per engine and lane, `must_pass` and `must_fail` lists. `python -m bakeoff.evalgate` exits 1 on a rate drop, a failed must-pass case, or a must-fail case that passes.
- `poc08-evals.yml` runs DeepEval and the gate per engine (jobs `evals`, `regression`, `redteam`). Locally: `make evals`, `make evals-regression`, `make redteam`. `kagent-adk` is skipped (kind only).
- The business-logic regression fails the gate: the `drop-facts` logic gives 0/50 against a 0.780 baseline ([eval-gate note](2026-10-29-eval-gate.md)).
- Waits on: the first GitHub run of `poc08-evals.yml` (run id and commit go in the README). A real judge's agreement with the labels (the Mac run). The label of g08: it is labelled `pass` and the scripted judge fails its rubric; it sits in neither baseline list.
- "About 50 cases per agent" holds for one agent. Every other agent needs its own set; that is 064 D-1's work.
- suggested: no tolerance on the pass rate while the model is scripted. A hosted model needs a tolerance; record it when the gate first runs on one.

### 023 C-5 AI-generated marking

- Built: the `ai_marking` body field on the OpenAI and Anthropic formats (complete and every chunk), the CloudEvent extension `aigenerated` on result events, and `Access-Control-Expose-Headers` for the two flags (contract v7, part E). Evidence: `packages/chassis/tests/test_marking_body.py`; every cell: `tests/test_poc08_c7_stages_redteam.py::test_every_stage_runs_in_every_lane` (the headers).
- Add to acceptance: a consumer on a chassis older than PoC-8 must read an event with `aigenerated`. `chassis.ports.events.CloudEvent` has `extra="forbid"`, so an older model rejects the attribute. See open question 3.
- Still open from PoC-7: legal on disclosure for `minimal` with marking on.

### 026 CH-4 Chassis-only credentials and egress

- `exfil-04-markdown-image` no longer has 026 CH-4 as owner: the chassis output URL allow-list stops it (022 H-6). 026 CH-4 keeps the static check that no workload holds a credential and the negative network tests.

### 054 H-16 Tool port

- Still owns two red-team misses: `tool-04-base64` (an encoded instruction in a tool output) and `exfil-03-write-tool-gets-secret` (a secret in a write tool's arguments). Unchanged from PoC-7.

## Open questions for the user

Carried as "open, user decision": 1, 4, and 6 in 014 H-7; 2 and 7 in 022 H-6; 3 in 023 C-5.

1. **`/metrics` open by default.** Today, with `spec.observability.metrics_scope` unset, `/metrics` needs no token, as `/health`. A scope can be set. Keep it open by default? (contract v7, A.2; ADR-007)
2. **Output URL allow-list default deny.** An empty `spec.guardrails.output_url_allow` removes every outside link and image from answers. That stops the markdown-image leak, but changes what every existing agent may answer. Keep default deny, or default allow with a per-agent opt-in? (contract v7, D.2)
3. **CloudEvent `extra="forbid"` and new extensions.** The CloudEvents spec says a consumer should ignore unknown extension attributes. Our model rejects them. Options: (a) accept and drop unknown extensions in `CloudEvent` from now on, so the next extension is not a breaking change; (b) keep `forbid` and roll consumers out before producers on every new extension. suggested: (a).
4. **Feedback on the dashboard.** A `User feedback by label` panel is built from `chassis_feedback_total`. Keep it, or leave feedback to Langfuse only?
5. **Workload-side logs.** Answered by the build (PR 39): the template A2A server and `echo-typescript` write JSON logs with `request_id` and `trace_id`, and the Collector still masks them (014 H-7, above).
6. **Named-entity masking in the Collector.** Run Presidio in the Collector log pipeline (cost and latency), or accept the regex-only gap for workload logs?
7. **Rate limits with auth off.** Today no call is limited when caller auth is off. Is that fine for local and test profiles only, or should an anonymous bucket exist?
