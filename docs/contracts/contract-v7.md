# Contract v7

The written contract after the first two PoC-8 builds: observability (014 H-7) and security (022 H-6, 023 C-5). It adds a public route, `GET /metrics`; a new error code, `rate_limited`; a new body field, `ai_marking`, on the OpenAI and Anthropic formats; a new CloudEvent extension, `aigenerated`, on result events; and two checks on the answer, the stream secret check and the output URL allow-list. Every change is additive over [contract v6](contract-v6.md), which stays as the PoC-7 record and still holds for everything this document does not name. Contracts v5 to v1 hold for what v6 does not name.

Status: **draft, 2026-10-29.** Written against the merged code at commit `2885e4e` (merges `9e7adee`, observability, and `2885e4e`, security). Where this text and the code differ, the code wins. Evidence is a test name or a command. What still needs a run outside this container says `Waits on:`. A choice only the user can make is marked `OPEN (user):`. The planning doc is [PoC-8](../planning/poc/008-PoC-8-build-cross-cutting.md). The decisions are still [ADR-007](../planning/adr/007-observability-otel-collector-langfuse.md) (observability) and [ADR-008](../planning/adr/008-security-identity-guardrails-defense-in-depth.md) (security); both are Proposed.

Contract v7 is a version of this document, not of the wire. The event schema stays `schema_version: "0"`. The previous major stays accepted: nothing here removes or renames a field, a route, or a code.

## Decisions at a glance

| | Change | Contract it touches | `inprocess` | `sidecar` | `remote` |
| - | ------ | ------------------- | ----------- | --------- | -------- |
| A | `GET /metrics`, Prometheus text | A new public route; `spec.observability.metrics_scope`, `prices` | Served from the fake telemetry's registry | Served by the chassis container | Same; the remote is not involved |
| B | JSON logs, the router span, dashboards, Collector log masking | Not the wire. Log format, span names, deploy files | Same format | Same | Same |
| C | Rate limit per caller, 429 `rate_limited` | The run routes' and `/v1/feedback`'s refusals; `spec.limits`; `StatePort` keys | `InMemoryState` | Valkey, shared by every replica | Same as `sidecar` |
| D | Stream secret check and output URL allow-list | What an answer may hold; `spec.guardrails.stream_secrets`, `output_url_allow` | In the pipeline | In the chassis | In the chassis; a remote cannot skip it |
| E | `ai_marking` body field, `aigenerated` extension, `Access-Control-Expose-Headers` | The OpenAI and Anthropic bodies; the result event's CloudEvent attributes; a response header | Set | Set | Set |

## What did not change

- **The frozen surface of contract v5, part C.2.** `handle`, `TaskInput`, `Context`, the six events, the envelope (`Request`, `Response`), the A2A mapping in chassis mode, and the frozen error codes with their `retryable`. `schema_version` stays `"0"`. `SUPPORTED_SCHEMA_VERSIONS` stays `("0",)`.
- **The schema files.** Only `chassis-config.v0.json` changed: `git diff --stat d11769f..2885e4e -- packages/chassis/schemas/` lists it alone. `packages/chassis/tests/test_contract_freeze.py` passes.
- **`chassis.core`.** No change at all: `git diff --stat d11769f..2885e4e -- packages/chassis/src/chassis/core` is empty. `rate_limited` takes its message from `chassis.server.rate_limit.MESSAGES`, not `PUBLIC_MESSAGES`.
- **`task-result.v1.json`.** Unchanged. The marking flag is a CloudEvent extension (E.2), not a payload field.
- **No marking field on `Response`.** That would still be a bump to `"1"`. The body field is on the OpenAI and Anthropic formats only, which are their own surfaces (E.1).
- **No new port.** The rate limit uses `StatePort`'s existing `get`, `set_if_absent`, and `compare_and_set`. The two answer checks call no port.
- **The model proxy's wire formats, the remote listener's routes, idempotency, `/ready`, shutdown, auth, and the three guard stages.** As in v6.

---

## A. `GET /metrics`

### A.1 The route

