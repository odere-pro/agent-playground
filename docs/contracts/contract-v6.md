# Contract v6

The written contract after PoC-7: four new ports (`AuthPort`, `GuardrailPort`, `EvaluatorPort`, `FeedbackPort`) and a real `TelemetryPort` adapter; caller auth and scopes on the public port; three guard stages; a new public route, `POST /v1/feedback`, and its CloudEvent `agents.feedback.received.v1`; AI-generated marking as response headers; W3C `baggage` over A2A; and the evaluator gate, built on fields that already exist. Every change is additive over [contract v5](contract-v5.md), which stays as the PoC-6 record and still holds for everything this document does not name. Contracts v4 to v1 hold for what v5 does not name.

Status: **draft, 2026-10-28.** Written from the code on branch `poc-07/cross-cutting` at commit `9f2f240` (the ports, fakes, suites, `spec` sections, and the Compose stack), from the [PoC-7 work plan](../plans/2026-10-28-poc-07-cross-cutting.md), and from the designs of the work running in parallel (W1 to W4). Where this text and the code differ, the code wins. Items another work stream may still change are marked `EVIDENCE:` (a test name or a number goes there) or `OPEN:` (a choice not made yet). The planning doc is [PoC-7](../planning/poc/007-PoC-7-cross-cutting-decisions.md). The decisions are [ADR-007](../planning/adr/007-observability-otel-collector-langfuse.md) (observability), [ADR-008](../planning/adr/008-security-identity-guardrails-defense-in-depth.md) (security), [ADR-009](../planning/adr/009-feedback-langfuse-scores-event.md) (feedback), and [ADR-010](../planning/adr/010-evals-offline-tool-online-gate.md) (evals).

Contract v6 is a version of this document, not of the wire. The event schema stays `schema_version: "0"`.

## Decisions at a glance

| | Decision | Contract it touches | `inprocess` | `sidecar` | `remote` |
| - | -------- | ------------------- | ----------- | --------- | -------- |
| A | Four new ports with fakes, no-op defaults, and suites; `OtelTelemetry` for `TelemetryPort` | Ports, `spec.adapters`. Not the envelope, events, `handle`, or the A2A mapping | Fakes; no network | Real adapters in the chassis container | Same as `sidecar`: every port lives in the chassis |
| B | `POST /v1/feedback` | A new public route; `StatePort` gains one record per run; `spec.feedback` | Route on the in-process app | Route on the chassis's public port | Same; the remote is not involved |
| C | `agents.feedback.received.v1` with payload `feedback.v0.json` | A new event type on `EventPort` (a new topic, not a change to a chassis event) | Fake bus | Broker | Broker |
| D | Caller auth and scopes; guard stages `input`, `tool_output`, `output`; new error codes | The public routes' refusals; the tool proxy's tool errors; `spec.security`, `spec.guardrails`; `PUBLIC_MESSAGES` entries | Fake auth and guardrails | One LiteLLM call per enabled stage | Same; `tool_output` also covers the remote's tool calls on the remote listener |
| E | `X-AI-Generated` and `X-AI-Disclosure-Required` response headers | The public interfaces' response headers; `spec.governance`. Not `Response` | Headers set | Headers set | Headers set |
| F | W3C `baggage` on every A2A request of a run, next to `traceparent` | The A2A transport's HTTP headers. Not the A2A mapping's metadata keys | Header on the ASGI transport | Header on localhost | Header over the network. OPEN: no `user.id` to an `untrusted` remote |
| G | The evaluator gate (`GatedEngine`) | `spec.harness`, `spec.model.fallback_route`; uses `metrics.attempt`, `metrics.model_route`, `end.status: fallback`, `Context.model_route` as they are | A retry is a second in-memory run | A second localhost run | A second network run in gVisor |

## What did not change

