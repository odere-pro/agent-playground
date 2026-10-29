# PoC-8: Build in security, observability, feedback, and evals on every engine

Status: in progress: offline evidence complete; waits on the first GitHub run of `poc08-evals.yml` and the user's review. Plan: [docs/plans/2026-10-29-poc-08-cross-cutting-build.md](../../docs/plans/2026-10-29-poc-08-cross-cutting-build.md)
Planning doc: [008-PoC-8-build-cross-cutting.md](../../docs/planning/poc/008-PoC-8-build-cross-cutting.md)
Time box: 3–5 days

## Question

Do the designs chosen in PoC-7 work end to end, on every supported engine and in every lane, as one part of the chassis?

## Cells

The scenario tests run 11 cells (`tests/poc08_harness.py`, `CELLS`), each with every stage on at once:

- the four trusted Python engines of ADR-006, `echo-python`, `echo-pydanticai`, `echo-langgraph`, and `echo-openai-agents`, in `inprocess` and `sidecar`;
- `echo-python`, `echo-smolagents`, and `echo-claude-agent` in `remote`.

The CI eval and red-team jobs (`bakeoff`) run 16 engine and lane targets: the same engines in all three lanes where the trust rule allows, plus `echo-typescript` in `sidecar` and `remote`. `kagent-adk` runs only on kind and is skipped in both.

Test names below are under `tests/` unless they start with `packages/`. `[every cell]` means the test passed in all 11 cells.

## Scope

