# Contract v6

The written contract after PoC-7: four new ports (`AuthPort`, `GuardrailPort`, `EvaluatorPort`, `FeedbackPort`) and a real `TelemetryPort` adapter; caller auth and scopes on the public port; three guard stages; a new public route, `POST /v1/feedback`, and its CloudEvent `agents.feedback.received.v1`; AI-generated marking as response headers; W3C `baggage` over A2A; and the evaluator gate, built on fields that already exist. Every change is additive over [contract v5](contract-v5.md), which stays as the PoC-6 record and still holds for everything this document does not name. Contracts v4 to v1 hold for what v5 does not name.

Status: **draft, 2026-10-28.** First written at commit `9f2f240`, then checked against the merged PoC-7 code at commit `9d942ee` (W1 to W4). Where this text and the code differ, the code wins. Evidence is a test name or a note. What still needs a run outside this container says `Waits on:` and names the run. A choice only the user can make is marked `OPEN (user):`; the rest of the old open points are decided by the code or deferred to a named PoC-8 issue. The planning doc is [PoC-7](../planning/poc/007-PoC-7-cross-cutting-decisions.md). The decisions are [ADR-007](../planning/adr/007-observability-otel-collector-langfuse.md) (observability), [ADR-008](../planning/adr/008-security-identity-guardrails-defense-in-depth.md) (security), [ADR-009](../planning/adr/009-feedback-langfuse-scores-event.md) (feedback), and [ADR-010](../planning/adr/010-evals-offline-tool-online-gate.md) (evals).

Contract v6 is a version of this document, not of the wire. The event schema stays `schema_version: "0"`.

## Decisions at a glance

| | Decision | Contract it touches | `inprocess` | `sidecar` | `remote` |
| - | -------- | ------------------- | ----------- | --------- | -------- |
| A | Four new ports with fakes, no-op defaults, and suites; `OtelTelemetry` for `TelemetryPort` | Ports, `spec.adapters`. Not the envelope, events, `handle`, or the A2A mapping | Fakes; no network | Real adapters in the chassis container | Same as `sidecar`: every port lives in the chassis |
| B | `POST /v1/feedback` | A new public route; `StatePort` gains one record per run; `spec.feedback` | Route on the in-process app | Route on the chassis's public port | Same; the remote is not involved |
| C | `agents.feedback.received.v1` with payload `feedback.v0.json` | A new event type on `EventPort` (a new topic, not a change to a chassis event) | Fake bus | Broker | Broker |
| D | Caller auth and scopes; guard stages `input`, `tool_output`, `output`; new error codes | The public routes' refusals; the tool proxy's tool errors; `spec.security`, `spec.guardrails`; `PUBLIC_MESSAGES` entries | Fake auth and guardrails | One LiteLLM call per enabled stage | Same; `tool_output` also covers the remote's tool calls on the remote listener |
| E | `X-AI-Generated` and `X-AI-Disclosure-Required` response headers | The public interfaces' response headers; `spec.governance`. Not `Response` | Headers set | Headers set | Headers set |
| F | W3C `baggage` on every A2A request of a run, next to `traceparent` | The A2A transport's HTTP headers. Not the A2A mapping's metadata keys | Header on the ASGI transport | Header on localhost | Header over the network. OPEN (user): no `user.id` to an `untrusted` remote? |
| G | The evaluator gate (`GatedEngine`) | `spec.harness`, `spec.model.fallback_route`; uses `metrics.attempt`, `metrics.model_route`, `end.status: fallback`, `Context.model_route` as they are | A retry is a second in-memory run | A second localhost run | A second network run in gVisor |

## What did not change

- **The frozen surface of contract v5, part C.2.** `handle`, `TaskInput`, `Context`, the six events, the envelope (`Request`, `Response`), the A2A mapping in chassis mode, and the frozen error codes with their `retryable`. `schema_version` stays `"0"`. `SUPPORTED_SCHEMA_VERSIONS` stays `("0",)`.
- **The five schema files.** `events.v0.json`, `task_input.v0.json`, `context.v0.json`, `request.v0.json`, `response.v0.json` are byte for byte as at the end of PoC-6. Evidence at `9d942ee`: `packages/chassis/tests/test_contract_freeze.py` passes (`::test_frozen_schema_file_is_byte_identical` among its tests), and `git diff --stat main..HEAD -- packages/chassis/schemas/` lists only `chassis-config.v0.json` and the new `feedback.v0.json`.
- **No user, session, or marking field in a frozen surface.** User and session ids are baggage and span attributes (F). The marking is a header (E). The evaluator's score is a span attribute and a Langfuse score, never a `metrics` field (G).
- **`chassis.core`**, except entries added to `PUBLIC_MESSAGES` in `core/inbound.py` for the new codes. That is the one allowed exception (contract v5, C.3). As built, `git diff --stat main..HEAD -- packages/chassis/src/chassis/core` is `core/inbound.py | 5 +`: three `PUBLIC_MESSAGES` entries (`evaluator_gate_failed`, `guardrail_blocked`, `guardrail_unavailable`). `status_for` does not change, so these codes take its default rule (D.4).
- **The model proxy's wire formats, the remote listener's routes, `RequireRun`, `BearerAuth`, idempotency, `/ready`, shutdown.** As in v5. `tool_output` adds a check on the tool proxy, not a route.
- **`task-result.v1.json` and the result events.** Unchanged. The `ai_generated` flag on result events is deferred to 023 C-5 (E.3).

---

## A. The ports

All four keep the PoC-1 shape: a `Protocol` with a `name`, one async method, a frozen value type, an error with `code`, `message`, and `retryable`, and a no-op class that is the `PortBundle` default, so a bundle built before PoC-7 still builds. Adapters are picked by `spec.adapters.<port>`, never in code. Each fake and each real adapter binds the same suite in `packages/contract-suites`.

### A.1 `AuthPort` (`chassis.ports.auth`)

```python
@dataclass(frozen=True)
class Principal:
    subject: str
    scopes: frozenset[str] = frozenset()
    issuer: str | None = None
    def allows(self, scope: str) -> bool: ...

class AuthPort(Protocol):
    name: str
    async def authenticate(self, token: str | None) -> Principal: ...
```