- **The frozen surface of contract v5, part C.2.** `handle`, `TaskInput`, `Context`, the six events, the envelope (`Request`, `Response`), the A2A mapping in chassis mode, and the frozen error codes with their `retryable`. `schema_version` stays `"0"`. `SUPPORTED_SCHEMA_VERSIONS` stays `("0",)`.
- **The five schema files.** `events.v0.json`, `task_input.v0.json`, `context.v0.json`, `request.v0.json`, `response.v0.json` are byte for byte as at the end of PoC-6. EVIDENCE: `packages/chassis/tests/test_contract_freeze.py` green at the PoC-7 merge, and `git diff --stat main -- packages/chassis/schemas/{events,task_input,context,request,response}.v0.json` printing nothing.
- **No user, session, or marking field in a frozen surface.** User and session ids are baggage and span attributes (F). The marking is a header (E). The evaluator's score is a span attribute and a Langfuse score, never a `metrics` field (G).
- **`chassis.core`**, except entries added to `PUBLIC_MESSAGES` in `core/inbound.py` for the new codes. That is the one allowed exception (contract v5, C.3). OPEN: whether `status_for` also needs entries for `guardrail_blocked` and `evaluator_gate_failed` (D.4). If it does, the diff adds entries only. EVIDENCE: `git diff --stat main -- packages/chassis/src/chassis/core` at the PoC-7 merge.
- **The model proxy's wire formats, the remote listener's routes, `RequireRun`, `BearerAuth`, idempotency, `/ready`, shutdown.** As in v5. `tool_output` adds a check on the tool proxy, not a route.
- **`task-result.v1.json` and the result events.** Unchanged. OPEN: the `ai_generated` flag on result events (E.3).

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
| `oidc` | `chassis.adapters.oidc:OidcAuth.from_env` | Default in `cloud`. A JWT checked against the IdP's JWKS. EVIDENCE: the env names (issuer, audience, JWKS URL), the JWKS cache rule, the clock leeway, and the suite binding |

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
- `split_for_classifier(text, pieces=384, overlap=32)` is the chunking rule for a classifier that reads 512 tokens. suggested: the two numbers. It counts word and punctuation pieces, which a subword tokenizer splits further, so the budget keeps headroom. `guardrail-server` uses the same rule. EVIDENCE: that it does, by test name.
- `NoGuardrails` (`none`) passes every text unchanged.

| `spec.adapters.guardrails` | Class | Notes |
| -------------------------- | ----- | ----- |
| `none` | `NoGuardrails` | Default in `fake` and `local` |
| `fake` | `KeywordGuardrails` | Patterns stand in for Prompt Guard 2, regex redaction for Presidio |
| `litellm` | `chassis.adapters.litellm.guardrails:LiteLLMGuardrails.from_env` | Default in `cloud`. LiteLLM's `POST /guardrails/apply_guardrail`. EVIDENCE: the env names and the guardrail names it sends |

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
| `judge` | `chassis.adapters.evaluators:JudgeEvaluator.from_env` | An LLM judge through `ModelPort`, on its own route. EVIDENCE: the route env name, the prompt, and how a reply becomes a score |
| `encoder` | `chassis.adapters.evaluators:EncoderEvaluator.from_env` | An HTTP classifier, served outside the chassis. EVIDENCE: the request and reply shape |

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
| `langfuse` | `chassis.adapters.langfuse:LangfuseFeedback.from_env` | Default in `cloud`. Langfuse `POST /api/public/scores`, score id = `feedback_id`, trace id = the W3C trace id. EVIDENCE: the env names, and where `label` goes |

Suite: `FeedbackPortContract` (submitted feedback is stored; optional fields may be empty; the same id twice is one record; an outage is retryable).

### A.5 `TelemetryPort`: the `otel` adapter

The port does not change. `spec.adapters.telemetry: otel` is `chassis.adapters.otel:OtelTelemetry.from_env`: the OpenTelemetry SDK, OTLP over HTTP, configured by the standard `OTEL_EXPORTER_OTLP_*` and `OTEL_SERVICE_NAME` variables. The run span's trace id is the request's W3C trace id, and its span id is the parent id of the `traceparent` sent to the workload. EVIDENCE: the suite binding, and the test that a workload span hangs under the run span.

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