- [x] Security middleware per the security ADR: auth and scopes, rate and size limits, PII redaction, the injection check, and AI-generated marking. · evidence: [contract v7](../../docs/contracts/contract-v7.md) parts C to E; `test_poc08_c7_stages_redteam.py::test_every_stage_runs_in_every_lane` [every cell] (401 and 403, the input, tool-output, and output guards, the marking headers, the per-caller rate limit, the stream secret check, the output URL allow-list); `packages/chassis/tests/test_rate_limit.py`, `packages/chassis/tests/test_output_filter.py`, `packages/chassis/tests/test_marking_body.py`. · waits on: Prompt Guard 2 and LiteLLM's real Presidio hook in place of the stand-ins (the Mac run); the user's decision on the URL allow-list default (deny today; open question 2 in [notes/backlog-changes.md](notes/backlog-changes.md))
- [ ] Observability per the observability ADR: spans at the chassis, its model and tool proxies, and the router; structured JSON logs with `request_id` and `trace_id`; Prometheus metrics at `/metrics`; one dashboard per agent (requests, latency, errors, tokens, cost, fallback rate). The workload's logs do not pass through the chassis. suggested: they are redacted in the OTel Collector log pipeline (gap (f) in the [backlog plan](../../docs/planning/issues/000-plan.md#adr-001-follow-ups)). · offline evidence: `test_poc08_c3_trace.py::test_a_tool_call_has_one_trace_across_run_model_and_tool_spans`, `::test_a_call_without_a_tool_has_no_tool_span`, `::test_every_call_has_metrics_and_json_logs_with_the_trace_id` [every cell]; dashboards `packages/chassis/tests/test_dashboards.py::test_every_panel_names_a_metric_the_chassis_exports`; Collector masking `packages/chassis/tests/test_collector_log_redaction.py::test_the_log_pipeline_masks_the_chassis_entity_set_on_body_and_attributes`. · waits on: Prometheus and Grafana loading `deploy/dashboards/`; JSON logs with `trace_id` from the workload side; named-entity masking in the Collector (regex covers emails, IBANs, cards, SSNs, phones only); a real LiteLLM span in the trace; the user's decision on `/metrics` open by default (open question 1)
- [x] Feedback per the feedback ADR: `POST /v1/feedback`, linked to the trace, and published as an event. · evidence: `test_poc08_c4_feedback.py::test_feedback_lands_on_the_runs_trace` [every cell]; `test_poc08_c7_stages_redteam.py::test_every_stage_runs_in_every_lane` (feedback 202 on the run's trace) [every cell]; the event and the real Langfuse were proven in PoC-7 (`poc07-stack` run 40735435727). The [demo](demo/2026-10-29-demo-cross-cutting-build-offline.md) shows a thumbs-down on the trace and in `chassis_feedback_total`
- [x] Online evals: the evaluator gate with `threshold`, `retries`, and `fallback_route` from config. · evidence: `test_poc08_c6_gate.py::test_the_gate_retries_then_falls_back_to_the_configured_route`, `::test_with_no_fallback_route_the_run_ends_in_the_gate_error` [every cell], with `retries: 2` from the config. The gate was built in PoC-7; PoC-8 proves it in every lane
- [ ] Offline evals: the eval runner in CI with a small golden set (about 50 cases per agent), blocking a merge on regression. · offline evidence: 50 cases (`packages/bakeoff/tests/test_bakeoff_gate_golden.py::test_bakeoff_gate_golden_has_50_labelled_cases_holding_the_poc07_set`), baseline `evals/gate/baseline.json`, `python -m bakeoff.evalgate`; `make evals` passes at 39/50 and `make evals-regression` fails the drop-facts logic at 0/50 ([eval-gate note](notes/2026-10-29-eval-gate.md)); `test_poc08_c5_regression.py` (below). · waits on: the first GitHub run of `poc08-evals.yml`. The set covers one agent, `simplifier`; the other agents' sets are 064 D-1
- [ ] The red-team set from PoC-7 runs in CI. · offline evidence: `make redteam` on 16 targets: 14/18 `litellm`, 13/18 `keyword` on every target ([red-team note](notes/2026-10-29-redteam.md)); `test_poc08_c1_ci.py::test_poc08_evals_runs_the_gate_and_the_redteam_set_on_every_supported_engine`. · waits on: the first GitHub run of `poc08-evals.yml`, job `redteam`
- [x] Every pipeline stage runs in every lane: `inprocess`, `sidecar`, and `remote`. · evidence: `test_poc08_c7_stages_redteam.py::test_every_stage_runs_in_every_lane` [every cell]

## Exit criteria

Each one has a scenario test in `tests/` or a recorded reason it cannot have one yet.

- [ ] 1. All suites, including red-team and offline evals, run in CI, and the `fake` profile still needs no network or keys. · offline evidence: `test_poc08_c1_ci.py::test_ci_runs_make_check_and_make_check_runs_every_poc_folder_offline`, `::test_the_gate_collects_this_folder_with_no_network_test`, `::test_poc08_evals_runs_the_gate_and_the_redteam_set_on_every_supported_engine`, `::test_the_suite_workflows_hold_no_secret_read_only_and_pinned[ci.yml]`, `[poc08-evals.yml]`, `::test_under_make_test_no_socket_and_no_key` (runs under `make test-poc POC=08` only). · waits on: the first GitHub run of `poc08-evals.yml` (run id and commit go here)
- [ ] 2. Every supported engine passes the red-team set in CI. · offline evidence: `make redteam`, every target at its baseline, 14/18 `litellm` and 13/18 `keyword`; every miss has an owner ([red-team note](notes/2026-10-29-redteam.md)). One target failed to start on a port race in the full run; its rerun passed. · waits on: the first GitHub run of `poc08-evals.yml`, job `redteam`
- [x] 3. Every call on every engine has a complete trace, logs with `trace_id`, and metrics. · evidence: `test_poc08_c3_trace.py::test_a_tool_call_has_one_trace_across_run_model_and_tool_spans`, `::test_a_call_without_a_tool_has_no_tool_span`, `::test_every_call_has_metrics_and_json_logs_with_the_trace_id` [every cell]; [demo](demo/2026-10-29-demo-cross-cutting-build-offline.md) step 3. These are the chassis's logs; the workload's own logs wait on the workload-side JSON logs (Scope, observability)
- [x] 4. Feedback shows on the right trace for every engine. · evidence: `test_poc08_c4_feedback.py::test_feedback_lands_on_the_runs_trace` [every cell]; [demo](demo/2026-10-29-demo-cross-cutting-build-offline.md) step 3
- [x] 5. A regression in business logic fails CI through the offline eval. · evidence: `test_poc08_c5_regression.py::test_the_gate_fails_drop_facts_and_passes_normal_on_every_baseline_target[normal]`, `[drop-facts]`, `::test_drop_facts_through_the_cell_fails_the_gate_and_normal_passes` [every cell], `::test_the_ci_regression_job_runs_the_same_drop_facts_gate`; `make evals-regression`: `FAIL simplifier echo-python sidecar 0/50 0.000 (baseline 0.780)` then `ok: the gate failed the drop-facts logic` ([eval-gate note](notes/2026-10-29-eval-gate.md), [demo](demo/2026-10-29-demo-cross-cutting-build-offline.md) step 2). The GitHub run of the `regression` job is in criterion 1
- [x] 6. The online evaluator gate retries and falls back as configured. · evidence: `test_poc08_c6_gate.py::test_the_gate_retries_then_falls_back_to_the_configured_route`, `::test_with_no_fallback_route_the_run_ends_in_the_gate_error` [every cell]; [demo](demo/2026-10-29-demo-cross-cutting-build-offline.md) step 3: `status=fallback`, `attempts=4`, model calls `['big-default', 'big-default', 'big-default', 'fallback-route']`
- [x] 7. Every pipeline stage runs in every lane, and the red-team set passes in each. · evidence: `test_poc08_c7_stages_redteam.py::test_every_stage_runs_in_every_lane`, `::test_the_redteam_set_is_stopped_at_least_at_the_poc08_rate` [every cell], `::test_the_runner_with_the_poc08_controls_misses_only_the_four`; `make redteam` in all three lanes ([red-team note](notes/2026-10-29-redteam.md))
- [x] 8. No engine-specific code outside the workloads' event mappings. · evidence: `test_poc08_c8_no_engine_code.py::test_no_engine_name_in_chassis_code`, `::test_no_framework_import_in_the_chassis`, `::test_every_mention_in_text_is_on_the_allow_list`, `::test_the_scan_sees_the_chassis`

## How to run

Offline, with no socket and no key (`113 passed` on 2026-10-29, the tree of `7900863`):

```bash
make test-poc POC=08
```

The CI steps, locally (localhost TCP only, no key; each starts `bakeoff` stacks):

```bash
make evals EVAL_ARGS="--engine echo-python --lane sidecar"   # DeepEval on the 50 cases, then the gate
make evals-regression                                        # drop-facts must fail the gate
make redteam                                                 # the red-team set, every engine and lane
```

CI runs them in `.github/workflows/poc08-evals.yml`, one job per engine.

## Demo

[demo/2026-10-29-demo-cross-cutting-build-offline.md](demo/2026-10-29-demo-cross-cutting-build-offline.md), written by `demo/demo.sh` (helper `demo/poc08_demo.py`). Offline. It shows the normal logic passing the gate, the drop-facts logic failing it, and one live call that scores low, retries twice, and falls back. Then the call's spans on one trace, a thumbs-down on that trace, and the `/metrics` lines. It does not show a Grafana dashboard: no Grafana runs offline. The dashboards' `User feedback by label` panel reads the `chassis_feedback_total` line the demo prints.

## Notes and decisions

Dated files in `notes/`: the [red team](notes/2026-10-29-redteam.md) and the [eval gate](notes/2026-10-29-eval-gate.md). The contract is [contract v7](../../docs/contracts/contract-v7.md); the build notes are in ADR-007 and ADR-008. [notes/backlog-changes.md](notes/backlog-changes.md) lists what the backlog should change and the open questions for the user.

What waits, in one place:

- The first GitHub run of `poc08-evals.yml` (criteria 1, 2; the Offline evals and red-team scope items).
- The Mac run: real Langfuse, LiteLLM, and Prompt Guard 2.
- Prometheus and Grafana loading the dashboards (each has a `User feedback by label` panel on `chassis_feedback_total`).
- JSON logs with `trace_id` from the workload side.
- Named-entity masking in the Collector log pipeline.
- User decisions: the `output_url_allow` default deny; `/metrics` open by default; `CloudEvent` `extra="forbid"` against new extensions such as `aigenerated`.
