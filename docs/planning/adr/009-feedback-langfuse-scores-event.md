# ADR-009: Feedback: `POST /v1/feedback` onto the call's trace in Langfuse, and the event `agents.feedback.received.v1`

- **Date:** 2026-10-28
- **Deciders:** Oleksandr (epic owner) and the delivery team
- **Format:** Michael Nygard's template: Status, Context, Decision, Consequences
- **Related:** [ADR-004](004-events-through-a-broker-client.md) (events through `EventPort`), [ADR-007](007-observability-otel-collector-langfuse.md) (the trace id in Langfuse), [ADR-008](008-security-identity-guardrails-defense-in-depth.md) (scopes, redaction), [ADR-010](010-evals-offline-tool-online-gate.md) (implicit signals), [PoC-7](../poc/007-PoC-7-cross-cutting-decisions.md), [reuse analysis](../poc/010-reuse-analysis.md), [contract v6](../../contracts/contract-v6.md), [014 H-7](../issues/014-H-7-observability.md), [040 D-0](../issues/040-D-0-recorder-agent.md), [064 D-1](../issues/064-D-1-golden-set-agent.md), [089 R-15](../issues/089-R-15-event-schemas-registered.md)

## Status

Accepted, 2026-10-30, by the user. Proposed 2026-10-28. Its open questions stay open; a change to one of them needs a new ADR or an edit here.

Evidence that is not in yet:

- **Langfuse runs only in CI or on the Mac.** Offline, `LangfuseFeedback` passes its suite against a stub of the scores API.
- Shown in CI: a feedback call shows up as a score on the call's trace in a real Langfuse v4, `poc07-stack` run 40735435727 on `dd1d594` (real Langfuse v4, OTel Collector 0.163.2, Jaeger 2.21.0: 7 passed, 1 skipped) (`pocs/poc-07-cross-cutting-decisions/tests/test_poc07_stack.py::test_chassis_feedback_lands_on_the_run_trace`). Offline, both engines land on the run's trace against the fake Langfuse: `pocs/poc-07-cross-cutting-decisions/tests/test_poc07_fb_engines.py::test_poc07_fb_feedback_lands_on_the_runs_trace_in_the_sidecar_lane[echo-python]`, `[echo-langgraph]`.
- The route, one test per answer: `packages/chassis/tests/test_feedback_route.py::test_feedback_after_a_run_lands_on_its_trace` (202), `::test_a_bad_body_is_422`, `::test_an_unknown_request_id_is_404`, `::test_a_feedback_id_used_for_another_request_is_409`, `::test_no_feedback_port_is_501`, `::test_a_store_outage_is_503_retryable`, `::test_a_state_outage_on_lookup_is_503`; `packages/chassis/tests/test_security_auth.py::test_feedback_route_needs_the_feedback_scope` (401, 403). The event: `::test_the_event_is_published_redacted_and_valid`, `::test_a_failed_publish_never_changes_the_202`, `::test_events_off_publishes_nothing` ([feedback note](../../../pocs/poc-07-cross-cutting-decisions/notes/2026-10-28-feedback.md)).

## Context