On the public port only. Mounted when `spec.feedback.enabled` (default `true`). Not mounted: a FastAPI 404. Needs `spec.security.feedback_scope` (D.1). Not on the proxy listeners, so no workload can call it.

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
| 202 | | Written, or the same `feedback_id` was written before. Body (suggested): `{"feedback_id": "...", "request_id": "..."}` | |
| 400 | `invalid_body` | Not JSON, not an object, an unknown field, a field of the wrong type or out of range | No |
| 401 | `unauthenticated` | No token, or a bad token. `WWW-Authenticate: Bearer` | No |
| 403 | `insufficient_scope` | A good token without `feedback_scope` | No |
| 404 | `feedback_unknown_request` (suggested) | No lookup record: an unknown `request_id`, an expired one, or one made by another caller | No |
| 501 | `feedback_not_configured` | `spec.adapters.feedback: none` | No |
| 503 | `auth_unavailable`, `state_unavailable`, `feedback_unavailable` | The IdP's keys, the state store, or the feedback store did not answer | Yes |

Errors use the native refusal body, `{"detail": {"code", "message"}}` (contract v2), with the fixed text of `PUBLIC_MESSAGES`. OPEN: a 4xx from Langfuse (`feedback_rejected`) maps to 502 by the default rule; confirm it with W3. EVIDENCE: one test per row.

### B.4 The lookup

One record per run in `StatePort` (suggested key: `<agent>:feedback:<request_id>`), holding the W3C trace id (`trace_id_hex(ctx.trace_id)`) and the caller's subject (suggested: a keyed hash). It lives `spec.feedback.lookup_ttl_s` (default 604800, seven days). The route reads it and refuses with 404 when the subject differs, so a caller cannot learn that another caller's request exists. OPEN: written at run start (suggested) or at run end. EVIDENCE: the record's size in bytes.

### B.5 Idempotency

By `feedback_id`. Langfuse upserts a score by id; the fake keeps one entry per id. A body without `feedback_id` gets one derived from `(agent, request_id, subject, spec.feedback.score_name)` (suggested: a UUIDv5), so one caller has one feedback per request and the latest wins. This is not the `Idempotency-Key` machinery: the route keeps no answer to replay.

### B.6 Redaction

`comment` and `label` pass through `chassis.redaction.redact` before the port and before the event. Names are not caught by the chassis (ADR-008 item 6).

---

## C. The event `agents.feedback.received.v1`

Published on `EventPort` when `spec.feedback.events` is `true` (default `false`), after the port's write succeeded, in the background, as the result events are (contract v3). A slow or broken broker never changes the route's answer. Not published for a refused call or a failed write.

- **The CloudEvent** (suggested, after contract v3's result events): `type` `agents.feedback.received.v1`; `id` the hex of `uuid5(EVENT_ID_NAMESPACE, "agents.feedback.received.v1:<feedback_id>")`, so a second delivery dedupes; `source` `/agents/<agent>`; `subject` the `request_id`; `time` UTC now; `dataschema` `feedback.v0.json`; `traceparent` with the run's trace id; `partitionkey` the sha256 hex of the `request_id`, so feedback on one request keeps its order.
- **The payload** (`feedback.v0.json`, `extra="forbid"`; suggested fields): `feedback_id`, `agent`, `request_id`, `trace_id`, `score`, `label`, `comment` (redacted), `subject_hash`, `received_at`. The payload schema has its own major, as `task-result.v1.json` has. EVIDENCE: the schema file from `make schemas`, and its test.
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

The check runs before limits, idempotency, and the run. The token is read from `Authorization: Bearer`, then removed: it never reaches the workload, a log, or a span. The in-process hop from `/v1/mcp` to `/v1/run` carries the checked principal; it is never trusted by loopback address or by `x-chassis-interface`. OPEN: `/metrics` (014 H-7). OPEN: the epic's `spec.security.scopes` list and `allowed_agents` against the three named scopes here (E.4's config table).

