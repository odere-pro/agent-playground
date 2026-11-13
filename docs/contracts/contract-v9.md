# Contract v9

The written contract after 136 CH-16, the model proxy under failure, and 135 CH-15, backpressure (section D, added 2026-11-09 in the CH-15 PR). It is additive over [contract v8](contract-v8.md): v8, and v7 to v1 before it, still hold for everything this document does not name. The rule in force is stated once in [docs/specs/contract.md](../specs/contract.md); this file is the record of what v9 added and why.

Status: **draft, 2026-11-08, section D 2026-11-09.** Written with the CH-16 PR and the CH-15 PR. Where this text and the code differ, the code wins. Evidence is a test name. The decision record is [ADR-013](../planning/adr/013-model-call-retries-belong-to-the-router.md).

Contract v9 is a version of this document, not of the wire:
- The event schema stays `schema_version: "0"`.
- `SUPPORTED_SCHEMA_VERSIONS` stays `("0",)`.
- No envelope field, event field, or A2A metadata key changes.

## A. The model port (`chassis.ports.model`)

1. **Three port-level codes.** `service_budget_exhausted` (the model service refused for the service's budget; not retryable), `timeout` (the call passed its `timeout_s`, or a transport timeout; retryable), `run_ended` (the run the call belongs to ended first; set by the proxy, never by an adapter; not retryable). Any other upstream status stays `http_<status>`.
2. **`timeout_s` on `complete` and `stream`.** The wall clock of the whole call, in seconds; `None` is the adapter's default (60 s per httpx operation, suggested). Past it the adapter raises `timeout` and ends the upstream call. Evidence: `ModelPortContract.test_a_call_ends_at_its_timeout`, bound for `ScriptedModel` and `LiteLLMModel`; `packages/chassis/tests/test_litellm_failures.py::test_a_call_past_its_deadline_closes_the_upstream_connection` (the fake model server on a real socket counts the close).
3. **No adapter retries.** One port call is one upstream call. The retries and the fallback routes are the model router's (`num_retries` and `fallbacks` per route in every LiteLLM config under `deploy/`). Evidence: `ModelPortContract.test_a_failed_call_goes_upstream_once`; `packages/chassis/tests/test_litellm_route_resilience.py`.
4. **LiteLLM's budget refusal.** A 4xx whose body is `{"error": {"type": "budget_exceeded", ...}}` (LiteLLM's `BudgetExceededError`; 400 or 429 across releases) is `service_budget_exhausted`. Evidence: `test_litellm_failures.py::test_litellms_budget_refusal_is_service_budget_exhausted`.

## B. The model proxy, both routes