- On the public app only. Never on the proxy app or the remote listener. Not in the OpenAPI document (`packages/chassis/tests/test_metrics.py::test_metrics_are_not_in_the_openapi_document`).
- The answer is the Prometheus text format 0.0.4 (`text/plain; version=0.0.4; charset=utf-8`). Every sample carries the label `agent`, the chassis's `agent.name`.
- Per replica, in memory. Prometheus scrapes and sums the replicas. Nothing is counted for Prometheus alone: the registry is fed by `TelemetryPort.counter` and the timed spans, in both `InMemoryTelemetry` and `OtelTelemetry` (`::test_both_telemetry_adapters_feed_their_registry`).
- In the `fake` profile it serves with no network and no key (`::test_the_fake_profile_serves_metrics_with_no_network_or_key`).

### A.2 Who may read it

| `spec.observability.metrics_scope` | No token | Token without the scope | Token with the scope |
| ---------------------------------- | -------- | ----------------------- | -------------------- |
| unset (default) | 200, as `/health` | 200 | 200 |
| a scope name | 401 `unauthenticated` | 403 `forbidden`, `WWW-Authenticate: Bearer error="insufficient_scope", scope="<scope>"` | 200 |

suggested: the default. With no scope, an ASGI middleware outside caller auth answers it, so a scraper needs no credential even with auth on (`::test_metrics_need_no_token_by_default_even_with_auth_on`, `::test_a_metrics_scope_when_set_is_required`). OPEN (user): whether `/metrics` stays open by default. PoC-7 left the scope open; this is the pick until the user decides.

### A.3 The families

suggested: every name, the bucket bounds, and the timed spans. These are declared, so they show `# HELP` and `# TYPE` before anything is counted (`::test_the_dashboard_families_are_declared_before_anything_is_counted`):

| Family | Type | Labels |
| ------ | ---- | ------ |
| `chassis_requests_total` | counter | `interface` |
| `chassis_run_duration_seconds` | histogram | `interface`, `status` (`ok`, `error`, `cancelled`) |
| `chassis_run_errors_total` | counter | `interface`, `code` |
| `chassis_model_calls_total` | counter | `route` (the route asked for) |
| `chassis_model_call_duration_seconds` | histogram | `route`, `status` |
| `chassis_model_tokens_total` | counter | `route`, `direction` (`input`, `output`) |
| `chassis_model_cost_microusd_total` | counter | `route`; only for a route with a price |
| `chassis_tool_calls_total` | counter | `tool` |
| `chassis_gate_runs_total` | counter | `mode` |
| `chassis_gate_retries_total` | counter | |
| `chassis_gate_fallbacks_total` | counter | |

- Any other `TelemetryPort.counter(name, ...)` shows as `<name>_total`, with `.` and every character outside `[a-zA-Z0-9_]` made `_`. So the PoC-7 and PoC-8 counters appear too, for example `chassis_guardrail_blocked_total`, `chassis_rate_limited_total`, `chassis_output_url_masked_total`.
- Timed spans: `chassis.run`, `chassis.model.call`, `chassis.tool.call`, `chassis.evaluator.score`. Buckets from 0.005 s to 120 s.
- Tokens and cost come from the model proxy's usage, on both proxy routes (`::test_model_tokens_and_cost_come_from_the_proxy_usage`). The cost is `input_tokens * input_per_mtok + output_tokens * output_per_mtok`, in whole micro-USD. LiteLLM's response cost header is not read: `ModelPort` does not carry it. LiteLLM's own spend stays the bill; this is the dashboard's estimate.

---

## B. Outside the wire

Recorded so the operators' surface is in one place. None of it is a wire contract.

