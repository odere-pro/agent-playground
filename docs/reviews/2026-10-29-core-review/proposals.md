# Proposals

Findings whose fix would change behavior: the wire contract, the event schema, or the meaning of an exit criterion. Fix sessions do not apply them. The user decides each one. An accepted proposal gets an ADR or a contract version.

Format: the finding ID, one sentence on the change, one sentence on why, and the decision.

Grouped by theme by R8 on 2026-10-29. The 38 findings with `Status: proposal` are here, in 37 entries: R1-08 shares an entry with R3-02, and R7-16 with R2-10. The last group holds 6 more that the fix sessions raised. Decide the entries in one group together.

## Error text and redaction

| ID | Change | Why | Decision |
| -- | ------ | --- | -------- |
| R3-02, R1-08 (and the rest of R2-08) | Native `/v1/run` and MCP send `public_message(code)` in the error frame and `output.error.message`, and keep the raw text in the log and span. | The run's own exception text reaches the caller today, which breaks the PoC-3 rule; contract v2 and v5 pin that text, so the contract changes. | open |
| R1-04 | The phone pattern needs a leading `+` or a separator, and cards get a Luhn check. | Dates, IP addresses, and order ids become `<PHONE_NUMBER>` today, while some real PII slips through. | open |
| R1-07 | Add a `SECRET` entity to `redact` for Bearer tokens, key shapes, and PEM headers. | `Bearer sk-abc123` passes through redaction unchanged. Decide with R7-14. | open |
| R7-14 | The guardrail secret scan adds vendor prefixes, matches key names in any case and with quotes, and normalizes text first. | Common shapes such as `ghp_`, `xoxb-`, JWTs, and `password=` are not caught. The chassis fake must follow (R7-19). | open |
| R2-27 | The TypeScript workload checks each SSE chunk and answers `bad_response`, not retryable. | A malformed chunk now throws a `TypeError` that is reported as a retryable `connect_error`. | open |
| R3-14 | One `materialize(value, path)` with one location format for both chat adapters. | Two copies turn a `ValidationError` into text, in two formats; one `invalid_body` text changes. | open |

## Caller input bounds

| ID | Change | Why | Decision |
| -- | ------ | --- | -------- |
| R1-09 (with the cap part of R4-27) | Add length and charset limits on `request_id`, `trace_id`, and `idempotency_key`, and `max_length` or `ge=0` on event fields, then `make schemas`. | Unbounded ids reach logs, spans, and the store, and a lone surrogate crashes the store key. Ask `chassis-architect` first. | open |
| R2-31 | Refuse a remote token shorter than 32 characters at startup. | Any non-blank token, even one character, is accepted today. | open |
| R5a-11 | Add `max_length` on `code` and an ASGI body cap that answers 413 in the code runner. | The 64 KiB check runs only after the whole body is read and parsed. | open |
| R5a-24 | Cap a tool result (suggested: 1 MiB) and map an overrun to `tool_unavailable`. | The gateway passes any tool output to the workload whole. | open |
| R7-13 | The guardrail route scans `tool_calls` and `structured_messages`, and refuses an empty `checks` with 422. | Today it scans only `texts`, and `checks: []` runs nothing. | open |

## Who may act for a run

| ID | Change | Why | Decision |
| -- | ------ | --- | -------- |
| R2-02 | The chassis mints a per-run secret, sends it with the A2A call, and the remote listener requires it with the run's `traceparent`. | One shared bearer lets a remote charge or write under any in-flight run. Adds a header on the proxy wire; ask `chassis-architect`. | open |
| R3-06 | Hash the principal's subject into the idempotency store key when auth is on. | Two principals with the same key share one cached result. Old entries are not found after the change. | open |
| R2-10, R7-16 | Store the caller's subject in the feedback lookup and answer `unknown_request` on a mismatch; count labels from a fixed set. | Any caller with the feedback scope can score any run, and a free-text label goes into a metric. F2 pinned R2-10 as a strict xfail; ADR-009 and contract v6 hold the open question. | open |
| R7-17 | Salt the evaluator sampling hash, and add a `fail_closed` option. | A client picks its `request_id`, so it can steer sampling, and an evaluator error fails open. | open |
| R4-29 | Store the hash of the idempotency key in the `done` entry, not the raw key. | The module says the store holds only the hash, but it holds the raw key with the full input and output for 24 h. F4 fixes the docstring now. | open |

## Startup refusals for an unsafe config

| ID | Change | Why | Decision |
| -- | ------ | --- | -------- |
| R4-30 | On the `cloud` profile, refuse `CONFIG_S3_SECURE=false` and a non-TLS Valkey URL unless a named override is set. | Plaintext config and state traffic is allowed in the cloud profile today. | open |
| R5a-12 | Refuse a non-`https` `jwks_url` unless the host is loopback. | Any URL is accepted, so signing keys can be fetched in cleartext. | open |
| R5a-22 | Refuse `--allow-any-proxy-host` in profile `cloud` and when `spec.trust` is `untrusted`. | The flag puts the model proxy and `/mcp` on a non-loopback address with no auth. | open |
| R5a-25 | Refuse a non-`https` `LITELLM_MCP_URL` unless the host is loopback or in-cluster, and require `https` in `cloud`. | An `http://` URL outside the cluster sends the LiteLLM key in cleartext. | open |

## Cluster admission and network (PoC-5 kind)

