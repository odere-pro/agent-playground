# ADR-009: Feedback: `POST /v1/feedback` onto the call's trace in Langfuse, and the event `agents.feedback.received.v1`

- **Date:** 2026-10-28
- **Deciders:** Oleksandr (epic owner) and the delivery team
- **Format:** Michael Nygard's template: Status, Context, Decision, Consequences
- **Related:** [ADR-004](004-events-through-a-broker-client.md) (events through `EventPort`), [ADR-007](007-observability-otel-collector-langfuse.md) (the trace id in Langfuse), [ADR-008](008-security-identity-guardrails-defense-in-depth.md) (scopes, redaction), [ADR-010](010-evals-offline-tool-online-gate.md) (implicit signals), [PoC-7](../poc/007-PoC-7-cross-cutting-decisions.md), [reuse analysis](../poc/010-reuse-analysis.md), [contract v6](../../contracts/contract-v6.md), [014 H-7](../issues/014-H-7-observability.md), [040 D-0](../issues/040-D-0-recorder-agent.md), [064 D-1](../issues/064-D-1-golden-set-agent.md), [089 R-15](../issues/089-R-15-event-schemas-registered.md)

## Status

Proposed, 2026-10-28. The user accepts it at the PoC-7 pull request.

Evidence that is not in yet:

- **Langfuse runs only in CI or on the Mac.** Offline, `LangfuseFeedback` passes its suite against a stub of the scores API.
- EVIDENCE: the `poc07-stack.yml` run where a feedback call shows up as a score on the call's trace in Langfuse, for two engines.
- EVIDENCE: the test names of the route (every status code) and of the event.

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

1. **The route.** `POST /v1/feedback` on the public port, mounted when `spec.feedback.enabled` (default `true`) and protected by `spec.security.feedback_scope` (ADR-008). The body (suggested names): `request_id` (required), `score` (a number in [0, 1]; a thumbs down is 0, a thumbs up is 1), `label` (optional, at most 64 characters), `comment` (optional, at most 2000 characters), `feedback_id` (optional). Unknown fields are refused. The answer is 202. The full table of status codes is in contract v6, part B.
2. **Finding the trace.** The chassis writes one record per run to `StatePort`: the `request_id` (keyed by agent), the W3C trace id, and the caller's subject (suggested: a keyed hash). It lives `spec.feedback.lookup_ttl_s` (suggested: 7 days). The route reads it. A missing or expired record, or one whose subject is not the caller's, is 404, so a caller cannot learn that another caller's request exists. OPEN: the record is written at run start or at run end (suggested: start, so feedback on a failed run works too). OPEN: a reviewer role that may score any request of the agent.
3. **Writing the score.** `FeedbackPort.submit` takes a `Feedback` with the trace id from the record, never from the body. The real adapter is `LangfuseFeedback` (`spec.adapters.feedback: langfuse`): Langfuse's `POST /api/public/scores`, with the score id set to `feedback_id`, the trace id set to the W3C trace id (the id Langfuse got from the OTLP spans, ADR-007), the name set to `spec.feedback.score_name` (suggested: `user-feedback`), and the comment. OPEN: where `label` goes (suggested: score metadata). The fake is `memory`.
4. **Idempotent by `feedback_id`.** The same id twice is one record: Langfuse upserts a score by id, and the fake keeps one entry per id. When the body has no `feedback_id`, the chassis derives one from the agent, the `request_id`, the caller's subject, and the score name (suggested: a UUIDv5), so one caller has one feedback per request and the latest wins. This is not the `Idempotency-Key` machinery of 018 H-18: there is no answer to replay.
5. **Redaction.** The chassis runs its regex redaction (`chassis.redaction`) on `comment` and `label` before the port and before the event. Names in a comment are not caught. That is a recorded gap until Langfuse's own masking or the Collector covers it (OPEN).
6. **The event.** With `spec.feedback.events: true` (default `false`), the chassis publishes `agents.feedback.received.v1` on `EventPort` after a successful write, in the background, as it does for result events (contract v3). The payload is `feedback.v0.json`. The CloudEvent fields are in contract v6, part C. The epic's catalog gains one row (producer: any agent's chassis; consumers: the recorder, the golden set agent, the audit log), recorded in the PoC-7 backlog changes.
7. **Implicit signals do not use this route.** Fallbacks, retries, and evaluator scores go onto the trace from the evaluator gate (ADR-010) as scores of their own names.
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

- One `StatePort` write per run, kept for seven days. EVIDENCE: the bytes per record, and the Valkey memory it adds at the PoC-4 load rate.
- Feedback depends on Langfuse. When Langfuse is down the route answers 503, and the client retries. The id makes the retry safe.
- A Langfuse project key can read every trace of its project. The chassis holds one. suggested: one Langfuse project per environment, and a scores-only key if a later Langfuse version offers one.
- When an app calls the agent with its own token for many end users, the subject is the app. OPEN: how an app passes its end user's id (a header, as `session.id` is passed, would be unchecked).

Costs, per lane: none. Feedback is a chassis route. The lookup record is written in every lane, and no A2A call is made.

## Revisit

Reopen this decision on any of these:

- **064 D-1 does not pick Langfuse** as the golden set workspace. Then feedback is written where the golden set lives, and the event becomes the main path.
- **Feedback volume** makes one Langfuse call per feedback too slow (suggested: p95 over 500 ms on the route), or the lookup records grow past 5% of Valkey's memory (suggested).
- **Langfuse changes the scores API** or its upsert by id.
- **A reviewer flow** needs to score requests of other callers.