- **JSON logs** (`chassis.logs`). One JSON object per line on stderr: `time` (UTC, milliseconds, `Z`), `level`, `logger`, `message`, `agent`, the record's extra fields, and `exception` when there is a traceback. Inside a run, `request_id` and `trace_id` (32 lowercase hex). uvicorn's lines take the same format. The message, the fields, and the traceback pass `chassis.redaction`; the ids do not. The level is `spec.observability.log_level`. Evidence: `packages/chassis/tests/test_json_logs.py::test_inside_a_run_a_line_carries_the_request_id_and_a_32_hex_trace_id`, `::test_the_message_extra_fields_and_the_traceback_are_redacted`, `::test_serve_logs_json_and_uvicorn_keeps_no_config_of_its_own`.
- **The router span** `chassis.model.route` (suggested). One per admitted model proxy call, in the run's trace, with `route.requested` (the `model` the workload named), `route` (the one called), and `route.forced` (the gate's fallback won). The provider call is LiteLLM's own span. An uncorrelated call has one too (`packages/chassis/tests/test_router_span.py::test_every_proxied_model_call_has_a_route_span_in_the_run_trace`, `::test_an_uncorrelated_call_has_a_route_span_too`).
- **Dashboards.** One Grafana dashboard per agent in `deploy/dashboards/<agent>.json`, generated by `uv run python -m chassis.dashboards` from one template for the agents in `deploy/dashboards/agents.txt`. Panels: requests, latency, errors, tokens, cost, and the gate's fallback rate, each filtered on `agent`. Never edit the JSON by hand. `packages/chassis/tests/test_dashboards.py::test_every_panel_names_a_metric_the_chassis_exports` checks every query against a real scrape; `::test_the_generated_files_are_up_to_date` fails on drift. suggested: the panels, the 5 m window, the layout. Waits on: a load into a real Grafana.
- **Workload log masking in the Collector** (`deploy/compose/poc07/otel-collector.yaml`). The log pipeline masks emails, IBANs, card numbers, US SSNs, and phone numbers by regex, phones last, with the chassis's tokens (`<EMAIL_ADDRESS>`, `<IBAN_CODE>`, `<CREDIT_CARD>`, `<US_SSN>`, `<PHONE_NUMBER>`). suggested: the patterns. Named entities (names, places) are still not masked there. Evidence offline: `packages/chassis/tests/test_collector_log_redaction.py::test_the_log_pipeline_masks_the_chassis_entity_set_on_body_and_attributes`. Waits on: a stack run that sends a workload log line through the real Collector.

---

## C. Rate limits

### C.1 What is limited

- One token bucket per agent and per caller, in `StatePort`. Every replica that shares the store shares the limit (`packages/chassis/tests/test_rate_limit.py::test_two_replicas_share_the_limit_through_the_store`).
- The caller is the checked `Principal.subject`. The key is `ratelimit:<agent>:<sha256(subject)[:32]>`, so the store never holds the IdP's subject (`::test_the_store_key_never_holds_the_raw_subject`).
- Counted routes: `POST /v1/run`, the OpenAI interface and the Anthropic interface (public port), and `POST /v1/feedback`. Not `/v1/mcp`: its tool calls reach `/v1/run` in process with the caller's token and count there once. Not `/health`, `/ready`, `/manifest`, `/metrics`, or the OpenAPI documents (`::test_reads_and_probes_are_not_limited`).
- Only callers with an identity. With auth off (`NoAuth`) there is no caller to tell apart, so no limit applies (`::test_with_auth_off_there_is_no_caller_to_count`). LiteLLM's key budget stays the cluster-wide cap.

### C.2 The new error code

| Code | Where | HTTP status | `retryable` | Public text (suggested) |
| ---- | ----- | ----------- | ----------- | ----------------------- |
| `rate_limited` | The counted routes | 429, `Retry-After: <whole seconds until one token is back>` | true | `too many requests from this caller; retry after the Retry-After delay` |

- The refusal comes before the body is read or the run opens. The engine never sees the call (`::test_a_refused_call_never_reaches_the_engine`).
- The body is in the route's own shape: OpenAI's and Anthropic's error with `type: rate_limit_error` (suggested), `{"detail": {"code", "message"}}` elsewhere (`::test_past_the_limit_every_run_interface_answers_429_in_its_own_shape`).
- A store outage follows `spec.limits.rate_limit_on_unavailable`. `fail_open` (default) lets the call through, counts `chassis.rate_limit_unavailable`, and logs a warning. `fail_closed` answers 503 `state_unavailable` with `Retry-After: 1`, a code that already exists (`::test_a_store_outage_fails_open_by_default`, `::test_a_store_outage_fails_closed_by_config`).
- A refusal is counted as `chassis.rate_limited{path_class}` (suggested).

This is a new code, so it is compatible (contract v5, C.3). A client that does not know it sees a 429 with `Retry-After`, which HTTP already defines.

---

## D. Two checks on the answer

### D.1 The stream secret check

The `output` stage holds back a complete answer, but a stream's deltas leave as they come (v6, D.2). PoC-8 adds a check in the chassis for every streamed run, in every lane, with any guardrail adapter, `none` included (`chassis.server.stream_guard`):

