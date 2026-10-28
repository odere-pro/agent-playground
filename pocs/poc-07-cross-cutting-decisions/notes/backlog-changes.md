# Backlog changes when PoC-7 closes

Status: **draft, 2026-10-28.** Not applied. Apply with the skill `planning-sync` when PoC-7 closes and the user accepts the ADRs. Each change becomes a "Status after PoC-7" bullet at the end of the issue's `## What`. A short "Proposed in PoC-7" bullet with the ADR links is already in 022, 014, 017, 037, 023, 042, and 081.

Evidence: [ADR-007](../../../docs/planning/adr/007-observability-otel-collector-langfuse.md), [ADR-008](../../../docs/planning/adr/008-security-identity-guardrails-defense-in-depth.md), [ADR-009](../../../docs/planning/adr/009-feedback-langfuse-scores-event.md), [ADR-010](../../../docs/planning/adr/010-evals-offline-tool-online-gate.md), [contract v6](../../../docs/contracts/contract-v6.md). The dated notes in this folder: the [per-engine list](2026-10-28-per-engine-mapping.md), the [red team](2026-10-28-redteam.md), the [eval tool trial](2026-10-28-eval-tool-trial.md), the [evaluator gate](2026-10-28-evaluator-gate.md), [feedback](2026-10-28-feedback.md), the [agent scan](2026-10-28-agent-scan.md), and [observability per engine](2026-10-28-event-mapping-per-engine.md). Test paths below are under `pocs/poc-07-cross-cutting-decisions/` unless they start with `packages/`. The real-service run passed: `poc07-stack` run 40735435727 on `dd1d594` (real Langfuse v4, Collector, Jaeger; 7 passed, 1 skipped). Waits on: the Mac run (Prompt Guard 2, the encoder, a hosted judge).

No order, size, or dependency change is proposed yet, so `backlog.py` `ROWS` stays as it is. If one is needed, it is listed under "ROWS" below and applied by the orchestrator, not here.

## The epic's event catalog (recorded here, because the epic is never edited)

Add one row to the epic's D.3 event catalog:

| Event type | Producer | Main consumers |
| ---------- | -------- | -------------- |
| `agents.feedback.received.v1` | Any agent's chassis (`POST /v1/feedback`) | Recorder (040 D-0), golden set agent (064 D-1), audit log (072 C-3) |

Payload `feedback.v0.json` (contract v6, part C). The schema is registered by 089 R-15. The schema file is `packages/chassis/schemas/feedback.v0.json`; its test is `packages/chassis/tests/test_feedback_route.py::test_the_event_is_published_redacted_and_valid`.

## Per issue

### 022 H-6 Security middleware