The proxy listeners are unchanged: loopback has no auth, the remote listener has `BearerAuth` and `RequireRun` (contract v4). Caller tokens are never accepted there.

### D.2 Guard stages

| Stage | `spec.guardrails.<stage>` | Where | Text checked | On a block |
| ----- | ------------------------- | ----- | ------------ | ---------- |
| `input` | `input: true` | The pipeline, after auth and limits, before the engine | `input.text`, and the text of `input.data` (OPEN: which keys) | No run. Native: 400 `{"detail": {"code": "guardrail_blocked", ...}}`. OpenAI and Anthropic: 400 in their error shape |
| `tool_output` | `tool_output: true` | The chassis tool proxy (`/mcp` on both proxy listeners), after the tool answered | The tool result's text content | An MCP tool error with the code `guardrail_blocked` and the fixed text. The run goes on |
| `output` | `output: true` | The pipeline, after a complete answer | The answer's text | The answer is dropped; the run ends with `error {code: guardrail_blocked, retryable: false}`. Native and MCP: a 200 envelope with `status: error`. OpenAI and Anthropic: the status from D.4 |

- A stream is not checked at `output`. LiteLLM's after-call guardrail is its check. EVIDENCE: whether it acts on a streamed answer in the pinned LiteLLM.
- suggested: `input` acts on the verdict only; `tool_output` and `output` pass `Verdict.text` on, with PII masked. OPEN: confirm against W2's code.
- Spans: one `chassis.guardrail` span per check, with `stage`, `allowed`, and `reasons` (suggested). Never the text.
- `on_unavailable: fail_closed` (default): `guardrail_unavailable`, retryable, 503 at `input` and `output`, a retryable MCP tool error at `tool_output`. `fail_open`: the text passes, `chassis.guardrails.unavailable{stage}` counts (suggested), and one warning is logged.

### D.3 What LiteLLM does on its own

Not a chassis contract, recorded so the layers are clear (ADR-008 item 4): Presidio through the `presidio` hook and Llama Prompt Guard 2 through the generic guardrail API (`POST /beta/litellm_basic_guardrail_api`, answered by `packages/guardrail-server`) run on every model call, for every key. The chassis model proxy builds its upstream request from `ModelPort`, so a workload cannot send a `guardrails` field to LiteLLM. EVIDENCE: the LiteLLM config in the PoC-7 stack, and a proxy test that a `guardrails` key in a workload's body never reaches the adapter.

### D.4 New error codes

| Code | Where | HTTP status | `retryable` | Public text (suggested) |
| ---- | ----- | ----------- | ----------- | ----------------------- |
| `unauthenticated` | Every protected route | 401, `WWW-Authenticate: Bearer` | false | `missing or invalid bearer token` |
| `insufficient_scope` | Every protected route | 403, `WWW-Authenticate: Bearer error="insufficient_scope", scope="<scope>"` | false | `the token does not grant this route` |
| `auth_unavailable` | Every protected route | 503 | true | `the chassis cannot check the token; retry later` |
| `guardrail_blocked` | `input`, `tool_output`, `output` | 400 (suggested) | false | `the content was blocked by a guardrail` |
| `guardrail_unavailable` | Same | 503 | true | `the guardrail service did not answer; retry later` |
| `evaluator_gate_failed` | The gate, sync, no `fallback_route` (OPEN: if used) | 502 (the default rule) | false | `no answer passed the quality check` |
| `feedback_unknown_request` | `/v1/feedback` | 404 | false | `no such request for this caller` |
| `feedback_not_configured` | `/v1/feedback` | 501 | false | `feedback is not configured for this agent` |
| `feedback_unavailable` | `/v1/feedback` | 503 | true | `the feedback store did not answer; retry later` |