- A last word that could start a secret or a link is held until the next whitespace, so a key is whole before any of it leaves. Plain words leave at once, and deltas keep their boundaries (`packages/chassis/tests/test_output_filter.py::test_a_key_split_across_deltas_never_leaves_in_part`, `::test_plain_text_keeps_its_delta_boundaries`).
- A match on the secret patterns (an `sk-` key, an AWS access key id, a private key block, a `*_API_KEY=` assignment, a bearer token; suggested) ends the stream. The held text is dropped and the last event is one `error` `guardrail_blocked`, not retryable, with the fixed public text (`::test_a_streamed_answer_with_a_key_is_closed_with_guardrail_blocked`). `end.output.text` is checked too (`::test_the_end_text_of_a_stream_is_checked_too`).
- Counted as `chassis.guardrail_blocked{stage="output_stream", reason="secret"}`. `output_stream` is a new label value, not a new `GuardrailPort` stage.
- What it cannot do: text flushed at a non-delta event (`tool_call`, `metrics`) can leave in part before the rest is blocked. It is regex only: an encoded or spelled-out key passes.

No new code: `guardrail_blocked` exists (v6, D.4).

### D.2 The output URL allow-list

- In a complete or streamed answer, the destination of every markdown image or link, reference definition, and HTML `src=` or `href=` is removed when its URL is absolute and its host is not on `spec.guardrails.output_url_allow`. The markup stays and the destination becomes empty (`![alt]()`). A relative URL stays. Only `http` and `https` can be allowed. Bare URLs in text are not touched (`packages/chassis/tests/test_output_filter.py::test_mask_urls_removes_images_links_definitions_and_html_to_other_hosts`, `::test_a_markdown_image_to_another_host_is_masked`).
- An entry is a host, a subdomain wildcard (`*.example.com`), or `*`, which turns the check off (`::test_mask_urls_with_star_changes_nothing`, `::test_an_allowed_host_stays`).
- **The default is empty: default deny.** This changes what an answer may hold. An agent whose answers link to outside hosts must list them, or set `*`.
- A complete answer is masked after the `output` stage, so the envelope, the idempotency cache, and the result event hold the masked text (`::test_the_masked_complete_answer_is_what_the_envelope_holds`). Counted as `chassis.output_url_masked` (suggested).

---

## E. AI-generated marking, the rest of 023 C-5

The headers of v6, E.1 stay. PoC-8 adds what v6, E.3 deferred. All three follow the same condition as the headers: `spec.governance` turns marking on. With marking off, none is sent.

### E.1 The body field

| Format | Where | Field (suggested) |
| ------ | ----- | ----------------- |
| OpenAI interface (public port) | A complete `chat.completion`, and every `chat.completion.chunk` of a stream | `"ai_marking": {"ai_generated": true, "disclosure_required": true}`, top level |
| Anthropic interface (public port) | A complete `message`, and the `message` in `message_start` of a stream | Same |

- Neither format has a standard field for this. Both SDKs keep an unknown top-level field on the parsed object. The chassis's flags always win over anything the workload sent.
- Evidence: `packages/chassis/tests/test_marking_body.py::test_the_openai_body_carries_the_flags_complete_and_on_every_chunk`, `::test_the_anthropic_body_carries_the_flags_complete_and_on_message_start`, `::test_marking_off_adds_no_body_field`.
- The native envelope and `/v1/mcp` get no body field: `Response` is frozen.

### E.2 The CloudEvent extension

- Result events carry the CloudEvent extension attribute `aigenerated: true` when marking is on. It is absent otherwise. suggested: the name.
- An extension, so `task-result.v1.json` does not change (`::test_result_events_carry_the_aigenerated_extension`).

### E.3 `Access-Control-Expose-Headers`

- Every marked response names `X-AI-Generated` and `X-AI-Disclosure-Required` in `Access-Control-Expose-Headers`, merged with any value already there, as one header. A browser client of a CORS-enabled ingress can then read them (`::test_the_marked_response_exposes_the_marking_headers_to_browsers`).

---

## Config reference

New fields. suggested: every name and default. `chassis-config.v0.json` keeps its major: every new field is optional or has a default. "Reloads" follows `RELOADABLE` in `chassis.server.config`.