- Caller auth is `OidcAuth` behind `AuthPort`; scopes per route from `spec.security` (`run_scope`, `feedback_scope`, `read_scope`); codes 401 `unauthenticated`, 403 `forbidden` (with `WWW-Authenticate: Bearer error="insufficient_scope"`), 503 `auth_unavailable`. Evidence: `packages/chassis/tests/test_oidc_adapter.py::TestOidcAuth` (the suite binding), `packages/chassis/tests/test_security_auth.py::test_run_scope_is_needed_on_every_run_interface`, `::test_feedback_route_needs_the_feedback_scope`, `::test_mcp_call_carries_the_callers_token_to_v1_run`.
- Content guardrails in LiteLLM for every key (Presidio hook; Prompt Guard 2 through the generic guardrail API and `guardrail-server`, 512-token windows). Gap (d) settled for now: per route, not per key. Evidence: `packages/guardrail-server/litellm/config.yaml` (not run against a real LiteLLM), `packages/guardrail-server/tests/test_guardrail_server.py::test_injection_past_the_first_window_is_blocked`, `packages/guardrail-server/tests/test_litellm_stub.py::test_prompt_guard_blocks_with_an_http_error`.
- Chassis guard stages `input`, `tool_output`, `output` as defense in depth; `on_unavailable` fail closed. On the OpenAI and Anthropic interfaces `guardrail_blocked` is 502, by the frozen `status_for` default. Evidence: `packages/chassis/tests/test_guard_stages.py` (16 tests), `tests/test_poc07_sec_engines.py` (two engines, `inprocess` and `sidecar`). Waits on: p50 and p95 per stage on a LiteLLM with guardrails (the Mac run; the CI stack has no LiteLLM).
- Red-team set: 18 cases; 12/18 on the `litellm` stack with the pattern stand-in, 11/18 on the `keyword` fake; owners of the misses: 022 H-6 (spaced-out and French injections, rerun on Prompt Guard 2), 054 H-16 (base64 tool output, write tool with a secret argument), 026 CH-4 (markdown image URL), PoC-8 (a streamed answer with a key) ([red-team note](2026-10-28-redteam.md), `tests/test_poc07_redteam.py::test_the_run_matches_the_recorded_pass_rates`). Waits on: the Mac run for Prompt Guard 2.
- Chassis RSS with regex redaction on, against ADR-001: waits on a load run in Docker (the Mac or PoC-8); not measured in PoC-7.
- Add a test that a `guardrails` key in a workload's model request never reaches LiteLLM (contract v6, D.3; none exists yet).
- Fix the generated `## Reuse` text (in `docs/planning/tools/reuse_map.py`, `H-6`): it still says "The Presidio library in the chassis for redaction before logging and publishing", which ADR-001 and ADR-008 rule out. suggested: "Regex redaction in the chassis; Presidio in LiteLLM and the Collector".
- Still open, carried: API keys for callers; `/metrics` scope; rate limits in Valkey (PoC-8).

### 014 H-7 Observability

- `OtelTelemetry`, run span on the request's W3C trace id, Collector fan-out to Langfuse and Jaeger (swap by exporter config). Evidence offline: `tests/test_poc07_obs_telemetry_swap.py::test_switching_the_backend_is_exporter_config_only`, `::test_fan_out_sends_the_same_spans_to_both`; trace join in the `sidecar` lane: `tests/test_poc07_obs_trace_join.py::test_one_trace_joins_chassis_workload_framework_model_and_tool[echo-langgraph]`, `[echo-openai-agents]`. Waits on: the `poc07-stack.yml` run id (`tests/test_poc07_stack.py`).
- Baggage `user.id` (the authenticated subject; none with auth off) and `session.id` over A2A; caller baggage never forwarded. Evidence: `packages/chassis/tests/test_otel_wiring.py::test_the_connector_sends_baggage_next_to_the_traceparent`, `packages/chassis/tests/test_security_auth.py::test_auth_off_puts_no_user_id_on_the_run_span`.
- Per-engine telemetry list (contract v6, F.3): `echo-langgraph` and `echo-openai-agents` run; nothing had to live in an event mapping, only the instrumentor and `inject_trace_headers` ([per-engine list](2026-10-28-per-engine-mapping.md)).
- The TypeScript twin of `workload_a2a.telemetry` is not built.
- Open, carried: named-entity redaction in the Collector log pipeline (it masks emails and phones by regex only today); head or tail sampling; whether a `remote` workload's spans are accepted (a user question, ADR-007).

### 017 H-4 Harness features

- `GatedEngine`: sync (score, retry, fallback via `ctx.model_route`, `end.status: fallback`; a passing retry is `ok`) and async (score after; every stream). Sample rate by request id hash. Judge tokens as a `metrics` event; chassis numbers `metrics.attempt`. Evidence: `packages/chassis/tests/test_evaluator_gate.py` (13 tests), `tests/test_poc07_evals_gate.py` (`echo-python`, `echo-langgraph`, `sidecar`).
- Open concern on gate cost: the judge prompt is 254 + 22 tokens; per 1M requests a small hosted judge costs $51 at rate 1.0 and $5.1 at 0.1 (suggested list prices), about 2.6 times the answer's own cost at rate 1.0 ([gate note](2026-10-28-evaluator-gate.md)). Waits on: the Mac run for real latency and judge-versus-encoder agreement. The decision on defaults (`sync`/1.0 today against the suggested `async`/0.1) is a user question in ADR-010.
- Decided by the code: with no fallback route the run ends in `evaluator_gate_failed`; the model proxy sends every call of the fallback attempt to the fallback route; an evaluator outage fails open (a user question in ADR-010).
- Known gaps: the gate does not shrink `budget.timeout_ms` across attempts, and the judge's tokens are not charged to `budget.max_tokens`. Async scores are not written to Langfuse yet.
- The epic's `harness.evaluator`, `timeout_ms`, `budget` names against `spec.harness` (contract v6, config table).