These are new codes, so they are compatible (contract v5, C.3). Each gets a `PUBLIC_MESSAGES` entry. `status_for` maps an unknown code to 503 when retryable and 502 otherwise. So `guardrail_blocked` at 400 needs a `status_for` entry or a refusal built with its own status in `chassis.server`. OPEN: which one W2 built. No frozen code changes its meaning or `retryable`. EVIDENCE: `test_contract_freeze.py` green.

---

## E. AI-generated marking

### E.1 The headers

| Header | Value | When |
| ------ | ----- | ---- |
| `X-AI-Generated` | `true` | `spec.governance` turns marking on: `risk_class` `limited` or `high`, or `ai_generated_marking: true` |
| `X-AI-Disclosure-Required` | `true` | Same condition (suggested). OPEN: whether a `minimal` agent with `ai_generated_marking: true` also requires disclosure; legal decides |

- On every answer of a run on every public interface: native, OpenAI, Anthropic, and `/v1/mcp`. Complete and streaming: on a stream they are in the response head, before the first byte. EVIDENCE: the test per interface and mode.
- With marking off, neither header is sent (suggested).
- Not on refusals before a run (401, 403, 400, 413, 429, 503), on `/v1/feedback`, `/health`, `/ready`, or `/manifest` (suggested).
- The chassis sets them on the way out. The workload sends no HTTP headers to the caller, so it cannot remove them, in any lane.

### E.2 Config

`spec.governance.risk_class` (`minimal` | `limited` | `high`, default `minimal`) and `ai_generated_marking` (`true` | `false` | unset). A `limited` or `high` agent with `ai_generated_marking: false` is refused at load.

### E.3 Not in v6

OPEN for 023 C-5 (PoC-8): a body field in the OpenAI and Anthropic formats (their own surfaces, not frozen); the flag on result events (suggested: a CloudEvent extension attribute `aigenerated`, so `task-result.v1.json` does not change); `Access-Control-Expose-Headers` for browser clients. A field on `Response` would be a schema bump to `"1"`.

---

## F. `baggage` over A2A, and what each engine exposes

### F.1 The header

- The chassis drops a caller's inbound `baggage` header. It never forwards a caller's baggage.
- It builds its own (W3C Baggage): `user.id` = the caller's `Principal.subject` (OPEN: the subject or a keyed hash, ADR-008 item 3), and `session.id` = the value of the request header named by `spec.observability.session_header` (default `x-session-id`), when present. Values are percent-encoded. suggested: at most 256 bytes per value; a longer session id is dropped and counted.
- The A2A connectors send `baggage` on every A2A request of the run (the message and the cancel), next to `traceparent`, in every lane. No baggage when both values are absent. OPEN: an `untrusted` remote gets no `user.id` (suggested).
- The chassis puts `user.id` and `session.id` on its own spans as attributes.
- It is not a credential, as `traceparent` is not (contract v1). It is not in `ctx`, and the A2A metadata keys do not change.

### F.2 What the workload does with it

- The template A2A server's telemetry setup (suggested: `workload_a2a.telemetry`) reads `traceparent` and `baggage` from the A2A request, sets them as the current context for `handle`, and copies the baggage entries onto every span the workload starts. One copy for every Python engine. The TypeScript agent does the same in its own server. EVIDENCE: the module, and the TypeScript twin.
- Model and MCP calls from `handle` carry `traceparent` (already required) and `baggage` (new, through the OpenTelemetry HTTP instrumentation).

### F.3 Per engine

What each engine must expose for one trace: an instrumentor or native OpenTelemetry in its process, an OTLP exporter to the Collector, and no vendor trace export. Nothing in its event mapping. From ADR-006 criterion 10, with the PoC-7 results to fill:

| Engine | Lane | Framework spans from | Notes | PoC-7 result |
| ------ | ---- | -------------------- | ----- | ------------ |
| Plain Python | `sidecar` | No framework instrumentor. HTTP client spans and manual spans | | EVIDENCE |
| PydanticAI | `sidecar` | OpenInference instrumentor, or its native OpenTelemetry | The default engine (ADR-006) | EVIDENCE |
| LangGraph | `sidecar` | OpenInference under the LangChain name | | EVIDENCE |
| OpenAI Agents SDK | `sidecar` | OpenInference instrumentor | The SDK's own trace export stays off | EVIDENCE |
| TypeScript agent | `sidecar` | HTTP client spans; its own A2A server reads `baggage` | | EVIDENCE |
| smolagents | `remote` | OpenInference instrumentor (reuse analysis) | OPEN: whether a remote's spans are accepted (ADR-007 item 9) | EVIDENCE |
| Claude Agent SDK | `remote` | OpenInference coverage per the reuse analysis; unverified | Same | EVIDENCE |
| kagent-adk | `remote`, plain A2A | Its own OpenTelemetry exporters (set to `none` in PoC-6) | Same. In plain mode the chassis sends only `traceparent` and `baggage`, no `ctx` | EVIDENCE |

EVIDENCE: the two PoC-7 engines in one trace, with the test names, and the list of anything that had to live in an engine's mapping (the PoC-7 exit criterion; expected: nothing).

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
| Fallback | Still below: one more run with `ctx.model_route = spec.model.fallback_route`. Not gated (suggested) | `Context.model_route`, `metrics.model_route` |
| End | One `end`: `ok` (attempt 1 passed), `retry` (a retry passed), or `fallback` | `end.status` |
| Judge cost | One `metrics` event per score, with the judge's tokens, `model_route` = the judge's route, `attempt` = the attempt scored | `metrics` |

What the client sees: one `start`, the chosen attempt's `delta` and `tool_call` events, every `metrics` event (so `usage` is the sum of every attempt and every score), one `end`. OPEN: whether a discarded attempt's `tool_call` events reach the client (suggested: no; they stay on the trace).

**`usage` now includes the judge** in sync mode. A consumer that wants the agent's own spend subtracts the `metrics` whose `model_route` is the judge's route. `Response.versions.model_route` stays the agent's configured route. OPEN: whether it should name the fallback route when the fallback answered.

With no `fallback_route`, OPEN: `evaluator_gate_failed` (suggested) or the last answer with `end.status: retry`.

### G.3 Async mode, and every stream

The answer leaves unchanged. After `end`, the gate scores in the background: a `chassis.evaluator` span on the run's trace with `score`, `evaluator`, and `passed` (suggested), and a Langfuse score named after the evaluator when the feedback adapter is `langfuse` (suggested; OPEN for W4). The judge's tokens go to `chassis.evaluator.tokens{route}` (suggested), not to the answer. No retry, no fallback.

### G.4 What a workload must do

Read `ctx["model_route"]` for every model call (every supported engine does), and be safe to run twice for one request (write tools through the idempotency key). It keeps writing `attempt: 1`; the chassis numbers attempts. OPEN: the model proxy refusing or rewriting a call whose `model` is not the run's route, so an engine that ignores `ctx.model_route` cannot break the fallback. EVIDENCE: the gate's tests on two engines in `inprocess` and `sidecar`.

### G.5 Numbers

EVIDENCE: tokens, cost, p50 and p95 latency per gated call, and agreement, for `judge` and `encoder`, in ADR-010's table. TBD by the trial.

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

**Against the epic's agent config** (C.1 of the epic, read-only), recorded so PoC-8 can align them. OPEN for each:

| Epic | Here | Note |
| ---- | ---- | ---- |
| `spec.harness.evaluator: facts_kept` (names an evaluator) | `spec.adapters.evaluator` (names an adapter) | A named evaluator per agent needs an adapter argument or the A2A adapter (ADR-010 G7) |
| `spec.harness.timeout_ms`, `spec.harness.budget.max_tokens` | Not here; `spec.limits` and the envelope's `budget` | 017 H-4 |
| `spec.security.scopes: [simplify:run]`, `spec.security.allowed_agents` | `run_scope`, `feedback_scope`, `read_scope`; no `allowed_agents` | 022 H-6 |
| `spec.observability.log_level`, `trace_sample_rate` | `session_header` only | Sampling is the Collector's (ADR-007 item 8) |
| `spec.governance` with `intended_purpose`, `owner`, `human_oversight`, and more (E.4) | `risk_class`, `ai_generated_marking` only | 077 C-1 adds the rest |
| `spec.modules: [evaluator_gate]` | `spec.harness.evaluator_gate: true` | |