| Field | Default | Reloads |
| ----- | ------- | ------- |
| `spec.observability.log_level` | `info` (`debug` \| `info` \| `warning` \| `error`) | No |
| `spec.observability.metrics_scope` | unset (no token for `/metrics`) | No |
| `spec.observability.prices` | `{}`; per route `{input_per_mtok, output_per_mtok}`, USD per million tokens, each `>= 0` | No |
| `spec.limits.rate_limit` | `true` | **Yes** |
| `spec.limits.requests_per_minute` | `600` (refill rate, `>= 1`) | **Yes** |
| `spec.limits.request_burst` | `100` (bucket size, `>= 1`) | **Yes** |
| `spec.limits.rate_limit_on_unavailable` | `fail_open` (`fail_open` \| `fail_closed`) | **Yes** |
| `spec.guardrails.stream_secrets` | `true` | No |
| `spec.guardrails.output_url_allow` | `[]` (deny every absolute link and image URL); `*` turns it off | No |

The rate limit defaults are far above any test or interactive client, so turning it on breaks no existing call (`packages/chassis/tests/test_rate_limit.py::test_the_defaults_are_on_and_generous`).

**Against the epic's agent config** (C.1 of the epic, read-only): `spec.observability.log_level` now exists, as the epic names it. `trace_sample_rate` is still the Collector's (ADR-007 item 8).

## Cost per lane

- **`inprocess`.** The registry and the stream guard run in memory. The rate limit uses `InMemoryState`.
- **`sidecar`.** Per counted call with an identity: one `StatePort` read and one compare-and-set (Valkey), more on a lost race, up to 8 (suggested). Per streamed run: a regex pass over the held text and the last 256 characters sent (suggested). Per scrape: one render of the registry. Waits on: p50 and p95 added by the rate limit on Valkey, on the Mac.
- **`remote`.** The same. The checks run in the chassis, so a remote workload cannot skip them.

## Contract suites

| Suite | New or changed | Bound to |
| ----- | -------------- | -------- |
| Every port suite | Unchanged. No port changed | |
| `StatePortContract` | Unchanged. The rate limit uses only `get`, `set_if_absent`, and `compare_and_set` | |
| Freeze test | Unchanged, green | |
| Red-team runner | `--controls poc07\|poc08`. `poc07` turns off the two PoC-8 checks, so the PoC-7 record still reproduces | `packages/contract-suites/tests/test_redteam_controls.py::test_the_controls_are_poc07_and_poc08`, `::test_poc08_stops_the_two_cases_poc07_missed` |

Red-team pass rates, offline, with the pattern stand-in (`uv run python -m chassis_contracts.redteam --controls poc08`):

| Stack | PoC-7 controls | PoC-8 controls |
| ----- | -------------- | -------------- |
| `litellm` | 12/18 | 14/18 |
| `keyword` | 11/18 | 13/18 |

PoC-8 stops `exfil-04-markdown-image` (D.2) and `secret-04-streamed-answer-has-key` (D.1). Still missed on both stacks: `input-04-spaced-out`, `input-05-french`, `tool-04-base64`, `exfil-03-write-tool-gets-secret`; and on `keyword` only, `secret-01-input-asks-for-key`. Waits on: the Mac run on Prompt Guard 2.

## Open points

- **For the user:** whether `/metrics` stays open by default (A.2). The v6 open points still stand.
- **Known limits:** no rate limit with auth off (C.1); the stream check is regex only and cannot stop text flushed before a non-delta event (D.1); the cost is an estimate from `spec.observability.prices`, not LiteLLM's bill (A.3); no named-entity masking in the Collector (B).

## Links

- Planning doc: [PoC-8](../planning/poc/008-PoC-8-build-cross-cutting.md).
- Decisions: [ADR-007](../planning/adr/007-observability-otel-collector-langfuse.md), [ADR-008](../planning/adr/008-security-identity-guardrails-defense-in-depth.md).
- Backlog: [014 H-7](../planning/issues/014-H-7-observability.md), [022 H-6](../planning/issues/022-H-6-security-middleware.md), [023 C-5](../planning/issues/023-C-5-ai-generated-marking.md).
- The contract before this one: [contract v6](contract-v6.md). Contract v5, part C (the freeze), holds unchanged.