The epic wants feedback from users and evaluators collected per run, for the golden set and for post-market monitoring ([E.5](../slm-agent-platform-epic-v3.md#e5), AI Act Art. 72). The reuse analysis picks the Langfuse scores API behind a chassis route. PoC-7 suggests a new event, `agents.feedback.received.v1`, which is not in the epic's event catalog (D.3). The epic is never edited, so the catalog addition is recorded in `pocs/poc-07-cross-cutting-decisions/notes/backlog-changes.md`.

The forces:

- **Feedback comes late.** Minutes or days after the run. The run's spans have ended, so feedback cannot be a span attribute.
- **The caller knows the `request_id`.** It may not know the trace id: the OpenAI and Anthropic interfaces can re-mint it (ADR-003).
- **One caller must not score another's call.** A trace id sent by the caller would let anyone write onto any trace.
- **Stateless.** Any replica must answer, so the link from `request_id` to the trace lives in `StatePort`.
- **Retries are normal.** A client that times out sends the same feedback again. It must count once.
- **A comment is free text.** It can hold personal data.

Options considered:

| # | Option | Verdict and why |
| - | ------ | --------------- |
| F1 | **`POST /v1/feedback` in the chassis; the trace found from `request_id` in `StatePort`; written through `FeedbackPort` to the Langfuse scores API; an optional CloudEvent** | **Chosen** |
| F2 | A span event or attribute on the run span | Rejected. The span has ended when feedback comes |
| F3 | Our own feedback table in Postgres now | Rejected now. The recorder (040 D-0) and the golden set agent (064 D-1) own records. They can consume the event later |
| F4 | The event only, no Langfuse | Rejected alone. The review flow in Langfuse needs the score on the trace |
| F5 | The caller sends the trace id | Rejected. It cannot be checked, and the non-native interfaces can re-mint it |
| F6 | The Langfuse SDK in the adapter | Rejected (suggested). One HTTP endpoint is enough; plain `httpx` keeps the image smaller and the adapter swappable |

## Decision

**We choose F1.**

1. **The route.** `POST /v1/feedback` on the public port, mounted when `spec.feedback.enabled` (default `true`) and `spec.adapters.feedback` is not `none` (`none` is the `fake` and `local` default, so their OpenAPI does not change), and protected by `spec.security.feedback_scope` (ADR-008). The body (suggested names): `request_id` (required), `score` (a number in [0, 1]; a thumbs down is 0, a thumbs up is 1), `label` (optional, at most 64 characters), `comment` (optional, at most 2000 characters), `feedback_id` (optional). Unknown fields are refused with 422. The answer is 202 `{feedback_id, trace_id}`. The full table of status codes is in contract v6, part B.
2. **Finding the trace.** The chassis writes one record per run to `StatePort`, at run end: after the engine stream ended, through an `on_finished` callback, awaited before a complete answer goes out. The key is `chassis:feedback:v1:req:<agent>:<sha256(request_id)>` and the value is `{trace_id, agent}`. It lives `spec.feedback.lookup_ttl_s` (suggested: 7 days). The route reads it. A missing, unfinished, or expired record, or another agent's, is 404 `unknown_request`. A replica keeps nothing (`pocs/poc-07-cross-cutting-decisions/tests/test_poc07_fb_route.py::test_poc07_fb_a_second_replica_sharing_only_state_takes_the_feedback`; `packages/chassis/tests/test_feedback_route.py::test_the_lookup_is_in_state_with_the_spec_ttl`). As built, the record holds no subject, so any caller with the `feedback_scope` who knows a `request_id` can score that call. OPEN (user): bind the record to the caller's subject, so another caller gets 404? OPEN (user): a reviewer role that may score any request of the agent?
3. **Writing the score.** `FeedbackPort.submit` takes a `Feedback` with the trace id from the record, never from the body. The real adapter is `LangfuseFeedback` (`spec.adapters.feedback: langfuse`): Langfuse's `POST /api/public/scores`, with the score id set to `feedback_id`, the trace id set to the W3C trace id (the id Langfuse got from the OTLP spans, ADR-007), the name set to `spec.feedback.score_name` (suggested: `user-feedback`), and the comment. The label goes in the score's `metadata` (`{request_id, label, subject}`), not in a second score: two scores would break "one record per `feedback_id`" and could leave a 207 half-written. Langfuse's score body is a JSON list (the `langfuse` SDK 5.0.4 client), sent with HTTP Basic auth (`packages/chassis/tests/test_langfuse_feedback.py::test_the_request_is_one_list_with_basic_auth`, `::test_the_score_is_a_numeric_score_on_the_trace`). The fake is `memory`.
4. **Idempotent by `feedback_id`.** The same id twice is one record: Langfuse upserts a score by id, and the fake keeps one entry per id. When the body has no `feedback_id`, the chassis derives one: 32 hex of sha256 of the `request_id` and the caller's subject, so one caller has one feedback per request and the latest wins. The first time a `feedback_id` is seen, its time goes to `StatePort`, and a repeat sends that time again, because Langfuse updates a score only for the same id and timestamp. A `feedback_id` already used for another request is 409 `feedback_id_conflict` (`packages/chassis/tests/test_feedback_route.py::test_a_repeat_is_one_record_with_the_first_timestamp`; `pocs/poc-07-cross-cutting-decisions/tests/test_poc07_fb_route.py::test_poc07_fb_a_repeat_is_one_score_on_the_trace`). This is not the `Idempotency-Key` machinery of 018 H-18: there is no answer to replay.
5. **Redaction.** As built, the comment goes to Langfuse as written: the user gave it on purpose, to be read with the trace. The event carries it through the chassis's regex redaction (`chassis.redaction`). Logs and spans never hold it. Names in a comment are not caught by the regex. OPEN (user): redact the stored comment as well, or keep it as written?
6. **The event.** With `spec.feedback.events: true` (default `false`), the chassis publishes `agents.feedback.received.v1` on `EventPort` after a successful write, in the background, as it does for result events (contract v3). The payload is `feedback.v0.json` (`packages/chassis/schemas/feedback.v0.json`): `feedback_id`, `request_id`, `trace_id`, `agent`, `score`, `label`, `comment` (redacted), `subject`, `received_at`. The CloudEvent fields are in contract v6, part C. The epic's catalog gains one row (producer: any agent's chassis; consumers: the recorder, the golden set agent, the audit log), recorded in the PoC-7 backlog changes.
7. **Implicit signals do not use this route.** Fallbacks, retries, and evaluator scores come from the evaluator gate. Where they go is [ADR-010 item 5](010-evals-offline-tool-online-gate.md).
8. **Where each hook lives:**

   | Hook | Lives in |
   | ---- | -------- |
   | The route, the scope check, the lookup, the redaction, the event | The chassis |
   | The `request_id` to trace record | `StatePort` (Valkey in `local` and `cloud`) |
   | The score and its view on the trace | Langfuse |
   | The event | The broker, through `EventPort` (ADR-004) |
   | Langfuse's credential | The chassis container only (hard requirement 1) |

9. **What each engine must expose: nothing.** The `request_id`, the trace id, and the subject are the chassis's.

## Consequences

Easier:

- A client gives feedback with the id it already has, on any interface.
- A score lands on the exact trace that produced the answer, next to the engine's own steps.
- The golden set agent and the recorder can consume feedback as an event, with no Langfuse dependency.

Harder:

- One `StatePort` write per run, kept for seven days. Waits on: the bytes per record and the Valkey memory it adds at the PoC-4 load rate. Not measured in PoC-7 (it needs the Docker load stack).
- Feedback depends on Langfuse. When Langfuse is down the route answers 503, and the client retries. The id makes the retry safe.
- A Langfuse project key can read every trace of its project. The chassis holds one. suggested: one Langfuse project per environment, and a scores-only key if a later Langfuse version offers one.
- When an app calls the agent with its own token for many end users, the subject is the app. OPEN (user): how does an app pass its end user's id? A header, as `session.id` is passed, would be unchecked.

Costs, per lane: none. Feedback is a chassis route. The lookup record is written in every lane, and no A2A call is made.

## Revisit

Reopen this decision on any of these:

- **064 D-1 does not pick Langfuse** as the golden set workspace. Then feedback is written where the golden set lives, and the event becomes the main path.
- **Feedback volume** makes one Langfuse call per feedback too slow (suggested: p95 over 500 ms on the route), or the lookup records grow past 5% of Valkey's memory (suggested).
- **Langfuse changes the scores API** or its upsert by id.
- **A reviewer flow** needs to score requests of other callers.

## Open questions for the user

1. Should the lookup record hold the caller's subject, so only the caller who made the request can score it (item 2)? Today any caller with `feedback_scope` who knows a `request_id` can.
2. Is a reviewer role that may score any request of the agent needed (item 2)?
3. Should the comment stored in Langfuse be regex-redacted too, or kept as written (item 5)?
4. How does an app that calls with its own token pass its end user's id (Consequences)?