| ID | Change | Why | Decision |
| -- | ------ | --- | -------- |
| R5a-01 | Add admission rule 7e: a sidecar-lane pod sets `automountServiceAccountToken: false`, and only the chassis container mounts a projected token. | A pipeline manifest can give a sidecar workload a token, which undoes hard requirement 1 with no refusal. | open |
| R5a-02 | Drop `networkpolicies` from `agent-deployer`, and apply each policy as cluster admin. | The deployer identity can delete `default-deny`. A test pins the current grant on purpose. | open |
| R5a-05 | Refuse `shareProcessNamespace: true` in sidecar pods, and require a workload `runAsUser` that differs from the chassis's. | H26 (no read of the chassis's `/proc/<pid>/environ`) depends on both, and admission checks neither. | open |
| R5a-06 | Add `!variables.projectedToken` to rule 7d. | A tool pod may mount a projected token, which rule 5 refuses for remote pods. | open |
| R5a-07 | Allow port 8080 only from a named gateway namespace, or require auth on the public port. | Any pod in an unpoliced namespace can call `/v1/run`, which takes no credential. | open |
| R5a-16 | Add a rule and a kind test for ephemeral containers, including `targetContainerName: chassis`. | The policy header names ephemeral containers, but nothing tests them, and the CEL ignores the target. | open |
| R5a-23 | The code-runner dispatcher accepts a bearer from a file, and LiteLLM sends it. | The dispatcher serves `/mcp` on `0.0.0.0` with no auth while it can create `SandboxClaim`s. | open |

## Guardrails, telemetry, and streaming (PoC-7)

| ID | Change | Why | Decision |
| -- | ------ | --- | -------- |
| R7-04 | Check streamed `delta` frames with a buffered or tail window, or refuse `stream` while the output guard is on. | A streamed answer skips the chassis output check for secrets and PII. Also a note for the PoC-7 plan: criterion 5 does not state the gap. | open |
| R7-15 | Add a bearer authenticator on the Collector's OTLP receiver, with a token only the chassis gets. | Anyone who can reach the receiver can send spans that leave with the Langfuse credential. | open |

## Engines and the bake-off (PoC-6)

| ID | Change | Why | Decision |
| -- | ------ | --- | -------- |
| R6-01 | Keep one task source in `bakeoff.tasks`, and import it in the test harness. | The tests and the kit ask different prompts; one side's prompt changes, so check that the scorecards still hold. | open |
| R6-11 | One Sandbox per run, or one request at a time with a pod recycle after each run. | Runs in one remote pod share a uid, `/tmp`, and a process tree, and smolagents runs model code in the server process. | open |
| R6-18 | 6c criterion 1 asserts the run counts, and criterion 2 skips only while the hosted results file is absent. | The README's "135 of 135 runs passed" is not gated, and criterion 2 always skips. Also for the PoC-6 plan. | open |

## Harness and CI

| ID | Change | Why | Decision |
| -- | ------ | --- | -------- |
| R0-01 | Narrow the `.claude/settings.json` allow rules to the commands the harness uses, and add denies for `uv pip`, `uv run curl`, and `.env` reads. | `uv *`, `make *`, and `cat *` get around the deny list. Some agent workflows stop working. | open |
| R0-08 | Pass `SNYK_TOKEN` only on `main` or `workflow_dispatch`, or behind an environment with a reviewer. | Branch code runs with the token on every push and same-repo PR. | open |
| R0-14 | Name the env gates by what they enable (`KIND_DRILLS`, `COMPOSE_STACK`), with one `kind` and one `stack` marker. | Six gates carry PoC numbers. CI, notes, and evidence commands change. | open |
| R1-28 | Drop `poc=` from `lazy(...)`, say "adapter X is unavailable (no module Y)", and rename `_POC07_OFF`. | The error says "arrives in PoC-N" for adapters that exist, so it misleads. | open |

## Shared checkers

| ID | Change | Why | Decision |
| -- | ------ | --- | -------- |
| R5b-08 | Keep one JSON Schema argument checker in a neutral module, used by the fake and the gateway. | The two checkers differ on `anyOf`, bool versus int, nested properties, and `enum`; the nested checks start to run in the adapter. | open |

## Raised by the fix sessions

These findings are `open` in their files. The fix session found that a full fix changes behavior.

| ID | Change | Why | Decision |
| -- | ------ | --- | -------- |
| R2-11 | Cap a lane stream by event count and bytes. | Every event is kept in memory with no cap; the cap ends the run with an error code that contract v1 does not list (F2). | open |
| R3-30 | Stop a direct caller from setting `x-chassis-interface` on `/v1/run`. | The header turns off the disconnect watch and relabels telemetry; contract v2 and v3 define it on the wire (F3). | open |
| R4-10 | Cap the waiters on one in-flight key, and answer 409 above the cap. | Duplicates poll the store with no limit; the 409 is a new refusal. F4 adds backoff and the disconnect check now. | open |
| R6-07 | 6a criterion 3 calls the scorecard check, not a skip. | The skip reason says "not done" while the README says done; calling the check changes what the criterion test asserts. Also for the PoC-6 plan. | open |
| R6-19 | The 6c static tests check `--dry-run` output instead of matching script text. | A text match passes when the behavior changes; F6 renames the tests now. | open |
| F8-01 (from R4-14) | A second signal ends `Drain.serve` at once, even with a stream open. | With a stream open, uvicorn's `wait_closed` holds the exit until `timeout_s`, so `force_exit` does not force it. The old test passed only because it closed its stream; `test_a_second_signal_forces_exit` now ends in `xfail` (F8). | open |

## Seen but not filed

From review summaries and notes, with no finding ID. File them if you want a decision.

- R2 summary: `profiles.py:323` accepts `spec.adapters.auth: none` in the `cloud` profile, so `/v1/run` runs as the anonymous principal. A refusal would sit with R4-30.
- R6 dropped note: `model_proxy.py:555` lets an uncorrelated call pick any route its key lists.
- R7 dropped notes: no guardrail sets `default_on: true`, and the guardrail server has no auth.