5. **The service's budget: 429.** `service_budget_exhausted` answers 429 with the fixed text `the service's model budget is exhausted` and `x-should-retry: false`. On the Anthropic proxy route the type is `rate_limit_error`; on the chat route the body is `{"error": {"message", "type": "model_error", "code": "service_budget_exhausted", "retryable": false}}`, and mid-stream it is one error frame then `[DONE]`. Counted `chassis.model_budget_exhausted{route}`, exported as `chassis_model_budget_exhausted_total`. Evidence: `packages/chassis/tests/test_model_proxy_failures.py`, the four budget tests.
6. **The run's remaining time.** A correlated call's `timeout_s` is what the run has left of `budget.timeout_ms`; an uncorrelated call gets none. Evidence: `test_model_proxy_failures.py::test_a_correlated_call_gets_the_runs_remaining_time`.
7. **A run that ends mid-call: 403 `run_ended`.** The port call is cancelled (the adapter closes the upstream connection) and both routes answer 403 `permission_error`, `the run this call belongs to has ended`, not retryable, `x-should-retry: false`. It is the same class as `run_required`: no run in flight for this call. Evidence: `test_model_proxy_failures.py::test_a_chat_call_whose_run_ends_is_cut_and_answered_403_run_ended`, `::test_an_anthropic_call_whose_run_ends_is_cut_and_answered_403_run_ended`.
8. **`x-should-retry` on every chat-route error.** Every complete-call error on the chat route carries `x-should-retry` from `retryable` (the Anthropic route always did). The OpenAI and Anthropic SDKs read it before their own status rules, so a non-retryable 500 or 429 is not retried by the client. Evidence: `test_model_proxy_failures.py::test_every_chat_error_carries_x_should_retry_from_retryable`.

## C. Operations

9. **The alert.** `ChassisModelBudgetExhausted` in `deploy/prometheus/rules/chassis.yaml` fires on any increase of `chassis_model_budget_exhausted_total` over 15 minutes (`for: 1m`, `severity: critical`; suggested). The PoC-8 Compose stack mounts the file. Evidence: `packages/chassis/tests/test_alert_rules.py`; the fault test `pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_metrics.py::test_a_budget_refusal_raises_the_counter_and_fires_the_alert` (`COMPOSE_STACK=1`).
10. **The fake model server.** An error rule with a `type` answers in LiteLLM's error shape; a rule with `delay_s` holds the answer and counts a client that leaves in `app.state.aborted`.

## D. Backpressure (135 CH-15)

11. **`spec.limits.max_in_flight`** (32, suggested): the most runs in flight on one replica, counted in the run registry (`len(app.state.runs)`). Reloadable with `spec.limits`; read per call. Evidence: `packages/chassis/tests/test_backpressure.py::test_max_in_flight_reloads_with_spec_limits_and_no_restart`.
12. **503 `overloaded`.** At the cap, every public interface refuses the next call before the engine sees it: `Retry-After: 1` (suggested), `retryable: true`; native `{"detail": {"code": "overloaded", "message"}}`, OpenAI `server_error` with `x-should-retry: true`, Anthropic `overloaded_error`. `/ready` stays 200; the drain order (ADR-012) is unchanged. Evidence: `test_backpressure.py::test_a_third_run_past_the_cap_is_503_overloaded_and_never_reaches_the_workload`, `::test_the_chat_interfaces_refuse_in_their_own_shape`, on a real uvicorn server over a Unix socket.
13. **Metrics.** `chassis_requests_refused_total{reason="overloaded"}` and the gauge `chassis_runs_in_flight`, set from the registry at scrape time. Evidence: `test_backpressure.py::test_metrics_show_the_refusal_and_the_runs_in_flight`.
14. **The chart.** `hpa.runsInFlight.enabled` adds a Pods metric on `chassis_runs_in_flight` (`targetAverageValue` 24, suggested) next to CPU; a metrics adapter must serve the gauge. Evidence: `pocs/poc-09-agent-mvp-template/tests/test_poc09_chart.py::test_hpa_scales_on_the_in_flight_gauge_when_asked`.

## E. A JSON-RPC error from the workload (009 CH-1, P-9)

Added 2026-11-13. Decided by the user on 2026-11-13 ([proposals.md P-9](../reviews/2026-11-06-backlog-review/proposals.md#p-9-a-json-rpc-error-from-the-workload), option 1). It replaces the v1 note "A JSON-RPC error answer from a server is not mapped by the connector".

15. **`a2a.rpc_error`.** A JSON-RPC error answer from the workload (a2a-sdk raises an `A2AError` that is not an `A2AClientError`) ends the run with one `error {code: "a2a.rpc_error", retryable: false}`, never an exception from `run`, so it is no longer the pipeline's `engine_error`. It applies in every lane, from the shared `A2AConnector`. The message is the exception text cut to `ERROR_MESSAGE_MAX` in chassis mode, and fixed text in the plain mode of `remote`. The code is additive: the frozen codes and their `retryable` values do not change (contract v5, C.3). Evidence: `packages/chassis/tests/test_a2a_cancel_rpc.py::test_a_jsonrpc_error_answer_is_an_a2a_code[*]` (`inprocess` and `sidecar`).
16. **The interfaces.** Not retryable, so 502 on the OpenAI and Anthropic interfaces by the existing row of `status_for`, and a 200 envelope with `status: error` on native and MCP. The public text is `the agent refused or failed the request` (`PUBLIC_MESSAGES`; suggested: the text). Evidence: `packages/chassis/tests/test_inbound.py::test_a_jsonrpc_error_from_the_workload_is_502_with_a_fixed_public_message`.