- `token` is the bearer value without `Bearer `, or `None`. The port says who the caller is and which scopes it holds. Which scope a route needs is `spec.security`'s rule (D.1), never the port's.
- Errors (`AuthError`): `unauthenticated` (no token, or forged, expired, wrong issuer or audience, malformed; not retryable) and `auth_unavailable` (the IdP's keys could not be read; retryable). The message never holds the token.
- `NoAuth` (`none`) answers the anonymous principal with the scope `*`, which allows every route.

| `spec.adapters.auth` | Class | Notes |
| -------------------- | ----- | ----- |
| `none` | `NoAuth` | Default in `fake` and `local` |
| `static` | `chassis.fakes.auth:StaticTokens.from_env` | Fake. Tokens from `CHASSIS_STATIC_TOKENS` (suggested: JSON `{token: {subject, scopes}}`). Refused in `cloud` |
| `oidc` | `chassis.adapters.oidc:OidcAuth.from_env` | Default in `cloud`. A JWT checked against the IdP's JWKS. Env (suggested names): `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL`. RS256 or ES256 only. JWKS cached 300 s; an unknown `kid` refetches once, at most once per 10 s. Clock leeway 30 s (all suggested). Suite: `packages/chassis/tests/test_oidc_adapter.py::TestOidcAuth` |

Suite: `AuthPortContract` (a good token gives its principal; the same token gives the same principal; a missing token and every bad token are `unauthenticated`; an outage is retryable).

### A.2 `GuardrailPort` (`chassis.ports.guardrail`)

```python
Stage = Literal["input", "output", "tool_output"]

@dataclass(frozen=True)
class Verdict:
    allowed: bool
    text: str                       # the text to pass on, PII masked; unused when not allowed
    reasons: tuple[str, ...] = ()   # "prompt_injection", "pii", or the adapter's own

class GuardrailPort(Protocol):
    name: str
    async def check(self, text: str, stage: Stage) -> Verdict: ...
```

- Errors (`GuardrailError`): `guardrail_unavailable` (retryable). What an outage does is `spec.guardrails.on_unavailable` (D.3).
- `split_for_classifier(text, pieces=384, overlap=32)` is the chunking rule for a classifier that reads 512 tokens. suggested: the two numbers. It counts word and punctuation pieces, which a subword tokenizer splits further, so the budget keeps headroom. `LiteLLMGuardrails` uses it for the guardrail names in `LITELLM_GUARDRAIL_CHUNKED` (`packages/chassis/tests/test_litellm_guardrails.py::test_a_long_tool_output_is_checked_per_chunk`, `packages/chassis/tests/test_redaction_and_chunks.py::test_long_text_chunks_overlap_and_cover_everything`). `guardrail-server` does not use this rule: it windows with the model's own tokenizer (512 tokens) for Prompt Guard 2, and on words (512, 64 shared) for the pattern stand-in (`packages/guardrail-server/tests/test_guardrail_server.py::test_pattern_chunks_overlap_so_a_boundary_injection_is_whole`).
- `NoGuardrails` (`none`) passes every text unchanged.

| `spec.adapters.guardrails` | Class | Notes |
| -------------------------- | ----- | ----- |
| `none` | `NoGuardrails` | Default in `fake` and `local` |
| `fake` | `KeywordGuardrails` | Patterns stand in for Prompt Guard 2, regex redaction for Presidio |
| `litellm` | `chassis.adapters.litellm.guardrails:LiteLLMGuardrails.from_env` | Default in `cloud`. LiteLLM's `POST /guardrails/apply_guardrail` with `LITELLM_BASE_URL` and `LITELLM_API_KEY`. Guardrail names per stage (suggested defaults): `LITELLM_GUARDRAIL_INPUT` `prompt-guard,pii-mask`; `LITELLM_GUARDRAIL_TOOL_OUTPUT` `prompt-guard,secret-guard,pii-mask`; `LITELLM_GUARDRAIL_OUTPUT` `secret-guard,pii-mask`; `LITELLM_GUARDRAIL_CHUNKED` `prompt-guard`. Suite: `packages/chassis/tests/test_litellm_guardrails.py::TestLiteLLMGuardrails` |

Suite: `GuardrailPortContract` (clean text passes unchanged at every stage; an injection is blocked at `input` and `tool_output`, also deep in a tool output over 1000 tokens; PII is masked at `output`; empty text passes; an outage is retryable).

### A.3 `EvaluatorPort` (`chassis.ports.evaluator`)

```python
@dataclass(frozen=True)
class EvalCase:
    input: str
    output: str
    reference: str | None = None

@dataclass(frozen=True)
class Score:
    value: float            # in [0, 1]; higher is better
    evaluator: str
    reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    latency_ms: float = 0.0

class EvaluatorPort(Protocol):
    name: str
    async def score(self, case: EvalCase) -> Score: ...
```

- The port scores. It does not decide pass or fail: the gate compares with `spec.harness.threshold`.
- Errors (`EvaluatorError`): `evaluator_unavailable` (retryable), `evaluator_bad_reply` (an answer that is not a score), `evaluator_not_configured` (`NoEvaluator`). None reaches a caller: the gate counts an error as a failed attempt (G.2).
- `NoEvaluator` (`none`) raises `evaluator_not_configured`. The gate refuses to start with it.

| `spec.adapters.evaluator` | Class | Notes |
| ------------------------- | ----- | ----- |
| `none` | `NoEvaluator` | Default in every profile |
| `fake` | `ScriptedEvaluator` | The first rule whose `match` is in the answer gives the score |
| `judge` | `chassis.adapters.evaluators:JudgeEvaluator.from_env` | An LLM judge through `ModelPort`, on route `CHASSIS_JUDGE_ROUTE` (suggested: `judge`). Fixed rubric `judge-facts-v1` as the system message; the case in `<input>`, `<reference>`, `<answer>` tags, closing tags escaped; temperature 0. The reply must be exactly `{"score": 0..1, "reason": str}`, else `evaluator_bad_reply`. Cost from `CHASSIS_JUDGE_PRICE_IN_PER_MTOK` and `CHASSIS_JUDGE_PRICE_OUT_PER_MTOK`, else `None`. Suite: `packages/chassis/tests/test_evaluators.py::TestJudgeEvaluator` |
| `encoder` | `chassis.adapters.evaluators:EncoderEvaluator.from_env` | Hugging Face TEI `POST {CHASSIS_ENCODER_URL}/predict` with one pair, `{"inputs": [[answer, input]], "truncate": true}`; the reply is a list of `{label, score}` per pair, and the score is the `entailment` label's. Zero tokens. Suite: `packages/chassis/tests/test_evaluators.py::TestEncoderEvaluator` |

Suite: `EvaluatorPortContract` (scores are in range and ordered; the decision repeats; the cost is reported; an outage is retryable).

### A.4 `FeedbackPort` (`chassis.ports.feedback`)

```python
class Feedback(BaseModel):          # extra="forbid", frozen
    feedback_id: str                # min length 1
    request_id: str                 # min length 1
    trace_id: str                   # 32 lowercase hex, the W3C trace id
    score: float                    # in [0, 1]
    label: str | None = None        # at most 64 characters
    comment: str | None = None      # at most 2000 characters (COMMENT_MAX, suggested)
    subject: str | None = None      # the caller's Principal.subject, never a body value
    timestamp: datetime | None = None  # the first time this feedback_id was seen; a repeat sends it again

class FeedbackPort(Protocol):
    name: str
    async def submit(self, feedback: Feedback) -> None: ...
```

- Idempotent per `feedback_id`: the same id twice is one record.
- Errors (`FeedbackError`): `feedback_unavailable` (retryable), `feedback_rejected` (the store answered 4xx), `feedback_not_configured` (`NoFeedback`).

| `spec.adapters.feedback` | Class | Notes |
| ------------------------ | ----- | ----- |
| `none` | `NoFeedback` | Default in `fake` and `local`. The route answers 501 |
| `memory` | `InMemoryFeedback` | One record per id |
| `langfuse` | `chassis.adapters.langfuse:LangfuseFeedback.from_env` | Default in `cloud`. Langfuse `POST /api/public/scores`, a JSON list with one score, HTTP Basic auth. Score id = `feedback_id`, trace id = the W3C trace id, `label` in the score's `metadata` with `request_id` and `subject`. Env: `LANGFUSE_BASE_URL` (or the older `LANGFUSE_HOST`), `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`. Suite: `packages/chassis/tests/test_langfuse_feedback.py::TestLangfuseFeedback`, offline against `fake-langfuse` |

Suite: `FeedbackPortContract` (submitted feedback is stored; optional fields may be empty; a timestamp is kept; the same id twice is one record; an outage is retryable). Bound against a real Langfuse v4 in `poc07-stack` run 40735435727 on `dd1d594`: 4 passed, the outage case skipped (`pocs/poc-07-cross-cutting-decisions/tests/test_poc07_stack.py::TestLangfuseFeedbackOnTheStack`).

### A.5 `TelemetryPort`: the `otel` adapter

The port does not change. `spec.adapters.telemetry: otel` is `chassis.adapters.otel:OtelTelemetry.from_env`: the OpenTelemetry SDK, OTLP over HTTP, configured by the standard `OTEL_EXPORTER_OTLP_*` and `OTEL_SERVICE_NAME` variables. The run span's trace id is the request's W3C trace id, and its span id is the parent id of the `traceparent` sent to the workload. Suite: `packages/chassis/tests/test_otel_telemetry.py::TestOtelTelemetry`. The workload span under the run span: `::test_the_connector_traceparent_names_the_engine_span`, and on two engines `pocs/poc-07-cross-cutting-decisions/tests/test_poc07_obs_trace_join.py::test_one_trace_joins_chassis_workload_framework_model_and_tool[echo-langgraph]`, `[echo-openai-agents]`.

### A.6 Profiles

| Port | `fake` | `local` | `cloud` |
| ---- | ------ | ------- | ------- |
| `telemetry` | `memory` | `memory` (as before) | `otel` |
| `auth` | `none` | `none` | `oidc` |
| `guardrails` | `none` | `none` | `litellm` |
| `evaluator` | `none` | `none` | `none` |
| `feedback` | `none` | `none` | `langfuse` |

`cloud` refuses the in-memory names `fake`, `memory`, and `static` (suggested). An agent names a port to switch it on in `fake` or `local`.

---

## B. `POST /v1/feedback`

### B.1 The route

On the public port only. Mounted when `spec.feedback.enabled` (default `true`) and `spec.adapters.feedback` is not `none`. `none` is the `fake` and `local` default, so their OpenAPI does not change. Not mounted: a FastAPI 404 (`packages/chassis/tests/test_feedback_route.py::test_the_route_is_off_without_an_adapter_or_when_disabled`). Needs `spec.security.feedback_scope` (D.1). Not on the proxy listeners, so no workload can call it.

### B.2 The body

JSON object, `extra="forbid"` (suggested names):

| Field | Rule |
| ----- | ---- |
| `request_id` | Required. A non-empty string. The id the chassis gave the run |
| `score` | Required. A number in [0, 1]. A JSON boolean is refused. suggested: thumbs down 0, thumbs up 1 |
| `label` | Optional. At most 64 characters |
| `comment` | Optional. At most 2000 characters |
| `feedback_id` | Optional. A non-empty string. When absent, the chassis derives one (B.5) |

Not in the body: `trace_id` (from the lookup, B.4) and `subject` (from the caller's principal). Either one sent is refused as an unknown field.

### B.3 Answers

| Status | Code | When | Retryable |
| ------ | ---- | ---- | --------- |
| 202 | | Written, or the same `feedback_id` was written before. Body: `{"feedback_id": "...", "trace_id": "..."}` | |
| 401 | `unauthenticated` | No token, or a bad token. `WWW-Authenticate: Bearer error="invalid_token"` | No |
| 403 | `forbidden` | A good token without `feedback_scope`. `WWW-Authenticate: Bearer error="insufficient_scope", scope="agent:feedback"` | No |
| 404 | `unknown_request` | No lookup record: an unknown `request_id`, an unfinished or expired run, or another agent's | No |
| 409 | `feedback_id_conflict` | The `feedback_id` was used for another request | No |
| 422 | (FastAPI's validation body) | Not JSON, an unknown field, a field of the wrong type or out of range | No |
| 501 | `feedback_not_configured` | `NoFeedback` injected | No |
| 502 | `feedback_rejected` | The store refused it (a 4xx, or a 207 with a refused score) | No |
| 503 | `auth_unavailable`, `state_unavailable`, `feedback_unavailable`, `not_ready` | The IdP's keys, the state store, or the feedback store did not answer. `Retry-After: 1` | Yes |

Errors use the native refusal body, `{"detail": {"code", "message"}}` (contract v2), with a fixed text from the route's own message table (`chassis.server.feedback.MESSAGES`). Evidence: `packages/chassis/tests/test_feedback_route.py::test_feedback_after_a_run_lands_on_its_trace`, `::test_a_bad_body_is_422`, `::test_an_unknown_request_id_is_404`, `::test_a_feedback_id_used_for_another_request_is_409`, `::test_no_feedback_port_is_501`, `::test_a_store_outage_is_503_retryable`, `::test_a_state_outage_on_lookup_is_503`; `packages/chassis/tests/test_langfuse_feedback.py::test_a_207_with_a_rejected_score_is_rejected`, `::test_wrong_keys_are_rejected_not_retryable`; `packages/chassis/tests/test_security_auth.py::test_feedback_route_needs_the_feedback_scope`.

### B.4 The lookup

One record per run in `StatePort`: `chassis:feedback:v1:req:<agent>:<sha256(request_id)>` -> `{"trace_id", "agent"}`, with the W3C trace id (`trace_id_hex(request.trace_id)`). It is written at run end, after the engine stream ended, through an `on_finished` callback; in complete mode it is there before the answer goes out. It lives `spec.feedback.lookup_ttl_s` (default 604800, seven days). A state failure on the write is counted (`chassis.feedback.lookup_failed`) and logged, never sent to the client (`packages/chassis/tests/test_feedback_route.py::test_the_lookup_is_in_state_with_the_spec_ttl`). The record holds no subject, so the route does not check who made the request. OPEN (user): bind the record to the caller's subject, so another caller gets 404? Waits on: the record's size in bytes and the Valkey memory at the PoC-4 load rate; not measured in PoC-7.

### B.5 Idempotency

By `feedback_id`. Langfuse upserts a score by id and timestamp; the fake keeps one entry per id. A body without `feedback_id` gets 32 hex of sha256(`request_id`, subject), so one caller has one feedback per request and the latest wins. The first time a `feedback_id` is seen, its time and request id go to `chassis:feedback:v1:id:<agent>:<sha256(feedback_id)>` (set if absent, same TTL). A repeat sends that first time again. A `feedback_id` already used for another request is 409. This is not the `Idempotency-Key` machinery: the route keeps no answer to replay (`packages/chassis/tests/test_feedback_route.py::test_a_repeat_is_one_record_with_the_first_timestamp`).

### B.6 Redaction

As built, the comment goes to the store as written: the user gave it on purpose, to be read with the trace. The event's `comment` passes through `chassis.redaction.redact`. Logs and spans never hold the comment. Names are not caught by the chassis (ADR-008 item 6). OPEN (user): redact the stored comment too?

---

## C. The event `agents.feedback.received.v1`

Published on `EventPort` when `spec.feedback.events` is `true` (default `false`), after the port's write succeeded, in the background, as the result events are (contract v3). A slow or broken broker never changes the route's answer. Not published for a refused call or a failed write.

- **The CloudEvent** (suggested, after contract v3's result events): `type` `agents.feedback.received.v1`; `id` the hex of a uuid5 of the feedback's content (`<feedback_id>:<score>:<label>:<comment>`), so a repeat of the same feedback has the same id and a changed score a new one; `source` `/agents/<agent>`; `subject` the `request_id`; `time` UTC now; `dataschema` `feedback.v0.json`; `traceparent` with the run's trace id; `partitionkey` the sha256 hex of the `request_id`, so feedback on one request keeps its order.
- **The payload** (`packages/chassis/schemas/feedback.v0.json`, `extra="forbid"`; suggested fields): `feedback_id`, `agent`, `request_id`, `trace_id`, `score`, `label`, `comment` (redacted), `subject` (the caller's subject as it is, not a hash), `received_at`. The payload schema has its own major, as `task-result.v1.json` has. Evidence: `packages/chassis/tests/test_feedback_route.py::test_the_event_is_published_redacted_and_valid`, `::test_a_failed_publish_never_changes_the_202`, `::test_events_off_publishes_nothing`.
- **The epic's event catalog (D.3)** gains one row: producer, any agent's chassis; consumers, the recorder (040 D-0), the golden set agent (064 D-1), the audit log (072 C-3). The epic is never edited. The row is recorded in `pocs/poc-07-cross-cutting-decisions/notes/backlog-changes.md`, and the schema is registered by 089 R-15.
- **Counters**: `chassis.events.published{topic}` and `chassis.events.publish_failed{topic}`, as for result events.

---

## D. Auth, guard stages, and the new error codes

### D.1 Scopes per route

| Route | Scope (`spec.security`, suggested defaults) |
| ----- | ------------------------------------------- |
| `POST /v1/run`, the OpenAI interface, the Anthropic interface (public port), `/v1/mcp` | `run_scope`: `agent:run` |
| `POST /v1/feedback` | `feedback_scope`: `agent:feedback` |
| `GET /manifest`, `/openapi.json`, `/docs`, `/redoc` | `read_scope`: `agent:read` |
| `GET /health`, `GET /ready` | none |

The check runs before limits, idempotency, and the run. The token is read from `Authorization: Bearer`, then removed: it never reaches the workload, a log, or a span. The in-process hop from `/v1/mcp` to `/v1/run` carries the checked principal; it is never trusted by loopback address or by `x-chassis-interface`. A path outside these classes needs a valid token and no scope. The public port serves no `/metrics` in PoC-7; where it goes is deferred to 014 H-7. The epic's `spec.security.scopes` list and `allowed_agents` against the three named scopes here are deferred to 022 H-6 (the config table). Evidence: `packages/chassis/tests/test_security_auth.py::test_run_scope_is_needed_on_every_run_interface`, `::test_read_scope_is_needed_for_the_manifest_and_docs`, `::test_health_and_ready_need_no_token`, `::test_every_other_route_needs_a_token`, `::test_mcp_call_carries_the_callers_token_to_v1_run`.

The proxy listeners are unchanged: loopback has no auth, the remote listener has `BearerAuth` and `RequireRun` (contract v4). Caller tokens are never accepted there.

### D.2 Guard stages

| Stage | `spec.guardrails.<stage>` | Where | Text checked | On a block |
| ----- | ------------------------- | ----- | ------------ | ---------- |
| `input` | `input: true` | The pipeline, after auth and limits, before the engine | `input.text`, then every string inside `input.data` (history, a system text), one per line | No run. Native: a 200 envelope with `status: error` and `guardrail_blocked`. OpenAI and Anthropic: 502 in their error shape (D.4) |
| `tool_output` | `tool_output: true` | The chassis tool proxy (`/mcp` on both proxy listeners), after the tool answered | The tool result's text content | An MCP tool error with the code `guardrail_blocked` and the fixed text. The run goes on |
| `output` | `output: true` | The pipeline, after a complete answer | The answer's text | The answer is dropped; the run ends with `error {code: guardrail_blocked, retryable: false}`. Native and MCP: a 200 envelope with `status: error`. OpenAI and Anthropic: 502 (D.4) |

- A stream is not checked at `output` (`packages/chassis/tests/test_guard_stages.py::test_output_stage_masks_pii_when_the_call_is_a_stream` (strict xfail: the known gap, review R7-04)). LiteLLM's after-call guardrail is its check. Waits on: a run of the pinned LiteLLM with guardrails on the Mac, to show whether it acts on a streamed answer.
- As built: `input` acts on the verdict only; `tool_output` and `output` pass `Verdict.text` on, with PII masked (`packages/chassis/tests/test_guard_stages.py::test_output_pii_is_masked_in_deltas_and_end_text`, `::test_tool_output_pii_is_masked_in_structured_results`, `::test_tool_output_injection_is_an_mcp_error_the_workload_never_reads`, `::test_tool_output_injection_past_512_tokens_is_blocked`).
- A block is counted as `chassis.guardrail_blocked{stage, reason}` (suggested) and logged with the ids and the reasons. Never the text.
- `on_unavailable: fail_closed` (default): `guardrail_unavailable`, retryable, 503 at `input` and `output`, a retryable MCP tool error at `tool_output`. `fail_open`: the text passes, `chassis.guardrail_unavailable{stage}` counts (suggested), and one warning is logged (`packages/chassis/tests/test_guard_stages.py::test_guardrail_outage_is_retryable_unavailable_when_fail_closed`, `::test_guardrail_outage_passes_the_text_when_fail_open`).

### D.3 What LiteLLM does on its own

Not a chassis contract, recorded so the layers are clear (ADR-008 item 4): Presidio through the `presidio` hook and Llama Prompt Guard 2 through the generic guardrail API (`POST /beta/litellm_basic_guardrail_api`, answered by `packages/guardrail-server`) run on every model call, for every key. The chassis model proxy builds its upstream request from `ModelPort`, so a workload cannot send a `guardrails` field to LiteLLM. The matching LiteLLM config is `packages/guardrail-server/litellm/config.yaml`. It is not run against a real LiteLLM in PoC-7 (the CI stack has no LiteLLM). No test yet checks that a `guardrails` key in a workload's body never reaches the adapter; owner 022 H-6.

### D.4 New error codes

| Code | Where | HTTP status | `retryable` | Public text (suggested) |
| ---- | ----- | ----------- | ----------- | ----------------------- |
| `unauthenticated` | Every protected route | 401, `WWW-Authenticate: Bearer error="invalid_token"` | false | `a valid bearer token is required` |
| `forbidden` | Every protected route, a good token without the route's scope | 403, `WWW-Authenticate: Bearer error="insufficient_scope", scope="<scope>"` | false | `the token does not grant the scope this route needs` |
| `auth_unavailable` | Every protected route | 503, `Retry-After: 1` | true | `the chassis cannot check credentials now; retry later` |
| `guardrail_blocked` | `input`, `tool_output`, `output` | Native and MCP: 200 envelope, `status: error`. OpenAI and Anthropic: 502 (the default rule) | false | `a guardrail blocked this content` |
| `guardrail_unavailable` | Same | 503 | true | `the guardrails cannot check content now; retry later` |
| `evaluator_gate_failed` | The gate, sync, no `fallback_route` | Native: 200 envelope, `status: error`. OpenAI and Anthropic: 502 (the default rule) | false | `the answer did not pass the quality check` |
| `unknown_request` | `/v1/feedback` | 404 | false | `no finished run with this request_id; it may have expired` |
| `feedback_id_conflict` | `/v1/feedback` | 409 | false | `this feedback_id was already used for another request` |
| `feedback_not_configured` | `/v1/feedback` | 501 | false | `feedback is not configured for this agent` |
| `feedback_rejected` | `/v1/feedback` | 502 | false | `the feedback store refused the feedback` |
| `feedback_unavailable` | `/v1/feedback` | 503 | true | `the feedback store cannot be reached; retry later` |

These are new codes, so they are compatible (contract v5, C.3). The run codes (`guardrail_*`, `evaluator_gate_failed`) get a `PUBLIC_MESSAGES` entry. The auth codes and the feedback codes use their own message tables (`chassis.server.security.MESSAGES`, `chassis.server.feedback.MESSAGES`). `status_for` does not change: it maps an unknown code to 503 when retryable and 502 otherwise, which is why `guardrail_blocked` is 502 on the OpenAI and Anthropic interfaces. No frozen code changes its meaning or `retryable` (`packages/chassis/tests/test_contract_freeze.py` passes).

---

## E. AI-generated marking

### E.1 The headers

| Header | Value | When |
| ------ | ----- | ---- |
| `X-AI-Generated` | `true` | `spec.governance` turns marking on: `risk_class` `limited` or `high`, or `ai_generated_marking: true` |
| `X-AI-Disclosure-Required` | `true` | Same condition (suggested). OPEN (user): should a `minimal` agent with `ai_generated_marking: true` also require disclosure? Legal decides |

- On every answer of a run on every public interface: native, OpenAI, Anthropic, and `/v1/mcp`. Complete and streaming: on a stream they are in the response head, before the first byte (`packages/chassis/tests/test_marking.py::test_limited_marks_every_interface_streaming_and_complete`, `::test_inprocess_and_sidecar_give_the_same_flags`; on two engines: `pocs/poc-07-cross-cutting-decisions/tests/test_poc07_sec_marking.py::test_marking_and_redaction_hold_on_every_engine`).
- With marking off, neither header is sent (suggested; `::test_minimal_marks_only_when_asked`).
- As built, a refusal on a run route (a 422, a 413) is marked too: it leaves through the same middleware (`::test_a_refused_call_on_a_run_route_is_marked`). `/health`, `/ready`, and `/manifest` are not marked (`::test_health_ready_and_manifest_are_not_marked`). `/v1/feedback` is not a run route, so it is not marked.
- The chassis sets them on the way out. The workload sends no HTTP headers to the caller, so it cannot remove them, in any lane.

### E.2 Config

`spec.governance.risk_class` (`minimal` | `limited` | `high`, default `minimal`) and `ai_generated_marking` (`true` | `false` | unset). A `limited` or `high` agent with `ai_generated_marking: false` is refused at load.

### E.3 Not in v6

Deferred to 023 C-5 (PoC-8): a body field in the OpenAI and Anthropic formats (their own surfaces, not frozen); the flag on result events (suggested: a CloudEvent extension attribute `aigenerated`, so `task-result.v1.json` does not change); `Access-Control-Expose-Headers` for browser clients. A field on `Response` would be a schema bump to `"1"`.

---

## F. `baggage` over A2A, and what each engine exposes

### F.1 The header

- The chassis drops a caller's inbound `baggage` header. It never forwards a caller's baggage.
- It builds its own (W3C Baggage) from its own span attributes: `user.id` = the caller's `Principal.subject` as it is, none with auth off (OPEN (user): a keyed hash instead? ADR-008 item 3), and `session.id` = the value of the request header named by `spec.observability.session_header` (default `x-session-id`), when present. Values are percent-encoded. suggested: at most 256 bytes per value; a longer session id is dropped and counted.
- The A2A connectors send `baggage` on every A2A request of the run (the message and the cancel), next to `traceparent`, in every lane. No baggage when both values are absent (`packages/chassis/tests/test_otel_wiring.py::test_the_connector_sends_baggage_next_to_the_traceparent`, `packages/chassis/tests/test_otel_telemetry.py::test_no_baggage_outside_a_run`). OPEN (user): should an `untrusted` remote get no `user.id` (suggested)? Today it gets it, as every lane does.
- The chassis puts `user.id` and `session.id` on its own spans as attributes.
- It is not a credential, as `traceparent` is not (contract v1). It is not in `ctx`, and the A2A metadata keys do not change.

### F.2 What the workload does with it

- The template A2A server's telemetry setup (suggested: `workload_a2a.telemetry`) reads `traceparent` and `baggage` from the A2A request, sets them as the current context for `handle`, and copies the baggage entries onto every span the workload starts. One copy for every Python engine. The module is `workload_a2a.telemetry` (extra `workload-a2a[otel]`), with `register_instrumentor` and `inject_trace_headers`. The TypeScript twin is not built; it is a gap for PoC-8 (014 H-7).
- Model and MCP calls from `handle` carry `traceparent` (already required) and `baggage` (new, through the OpenTelemetry HTTP instrumentation).

### F.3 Per engine

What each engine must expose for one trace: an instrumentor or native OpenTelemetry in its process, an OTLP exporter to the Collector, and no vendor trace export. Nothing in its event mapping. From ADR-006 criterion 10, with the PoC-7 results to fill:

| Engine | Lane | Framework spans from | Notes | PoC-7 result |
| ------ | ---- | -------------------- | ----- | ------------ |
| Plain Python | `sidecar` | No framework instrumentor. HTTP client spans and manual spans | | Not run. Add `inject_trace_headers` to its `httpx` client and the `workload-a2a[otel]` extra |
| PydanticAI | `sidecar` | OpenInference instrumentor, or its native OpenTelemetry | The default engine (ADR-006) | Not run. Same two-line wiring |
| LangGraph | `sidecar` | OpenInference under the LangChain name | | **Run**, one trace (`openinference-instrumentation-langchain` 0.1.80). The model proxy span hangs off `workload.handle`, next to the LLM span: same trace, correct ancestry |
| OpenAI Agents SDK | `sidecar` | OpenInference instrumentor | The SDK's own trace export stays off | **Run**, one trace (`openinference-instrumentation-openai-agents` 2.6.0, `exclusive_processor=True`) |
| TypeScript agent | `sidecar` | HTTP client spans; its own A2A server reads `baggage` | | Not run. Needs a TypeScript twin of `workload_a2a.telemetry` |
| smolagents | `remote` | OpenInference instrumentor (reuse analysis) | OPEN (user): whether a remote's spans are accepted (ADR-007 item 9) | Not run |
| Claude Agent SDK | `remote` | OpenInference coverage per the reuse analysis; unverified | Same. The SDK's CLI subprocess may not carry our `traceparent` | Not run |
| kagent-adk | `remote`, plain A2A | Its own OpenTelemetry exporters (set to `none` in PoC-6) | Same. In plain mode the chassis sends only `traceparent` and `baggage`, no `ctx` | Not run (kind only) |

The two PoC-7 engines in one trace: `pocs/poc-07-cross-cutting-decisions/tests/test_poc07_obs_trace_join.py::test_one_trace_joins_chassis_workload_framework_model_and_tool[echo-langgraph]`, `[echo-openai-agents]`. Nothing had to live in an engine's mapping, for any concern: [the per-engine note](../../pocs/poc-07-cross-cutting-decisions/notes/2026-10-28-per-engine-mapping.md).

---

## G. The evaluator gate on existing fields

### G.1 Where it sits

`GatedEngine` wraps the `EngineConnector` in the lifespan when `spec.harness.evaluator_gate` is `true`. It refuses to start with `spec.adapters.evaluator: none`. It runs in every lane, around the connector, so the lane contract and the A2A mapping do not change.

### G.2 Sync mode (complete calls)

| Step | What happens | Field used |
| ---- | ------------ | ---------- |
| Sample | Gated when the hash of `request_id` falls under `evaluator_sample_rate` (suggested: the first 8 bytes of its sha256, as a number below 1). Not gated: no evaluator call, the run is as before | none |
| Attempt 1 | The run, events buffered | The workload's `metrics.attempt` is set to `1` by the chassis |
| Score | `EvaluatorPort.score(EvalCase(input, output))`. An evaluator error counts as a fail | `Score` |
| Retry | Below `threshold`: run again with the same `ctx`, up to `retries` times | `metrics.attempt` set to 2, 3, ... |
| Fallback | Still below: one more run with `ctx.model_route = spec.model.fallback_route`, enforced by the model proxy. Not scored | `Context.model_route`, `metrics.model_route` |
| End | One `end`: `ok` (an attempt on the agent's route passed, the first or a retry; `metrics.attempts` says which), or `fallback` | `end.status` |
| Judge cost | One `metrics` event per score, with the judge's tokens, `model_route` = the judge's route, `attempt` = the attempt scored | `metrics` |

What the client sees: one `start`, the chosen attempt's `delta` and `tool_call` events, every `metrics` event (so `usage` is the sum of every attempt and every score), one `end`. A failed attempt's events, its `tool_call` events too, never reach the client; they stay on the trace. An evaluator outage fails open: the attempt goes out as it is (`chassis.gate_failed_open`). An attempt that ends in `error` is sent as it is, not scored. Evidence: `packages/chassis/tests/test_evaluator_gate.py::test_a_passing_answer_goes_out_once_with_the_judge_metrics`, `::test_a_retry_that_passes_is_ok_not_fallback`, `::test_a_failing_answer_retries_once_then_falls_back`, `::test_an_evaluator_outage_fails_open`, `::test_an_engine_error_is_sent_as_is_and_not_scored`.

**`usage` now includes the judge** in sync mode. A consumer that wants the agent's own spend subtracts the `metrics` whose `model_route` is the judge's route. `Response.versions.model_route` stays the agent's configured route. Whether it should name the fallback route when the fallback answered is deferred to 017 H-4.

With no `fallback_route`, the run ends with one `error {code: evaluator_gate_failed}` and no answer (`packages/chassis/tests/test_evaluator_gate.py::test_no_fallback_route_ends_in_evaluator_gate_failed`).

### G.3 Async mode, and every stream

The answer leaves unchanged. After `end`, the gate scores in the background: a `chassis.evaluator.score` span on the run's trace with `evaluator.score` and `evaluator.passed`, and the counter `chassis.evaluator_calls{evaluator, mode, outcome}` (suggested names). No Langfuse score is written for it in PoC-7; that is 017 H-4 work. The judge's tokens are on the span, not in the answer. No retry, no fallback (`packages/chassis/tests/test_evaluator_gate.py::test_async_mode_never_changes_the_answer_and_records_the_score`, `::test_a_stream_is_always_async`).

### G.4 What a workload must do

Read `ctx["model_route"]` for every model call (every supported engine does), and be safe to run twice for one request (write tools through the idempotency key). It keeps writing `attempt: 1`; the chassis numbers attempts. The model proxy rewrites the route for the fallback attempt: the gate sets the run's `RunRecord.model_route`, and the proxy sends every model call of that run there, whatever `model` the workload names (`chassis.model_route_forced`; `packages/chassis/tests/test_evaluator_gate.py::test_the_proxy_sends_every_call_of_the_fallback_attempt_to_the_fallback_route`). On two engines, in the `sidecar` lane: `pocs/poc-07-cross-cutting-decisions/tests/test_poc07_evals_gate.py::test_poc07_evals_gate_retries_once_then_falls_back[echo-python]`, `[echo-langgraph]`.

### G.5 Numbers

In ADR-010's table (item 9). Measured: the judge prompt is 254 tokens in and 22 out; at suggested list prices that is $0.0000513 per call on a small hosted judge and $0.000855 on a large one; on fakes the gate's own overhead is 0.33 ms mean at sync rate 1.0. Waits on: the Mac run for real judge and encoder latency and agreement.

---

## Config reference

New fields. suggested: every name and default. `chassis-config.v0.json` keeps its major: every new field is optional or has a default. "Reloads" follows `RELOADABLE` in `chassis.server.config`: a path not listed there is restart-only.

| Field | Default | Reloads |
| ----- | ------- | ------- |
| `spec.adapters.auth` | `none` (`fake`, `local`); `oidc` (`cloud`) | No |
| `spec.adapters.guardrails` | `none`; `litellm` (`cloud`) | No |
| `spec.adapters.evaluator` | `none` in every profile | No |
| `spec.adapters.feedback` | `none`; `langfuse` (`cloud`) | No |
| `spec.adapters.telemetry` | as before; `otel` (`cloud`) now resolves | No |
| `spec.security.run_scope` | `agent:run` | No |
| `spec.security.feedback_scope` | `agent:feedback` | No |
| `spec.security.read_scope` | `agent:read` | No |
| `spec.guardrails.input` | `true` | No |
| `spec.guardrails.tool_output` | `true` | No |
| `spec.guardrails.output` | `true` | No |
| `spec.guardrails.on_unavailable` | `fail_closed` (`fail_closed` \| `fail_open`) | No |
| `spec.harness.evaluator_gate` | `false` | No |
| `spec.harness.threshold` | `0.7` (0 to 1) | No |
| `spec.harness.retries` | `1` (0 to 5) | No |
| `spec.harness.evaluator_sample_rate` | `1.0` (0 to 1) | **Yes** |
| `spec.harness.evaluator_mode` | `sync` (`sync` \| `async`) | No |
| `spec.model.fallback_route` | `null` (no fallback) | No (only `spec.model.route` reloads) |
| `spec.feedback.enabled` | `true` | No |
| `spec.feedback.lookup_ttl_s` | `604800` (seven days) | No |
| `spec.feedback.events` | `false` | No |
| `spec.feedback.score_name` | `user-feedback` | No |
| `spec.observability.session_header` | `x-session-id` | No |
| `spec.governance.risk_class` | `minimal` (`minimal` \| `limited` \| `high`) | No |
| `spec.governance.ai_generated_marking` | unset; `false` refused for `limited` and `high` | No |

The guard stages are on by default, but with `guardrails: none` (the `fake` and `local` default) they pass everything.

**Against the epic's agent config** (C.1 of the epic, read-only), recorded so PoC-8 can align them. Each is deferred to PoC-8, with the owner in the Note column:

| Epic | Here | Note |
| ---- | ---- | ---- |
| `spec.harness.evaluator: facts_kept` (names an evaluator) | `spec.adapters.evaluator` (names an adapter) | A named evaluator per agent needs an adapter argument or the A2A adapter (ADR-010 G7) |
| `spec.harness.timeout_ms`, `spec.harness.budget.max_tokens` | Not here; `spec.limits` and the envelope's `budget` | 017 H-4 |
| `spec.security.scopes: [simplify:run]`, `spec.security.allowed_agents` | `run_scope`, `feedback_scope`, `read_scope`; no `allowed_agents` | 022 H-6 |
| `spec.observability.log_level`, `trace_sample_rate` | `session_header` only | Sampling is the Collector's (ADR-007 item 8) |
| `spec.governance` with `intended_purpose`, `owner`, `human_oversight`, and more (E.4) | `risk_class`, `ai_generated_marking` only | 077 C-1 adds the rest |
| `spec.modules: [evaluator_gate]` | `spec.harness.evaluator_gate: true` | |

Environment variables (suggested names, as built): `OTEL_EXPORTER_OTLP_ENDPOINT` or `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` and the other standard `OTEL_*`; `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL`; `CHASSIS_STATIC_TOKENS` (the fake); `LITELLM_BASE_URL`, `LITELLM_API_KEY`, `LITELLM_GUARDRAIL_INPUT`, `LITELLM_GUARDRAIL_TOOL_OUTPUT`, `LITELLM_GUARDRAIL_OUTPUT`, `LITELLM_GUARDRAIL_CHUNKED`; `CHASSIS_JUDGE_ROUTE`, `CHASSIS_JUDGE_PRICE_IN_PER_MTOK`, `CHASSIS_JUDGE_PRICE_OUT_PER_MTOK`; `CHASSIS_ENCODER_URL`, `CHASSIS_ENCODER_LABEL`; `LANGFUSE_BASE_URL` (or `LANGFUSE_HOST`), `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`. All in the chassis container only (hard requirement 1).

## Cost per lane

- **`inprocess`.** Fakes for every new port. The `baggage` header rides the ASGI transport. A gate retry is a second in-memory run.
- **`sidecar`.** Per request: one JWT check (local, JWKS cached); one LiteLLM call per enabled stage, plus one per tool call for `tool_output`; one `StatePort` write for the feedback lookup; one OTLP export batch from each container. About 100 bytes of `baggage` per A2A request (suggested estimate). A sync gate adds one evaluator call per gated request and a full second run per failed attempt. Waits on: p50 and p95 added by each, on the Mac (the CI stack has no LiteLLM); not measured in PoC-7.
- **`remote`.** The same, with the A2A hop over the network and in gVisor, so a gate retry costs the most here (ADR-010 suggests async for `remote`). OPEN (user): whether a remote's spans are accepted, and whether it gets `user.id`.

## Contract suites

| Suite | New or changed | Bound to |
| ----- | -------------- | -------- |
| `AuthPortContract` | New | `StaticTokens` (`packages/chassis/tests/test_contracts_poc07.py::TestStaticTokens`), `OidcAuth` (`packages/chassis/tests/test_oidc_adapter.py::TestOidcAuth`) |
| `GuardrailPortContract` | New | `KeywordGuardrails` (`test_contracts_poc07.py::TestKeywordGuardrails`), `LiteLLMGuardrails` (`packages/chassis/tests/test_litellm_guardrails.py::TestLiteLLMGuardrails`) |
| `EvaluatorPortContract` | New | `ScriptedEvaluator` (`test_contracts_poc07.py::TestScriptedEvaluator`), `JudgeEvaluator` and `EncoderEvaluator` (`packages/chassis/tests/test_evaluators.py::TestJudgeEvaluator`, `::TestEncoderEvaluator`) |
| `FeedbackPortContract` | New; gained `test_a_timestamp_is_kept` | `InMemoryFeedback` (`test_contracts_poc07.py::TestInMemoryFeedback`), `LangfuseFeedback` (`packages/chassis/tests/test_langfuse_feedback.py::TestLangfuseFeedback`, against `fake-langfuse`; the real Langfuse waits on `poc07-stack.yml`) |
| `TelemetryPortContract` | Gained cases for a `traceparent` attribute | `InMemoryTelemetry`, `OtelTelemetry` (`packages/chassis/tests/test_otel_telemetry.py::TestOtelTelemetry`) |
| `LaneContract` | Unchanged. No `baggage` case was added; the connector test is `packages/chassis/tests/test_otel_wiring.py::test_the_connector_sends_baggage_next_to_the_traceparent`, and the two-engine join is in the `sidecar` lane | |
| Freeze test | Unchanged, must stay green | |

## Open points

- **For the user** (`OPEN (user):` above): the form of `user.id`, and what an `untrusted` remote gets and may send (F, ADR-007, ADR-008); binding the feedback lookup to the caller's subject, and redacting the stored comment (B.4, B.6, ADR-009); disclosure for a `minimal` agent with marking on (E.1).
- **Deferred to PoC-8, with an owner:** the marking body field and the result-event flag (023 C-5); `/metrics` (014 H-7); the epic config names (022 H-6, 017 H-4); a Langfuse score for async gate results (017 H-4); a test that a workload's `guardrails` key never reaches LiteLLM (022 H-6).
- **The environment.** Hugging Face is blocked here (no Prompt Guard 2, no encoder model). There is no hosted model key (no real judge numbers). Docker images run only in CI (`poc07-stack.yml`) or on the Mac. Every number that needs them says `Waits on:`.

## Links

- Plan and planning doc: [the PoC-7 work plan](../plans/2026-10-28-poc-07-cross-cutting.md), [PoC-7](../planning/poc/007-PoC-7-cross-cutting-decisions.md).
- Decisions: [ADR-007](../planning/adr/007-observability-otel-collector-langfuse.md), [ADR-008](../planning/adr/008-security-identity-guardrails-defense-in-depth.md), [ADR-009](../planning/adr/009-feedback-langfuse-scores-event.md), [ADR-010](../planning/adr/010-evals-offline-tool-online-gate.md). Built on [ADR-001](../planning/adr/001-chassis-delivery-model.md) and [ADR-004](../planning/adr/004-events-through-a-broker-client.md).
- The contract before this one: [contract v5](contract-v5.md). Its part C (the freeze) holds unchanged.
- Backlog changes: `pocs/poc-07-cross-cutting-decisions/notes/backlog-changes.md`.