### 037 M-2 Eval gate in CI

- The tool picked for CI: DeepEval 4.3.2 ([trial](2026-10-28-eval-tool-trial.md)), with the 20-case reports for two engines in `notes/evals/` and one format, `eval-report.v0` (`tests/test_poc07_evals_report_format.py::test_both_engines_give_the_same_report_format`). CI: `poc07-evals` passed on commit `9d942ee`, [run 40730858164](https://github.com/odere-pro/agent-playground/actions/runs/40730858164).
- Langfuse datasets and experiments as the case and result store: `bakeoff.langfuse_datasets`, tested against a stub (`packages/bakeoff/tests/test_bakeoff_langfuse_datasets.py::test_bakeoff_langfuse_creates_the_dataset_and_one_run_per_report`). Waits on: a real Langfuse (the `poc07-stack.yml` run or the Mac) for the dataset name and the experiment runs.

### 023 C-5 AI-generated marking

- Headers `X-AI-Generated` and `X-AI-Disclosure-Required` on every public interface, complete and streaming; `spec.governance` with the load-time refusal. Evidence: `packages/chassis/tests/test_marking.py::test_limited_marks_every_interface_streaming_and_complete`, `::test_marking_false_is_refused_for_limited_and_high`, `tests/test_poc07_sec_marking.py::test_marking_and_redaction_hold_on_every_engine`.
- Still open: body field for OpenAI and Anthropic formats; flag on result events (suggested: CloudEvent extension `aigenerated`); `Access-Control-Expose-Headers`; legal on disclosure for `minimal` with marking on.

### 042 A-2 Evaluator gate and fallback

- The gate shape is decided (ADR-010). Open here: keep the `evaluator-facts` agent behind `EvaluatorPort` (needs an A2A adapter) or use `judge`.

### 081 R-8 Description scanning

- Snyk Agent Scan in CI in a sandbox, on our MCP servers: `.github/workflows/poc07-agent-scan.yml`, `deploy/agent-scan/` ([note](2026-10-28-agent-scan.md), `tests/test_poc07_agent_scan_static.py`). Waits on: a `SNYK_TOKEN` repo secret for the first real scan. The `scan` run's egress is not limited to `api.snyk.io` (suggested: an egress proxy, if it becomes a gate).

### Others touched (status bullets suggested)

- **064 D-1, 040 D-0, 072 C-3**: may consume `agents.feedback.received.v1`. 064 D-1's Langfuse decision reopens ADR-009 if it goes another way.
- **089 R-15**: register `feedback.v0.json`.
- **026 CH-4**: the workload's egress to the Collector's OTLP port is already in its allow-list; add the guardrail-server and the encoder service to the chassis's egress (not the workload's). A remote's path to the Collector is a user question (ADR-007). Owns the red-team miss `exfil-04-markdown-image` (an output URL allow-list).
- **075 E-3, 073 E-1, 074 E-2, 031 G-5**: the encoder adapter and the gate cost formula (ADR-010 item 9) are where their numbers plug in. Waits on: encoder agreement from the Mac run, when a model is available.
- **013 CH-2**: the model proxy now rewrites the route of every call in the gate's fallback attempt to the run's route (`chassis.model_route_forced`; `packages/chassis/tests/test_evaluator_gate.py::test_the_proxy_sends_every_call_of_the_fallback_attempt_to_the_fallback_route`).
- **054 H-16**: owns the red-team misses `tool-04-base64` and `exfil-03-write-tool-gets-secret` (an argument guardrail before MCP calls).

## ROWS

None proposed. Confirm at close that no size changes: 022 H-6 may shrink, since guardrails are LiteLLM config; 017 H-4 may grow by the async mode and the Langfuse score for async results.
