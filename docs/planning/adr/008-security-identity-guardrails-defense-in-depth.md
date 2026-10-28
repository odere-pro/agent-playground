# ADR-008: Security: caller identity in the chassis, content limits in LiteLLM, chassis guard stages as defense in depth

- **Date:** 2026-10-28
- **Deciders:** Oleksandr (epic owner) and the delivery team
- **Format:** Michael Nygard's template: Status, Context, Decision, Consequences
- **Related:** [ADR-001](001-chassis-delivery-model.md) (hard requirement 1, items 5, 6, and 8), [ADR-005](005-remote-lane-auth-and-trust-admission.md) (the remote token), [ADR-007](007-observability-otel-collector-langfuse.md) (baggage, log redaction), [PoC-7](../poc/007-PoC-7-cross-cutting-decisions.md), [reuse analysis](../poc/010-reuse-analysis.md), [contract v6](../../contracts/contract-v6.md), [022 H-6](../issues/022-H-6-security-middleware.md), [023 C-5](../issues/023-C-5-ai-generated-marking.md), [026 CH-4](../issues/026-CH-4-chassis-only-credentials-egress.md), [081 R-8](../issues/081-R-8-description-scanning.md), [the backlog plan, gap (d)](../issues/000-plan.md#adr-001-follow-ups)

## Status

Proposed, 2026-10-28. The user accepts it at the PoC-7 pull request.

Evidence that is not in yet:

- **No Prompt Guard 2 here.** Hugging Face answers 403 in the build container, and the model is gated. Offline, `KeywordGuardrails` and a stand-in classifier play its part, and every offline number says so. The real classifier runs on the Mac, where Hugging Face is reachable. The CI stack (`poc07-stack.yml`) has no LiteLLM and no classifier.
- **No LiteLLM proxy with guardrails here.** The Docker registries need auth. Offline tests run `LiteLLMGuardrails` against a stub of LiteLLM's guardrail API.
- The red-team pass rate, offline: 12 of 18 (0.667) with `LiteLLMGuardrails` over the LiteLLM stub and the pattern stand-in, 11 of 18 (0.611) with `KeywordGuardrails`. Every miss has an owner ([red-team note](../../../pocs/poc-07-cross-cutting-decisions/notes/2026-10-28-redteam.md); `pocs/poc-07-cross-cutting-decisions/tests/test_poc07_redteam.py::test_the_run_matches_the_recorded_pass_rates`). Waits on: the Mac run for the same cases on Prompt Guard 2.
- The suites, offline against stubs: `packages/chassis/tests/test_oidc_adapter.py::TestOidcAuth` binds `AuthPortContract`, as the fake does (`packages/chassis/tests/test_contracts_poc07.py::TestStaticTokens`). `packages/chassis/tests/test_litellm_guardrails.py::TestLiteLLMGuardrails` binds `GuardrailPortContract`, as `TestKeywordGuardrails` does. `guardrail-server` is a service behind LiteLLM, not a port adapter, so it has no port suite; it has its own tests (`packages/guardrail-server/tests/test_guardrail_server.py::test_injection_past_the_first_window_is_blocked`, `::test_pattern_chunks_overlap_so_a_boundary_injection_is_whole`).

## Context

ADR-001 decides that the chassis pipeline runs in every lane but is not the security boundary against hostile code. Item 6 puts the hard limits in shared services: LiteLLM keys and guardrails, the MCP gateway's allow-list, network policy, and gVisor for `remote` pods. PoC-5 proved hard requirement 1 on kind. This ADR decides what the chassis adds on top, and where each content check runs.

The forces:

- **The epic's security standard** ([G.3](../slm-agent-platform-epic-v3.md#g3)): OAuth or API keys with read and write scopes; rate and size limits; PII redaction before logging or storage; input text, tool descriptions, and tool outputs treated as data.
- **The workload cannot skip a hard control.** A trusted workload with a bug, or an agent hijacked by prompt injection, still reaches models only through LiteLLM with the chassis's scoped key, and tools only through the MCP gateway.
- **The chassis stays thin.** ADR-001's memory estimate assumes no named-entity model in the chassis. Presidio with a spaCy model adds about 0.5–1 GiB per replica.
- **The AI Act marking** (Art. 50, [E.4](../slm-agent-platform-epic-v3.md#e4)) must hold in every lane, and the workload must not be able to remove it. `Response` is frozen (contract v5, C.2), so a new field there is a schema bump.

Facts that shape the choice:

- LiteLLM runs guardrails on model calls before the call (input, and the tool messages in it) and after it (output). It also offers `POST /guardrails/apply_guardrail`, which runs a configured guardrail on given text.
- Per-key guardrail control in LiteLLM is Enterprise-only (the reuse analysis; gap (d) of the backlog plan). Guardrails switched on for every call are in the open-source proxy.
- LiteLLM's built-in `promptguard` hook calls a different vendor's hosted API. It is not Llama Prompt Guard 2 (checked in the LiteLLM 1.106.5 source). LiteLLM's generic guardrail API (`POST /beta/litellm_basic_guardrail_api`) lets a self-hosted service answer instead.
- Llama Prompt Guard 2 reads 512 tokens at a time. A long tool output must be split, or an injection after the first 512 tokens is not seen.
- LiteLLM has a built-in `presidio` hook.
- Snyk Agent Scan sends component data to Snyk and runs the MCP servers it scans (081 R-8, "Watch").
- The chassis model proxy maps every workload request onto `ModelPort` and builds a new upstream request (contract v5, A.6). Fields the port does not carry never reach LiteLLM.

Options considered:

| # | Concern | Option | Verdict and why |
| - | ------- | ------ | --------------- |
| S1 | Where content checks run | LiteLLM guardrails only | Rejected alone. LiteLLM sees model calls, not the caller's raw input before the engine runs, not a tool output the workload acts on without a model call, and not the final answer of a non-model step |
| S2 | | The chassis only | Rejected. The chassis is not the boundary (ADR-001 item 6), so a check that lives only there is not a hard control. PII checks there would also need a named-entity model in the chassis (S9) |
| S3 | | **LiteLLM guardrails as the hard control, plus chassis stages that call the same guardrails** | **Chosen**. One guardrail config, two places to apply it |
| S4 | Injection classifier | **Llama Prompt Guard 2 behind LiteLLM's generic guardrail API, in our own `guardrail-server`** | **Chosen**. Self-hosted, no data leaves the cluster, 512-token chunks with overlap |
| S5 | | LiteLLM's `promptguard` hook | Rejected. It calls a different vendor's hosted API, so prompts leave the cluster |
| S6 | | LLM Guard | Rejected. No release in over a year (reuse analysis, "Avoid") |
| S7 | | NeMo Guardrails | Rejected (not evaluated in depth). A dialogue-rails framework with its own runtime, and it would sit in the request path as one more framework |
| S8 | PII | **Presidio through LiteLLM's built-in `presidio` hook; regex in the chassis** | **Chosen** |
| S9 | | Presidio in the chassis | Rejected. A named-entity model adds about 0.5–1 GiB per replica and breaks ADR-001's memory estimate |
| S10 | Guardrails per service | LiteLLM Enterprise, guardrails per key | Not now. An owner decision with a licence cost (gap (d)). Revisit below |
| S11 | Caller auth | **OIDC: a JWT checked against the IdP's JWKS, scopes per route from `spec.security`** | **Chosen**. Local check, no IdP call per request |
| S12 | | Token introspection on every call | Rejected. One IdP round trip per request, and the IdP becomes a hard dependency of every call |
| S13 | | Auth only at the ingress | Rejected as the only check. The chassis owns the public port (ADR-001 item 3) and the scope per route. An ingress may still add a first check |
| S14 | AI marking | **Response headers** | **Chosen for PoC-7**. No frozen surface changes |
| S15 | | A field on `Response` | Rejected now. `response.v0.json` is frozen; a new field is a bump to `"1"` |

## Decision

**We choose S3: the hard limits stay in shared services, as ADR-001 item 6 says. The chassis adds caller identity and three guard stages that call the same LiteLLM guardrails, as defense in depth.**

1. **The layers, and which one is the boundary:**

   | Layer | What it stops | Boundary? |
   | ----- | ------------- | --------- |
   | LiteLLM scoped virtual key per service | Models, budget, and rate the service may use | **Yes** (hard requirement 1) |
   | LiteLLM guardrails on every model call | Injection and PII in what goes to and comes from a model, tool results included, since they reach the model as messages | **Yes**. The workload cannot skip them: the proxy builds the upstream request and never forwards a `guardrails` field |
   | MCP gateway allow-list per key | Tools the service may call | **Yes** |
   | Network policy, default-deny egress; gVisor for `remote` | Every other destination; the host kernel | **Yes** |
   | Chassis auth and scopes | Who may call which route | Yes for callers. Not against the workload, which never holds the caller's token |
   | Chassis guard stages | Injection in the caller's text before the engine runs; injection in a tool output before the workload reads it; a secret or PII in the final answer | **No**. Defense in depth |
   | Chassis regex redaction | Emails, phone numbers, and ids in the chassis's own logs and events | No. Hygiene |

2. **Caller auth is `OidcAuth`** behind `AuthPort` (`spec.adapters.auth: oidc`; the fake is `static`, refused in the `cloud` profile). It checks a JWT's signature against the IdP's JWKS, and its issuer, audience, and expiry. Each public route needs one scope from `spec.security` (suggested names):

   | Route | Scope |
   | ----- | ----- |
   | `POST /v1/run`, `POST /v1/chat/completions` and `POST /v1/messages` on the public port, `/v1/mcp` | `run_scope` (`agent:run`) |
   | `POST /v1/feedback` (ADR-009) | `feedback_scope` (`agent:feedback`) |
   | `GET /manifest`, the OpenAPI documents | `read_scope` (`agent:read`) |
   | `GET /health`, `GET /ready` | none |

   No token or a bad token is 401 `unauthenticated` with `WWW-Authenticate: Bearer error="invalid_token"`. A good token without the scope is 403 with the code `forbidden`, and its `WWW-Authenticate` is `Bearer error="insufficient_scope", scope="<scope>"` (RFC 6750). An IdP whose keys cannot be read is 503 `auth_unavailable` with `Retry-After: 1`. The bodies are in each interface's own error shape. Evidence: `packages/chassis/tests/test_security_auth.py::test_run_scope_is_needed_on_every_run_interface`, `::test_feedback_route_needs_the_feedback_scope`, `::test_read_scope_is_needed_for_the_manifest_and_docs`, `::test_health_and_ready_need_no_token`, `::test_an_idp_outage_is_503_and_retryable`, `::test_errors_are_in_each_interface_shape`, `::test_mcp_call_carries_the_callers_token_to_v1_run`. The token is never forwarded to the workload, never logged, and never in a span. The in-process hop from `/v1/mcp` to `/v1/run` carries the checked principal, never trust by loopback address or by `x-chassis-interface` (022 H-6, PoC-3 debt). OPEN (user): API keys for callers (the epic's "or API keys"): a second `AuthPort` adapter, or the IdP's client-credentials flow? As built, a path outside the three scope classes needs a valid token and no scope. The public port serves no `/metrics` in PoC-7; where it goes is deferred to 014 H-7.
3. **The user id is the principal's subject.** It goes to spans and to baggage (ADR-007 item 3). As built, it is the authenticated subject as it is, and there is none with auth off (`packages/chassis/tests/test_security_auth.py::test_the_callers_subject_is_user_id_on_the_run_span`, `::test_auth_off_puts_no_user_id_on_the_run_span`). OPEN (user): a keyed hash instead (suggested, so a `remote` workload and the trace backend never see the IdP's subject)? And no `user.id` in baggage to an `untrusted` remote (suggested)?
4. **Content guardrails run in LiteLLM.** Presidio through the built-in `presidio` hook. Llama Prompt Guard 2 through LiteLLM's generic guardrail API, answered by `packages/guardrail-server` (`POST /beta/litellm_basic_guardrail_api`), which splits text into chunks that fit 512 tokens and blocks when any chunk is flagged. suggested: 384 word pieces per chunk with 32 of overlap (`split_for_classifier`), so an injection on a boundary is seen whole. Both run on every model call, for every key, in the open-source proxy (suggested: switched on for every call, so no key can miss them). This closes gap (d) for now: guardrails are per route, not per key. A service that needs another set gets its own route alias with its own guardrails (suggested).
5. **The chassis guard stages call LiteLLM's `POST /guardrails/apply_guardrail`** through `LiteLLMGuardrails` behind `GuardrailPort` (`spec.adapters.guardrails: litellm`; the fake is `fake`). One guardrail config serves both places. The stages, each switched by `spec.guardrails.<stage>`:

   | Stage | Where | On a block |
   | ----- | ----- | ---------- |
   | `input` | The pipeline, before the engine runs | The run does not start. `guardrail_blocked`, not retryable. Native: a 200 envelope with `status: error`. OpenAI and Anthropic: 502, because the frozen `status_for` maps an unknown code that is not retryable to 502 |
   | `tool_output` | The chassis tool proxy (`/mcp` on the proxy listeners), before the workload reads the result | The tool call returns an MCP tool error `guardrail_blocked`. The run goes on; the engine handles a tool error as it does any other |
   | `output` | The pipeline, after a complete answer, before it leaves | The answer is dropped. The run ends with `guardrail_blocked` |

   A stream is not checked by the `output` stage: its deltas have already left (`packages/chassis/tests/test_guard_stages.py::test_a_stream_is_not_held_back_by_the_output_stage`). LiteLLM's after-call guardrail is the check for a streamed answer. Waits on: a run of the pinned LiteLLM with guardrails, on the Mac (the CI stack has no LiteLLM), to show whether that guardrail acts on a stream. The red-team miss `secret-04-streamed-answer-has-key` is owned by PoC-8.

   As built: `input` acts on the verdict only, and the engine gets the caller's text (LiteLLM masks PII before the model). `tool_output` and `output` pass on `Verdict.text`, with PII masked (`packages/chassis/tests/test_guard_stages.py::test_output_pii_is_masked_in_deltas_and_end_text`, `::test_tool_output_pii_is_masked_in_structured_results`, `::test_tool_output_injection_past_512_tokens_is_blocked`).

   When the guardrail service fails, `spec.guardrails.on_unavailable` decides: `fail_closed` (default) answers `guardrail_unavailable`, retryable, 503; `fail_open` passes the text, counts `chassis.guardrail_unavailable{stage}` (suggested), and logs one warning (`packages/chassis/tests/test_guard_stages.py::test_outage_fails_closed_and_retryable`, `::test_outage_fails_open_by_config`).
6. **The chassis's own redaction is regex only** (`chassis.redaction`): emails, phone numbers, card numbers, IBANs, and US social security numbers, each replaced by `<ENTITY>`, the form Presidio's anonymizer uses. It runs on the chassis's logs and on the events it publishes. Named entities are redacted by LiteLLM in model calls and, from PoC-8, by the Collector in workload logs (ADR-007 item 7). Evidence: `packages/chassis/tests/test_redaction_and_chunks.py::test_redact_masks_emails_phones_and_ids`, `packages/chassis/tests/test_result_events_redaction.py::test_a_completed_runs_event_holds_no_pii`. Waits on: the chassis's RSS under load with redaction on, against ADR-001's estimate. That needs a load run in Docker (the Mac or PoC-8); not measured in PoC-7.
7. **AI-generated marking is two response headers in PoC-7**, set by the chassis on the way out of every public interface, complete and streaming (the headers go before the first byte): `X-AI-Generated: true` and `X-AI-Disclosure-Required: true`, when `spec.governance` turns marking on (`risk_class` `limited` or `high`, or `ai_generated_marking: true`). With marking off, neither header is sent (suggested). The workload sends no HTTP headers to the caller, so it cannot remove them. `Response` does not change. As built, a refusal on a run route (a 422, a 413) is marked too, and `/health`, `/ready`, and `/manifest` are not (`packages/chassis/tests/test_marking.py::test_limited_marks_every_interface_streaming_and_complete`, `::test_a_refused_call_on_a_run_route_is_marked`, `::test_a_workload_that_claims_not_ai_generated_is_still_marked`, `::test_health_ready_and_manifest_are_not_marked`; on two engines: `pocs/poc-07-cross-cutting-decisions/tests/test_poc07_sec_marking.py::test_marking_and_redaction_hold_on_every_engine`). Deferred to 023 C-5 in PoC-8: a body field in the OpenAI and Anthropic formats; the flag on result events (suggested: a CloudEvent extension attribute, `aigenerated`, so `task-result.v1.json` does not change); and `Access-Control-Expose-Headers` for browser clients.
8. **Snyk Agent Scan runs in CI, in a container sandbox.** It scans our own MCP servers (`fake-mcp-server`, `code-runner`) and their tool descriptions. The sandbox has no repository secret but the scan's own token, and a read-only file system. It runs twice: `inspect` with `--network=none` and no token, then `scan` with the token. Docker cannot limit the `scan` run's egress to Snyk's API, so it can reach any host (suggested: an egress proxy that allows only `api.snyk.io`, if the scan becomes a gate). It reports; it does not replace the in-house scan at registration (081 R-8). The workflow is `.github/workflows/poc07-agent-scan.yml`, checked statically by `pocs/poc-07-cross-cutting-decisions/tests/test_poc07_agent_scan_static.py` (13 tests, among them `::test_every_docker_run_is_sandboxed`, `::test_the_inspect_phase_has_no_network_and_no_token`, `::test_a_missing_token_is_a_clear_skip_not_a_failure`). Waits on: a `SNYK_TOKEN` repo secret for the first real scan. No scan has reached the analysis API ([agent-scan note](../../../pocs/poc-07-cross-cutting-decisions/notes/2026-10-28-agent-scan.md)).
9. **The red-team set** has four cases, each with the layer that must stop it:

   | Case | First stop | Backstop |
   | ---- | ---------- | -------- |
   | Injection in the input | Chassis `input` stage | LiteLLM's before-call guardrail |
   | Injection in a tool output | Chassis `tool_output` stage | LiteLLM's before-call guardrail on the next model call |
   | Data exfiltration through a tool | MCP gateway allow-list | Network policy; the `tool_output` stage on the result |
   | A secret read | Hard requirement 1: the workload holds no secret | Chassis `output` stage (secret patterns); LiteLLM's after-call guardrail |

   The set has 18 cases in these four categories. Offline, with the pattern stand-in: 12 of 18 stopped on the `litellm` stack, 11 of 18 on the `keyword` fake. The misses and their owners: spaced-out and French injections (022 H-6, rerun on Prompt Guard 2), a base64 tool output and a write tool that gets a secret in its arguments (054 H-16), a markdown image URL in the answer (026 CH-4), a streamed answer with a key (PoC-8) ([red-team note](../../../pocs/poc-07-cross-cutting-decisions/notes/2026-10-28-redteam.md)). Waits on: the Mac run for the same cases on Prompt Guard 2.
10. **What each engine must expose:** nothing in its event mapping, and no code of its own for auth, guardrails, or marking. It must call models and tools only through the chassis proxies (already true for every supported engine), and it must turn a tool error into a `tool_call` result or an `error` event, not an unhandled exception. On a blocked tool output, `echo-python` ends the run with its own `tool_error` event. `echo-langgraph`'s exact event was not checked. In both, the injected text reaches neither the model nor the answer (`pocs/poc-07-cross-cutting-decisions/tests/test_poc07_sec_engines.py::test_tool_output_injection_never_reaches_the_workload`, `inprocess` and `sidecar`). The list per engine is in [the per-engine note](../../../pocs/poc-07-cross-cutting-decisions/notes/2026-10-28-per-engine-mapping.md).

## Consequences

Easier:

- One guardrail config in LiteLLM covers the hard control and the chassis stages.
- No named-entity model in the chassis, so ADR-001's cost table holds.
- Prompt Guard 2 is replaceable: any service that answers the generic guardrail API fits, with no chassis change.
- Every engine gets auth, content checks, and marking with no engine-specific code.

Harder:

- One more service to run: `guardrail-server`, on CPU, with a gated model to fetch at build time.
- Each stage is one more HTTP call to LiteLLM per request or per tool call. Waits on: p50 and p95 added per stage, on a LiteLLM with guardrails (the Mac run; the CI stack has no LiteLLM).
- Guardrails are the same for every key until the owner buys LiteLLM Enterprise or adds route aliases.
- A blocked tool output reaches the engine as a tool error. An engine that fails on tool errors fails the run.
- The generic guardrail API sits under a `/beta/` path in LiteLLM. A LiteLLM upgrade may move it. The pinned version and the adapter's suite catch that.
- The marking is in headers only. A client that reads only the body does not see it until C-5 adds the body field.

Costs, per lane:

| Lane | Cost |
| ---- | ---- |
| `inprocess` | The stages run against the fake. No network |
| `sidecar` | One call to LiteLLM per enabled stage per request, and one per tool call for `tool_output`. LiteLLM's own guardrails on every model call |
| `remote` | The same stages, same cost. The remote's tool calls already pass the chassis tool proxy, so `tool_output` covers them. Its model calls pass LiteLLM's guardrails through the remote listener |

## Revisit

Reopen this decision on any of these:

- **The red-team set has a miss that no layer stops.** Then the layer that should have stopped it changes, or this ADR does.
- **The stages cost too much.** If a stage adds more than 150 ms at p95 (suggested), move that stage behind a sample rate or drop it where LiteLLM already covers it.
- **The owner buys LiteLLM Enterprise.** Then guardrails move per key, and gap (d) closes as ADR-001 item 6 first wrote it.
- **LiteLLM moves or drops the generic guardrail API**, or ships a hook for Llama Prompt Guard 2 itself.
- **Prompt Guard 2 is not good enough on our traffic.** If its false-positive rate on the golden set's inputs is above 1% (suggested), revisit the classifier.

## Sources

- LiteLLM guardrails: <https://docs.litellm.ai/docs/proxy/guardrails/quick_start>
- LiteLLM virtual keys and MCP permissions: <https://docs.litellm.ai/docs/proxy/virtual_keys> and <https://docs.litellm.ai/docs/mcp_control>
- Presidio: <https://pypi.org/project/presidio-analyzer/>
- Prompt Guard 2: <https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-86M>
- Snyk Agent Scan: <https://github.com/snyk/agent-scan>
- RFC 6750, bearer token errors: <https://www.rfc-editor.org/rfc/rfc6750>

## Open questions for the user

1. Should `user.id` be a keyed hash of the caller's subject, and should an `untrusted` remote get no `user.id` at all (item 3, shared with ADR-007)?
2. API keys for callers: a second `AuthPort` adapter, or the IdP's client-credentials flow (item 2)?
3. For a `minimal` agent with `ai_generated_marking: true`, is `X-AI-Disclosure-Required: true` right, or does legal want it off (item 7)?