Environment variables (EVIDENCE: the final names from W1 to W4): the OTLP endpoint and headers (standard `OTEL_*`); the IdP's issuer, audience, and JWKS URL; LiteLLM's guardrail URL and the guardrail names; the judge route; the encoder URL; Langfuse's host and keys. All in the chassis container only (hard requirement 1).

## Cost per lane

- **`inprocess`.** Fakes for every new port. The `baggage` header rides the ASGI transport. A gate retry is a second in-memory run.
- **`sidecar`.** Per request: one JWT check (local, JWKS cached); one LiteLLM call per enabled stage, plus one per tool call for `tool_output`; one `StatePort` write for the feedback lookup; one OTLP export batch from each container. About 100 bytes of `baggage` per A2A request (suggested estimate). A sync gate adds one evaluator call per gated request and a full second run per failed attempt. EVIDENCE: p50 and p95 added by each, on the CI stack.
- **`remote`.** The same, with the A2A hop over the network and in gVisor, so a gate retry costs the most here (ADR-010 suggests async for `remote`). OPEN: whether a remote's spans are accepted, and whether it gets `user.id`.

## Contract suites

| Suite | New or changed | Bound to |
| ----- | -------------- | -------- |
| `AuthPortContract` | New | `StaticTokens`, `OidcAuth` (EVIDENCE: test path) |
| `GuardrailPortContract` | New | `KeywordGuardrails`, `LiteLLMGuardrails` (EVIDENCE) |
| `EvaluatorPortContract` | New | `ScriptedEvaluator`, `JudgeEvaluator`, `EncoderEvaluator` (EVIDENCE) |
| `FeedbackPortContract` | New | `InMemoryFeedback`, `LangfuseFeedback` (EVIDENCE) |
| `TelemetryPortContract` | Unchanged | `InMemoryTelemetry`, `OtelTelemetry` (EVIDENCE) |
| `LaneContract` | Unchanged. EVIDENCE: a case that `baggage` and `traceparent` reach the workload in every lane | |
| Freeze test | Unchanged, must stay green | |

## Open points

- Every `OPEN:` above. The main ones: the form of `user.id` and what an `untrusted` remote gets (ADR-008); the status of `guardrail_blocked` and where it is set (D.4); the gate with no fallback route, the discarded attempts' tool calls, and the proxy enforcing the run's route (G); where `label` goes in Langfuse and when the lookup record is written (B); the marking body field and the result-event flag (E.3); the epic config names (Config reference).
- **The environment.** Hugging Face is blocked here (no Prompt Guard 2, no encoder model). There is no hosted model key (no real judge numbers). Docker images run only in CI (`poc07-stack.yml`) or on the Mac. Every number in this document that needs them is an `EVIDENCE:` slot.

## Links

- Plan and planning doc: [the PoC-7 work plan](../plans/2026-10-28-poc-07-cross-cutting.md), [PoC-7](../planning/poc/007-PoC-7-cross-cutting-decisions.md).
- Decisions: [ADR-007](../planning/adr/007-observability-otel-collector-langfuse.md), [ADR-008](../planning/adr/008-security-identity-guardrails-defense-in-depth.md), [ADR-009](../planning/adr/009-feedback-langfuse-scores-event.md), [ADR-010](../planning/adr/010-evals-offline-tool-online-gate.md). Built on [ADR-001](../planning/adr/001-chassis-delivery-model.md) and [ADR-004](../planning/adr/004-events-through-a-broker-client.md).
- The contract before this one: [contract v5](contract-v5.md). Its part C (the freeze) holds unchanged.
- Backlog changes: `pocs/poc-07-cross-cutting-decisions/notes/backlog-changes.md`.
