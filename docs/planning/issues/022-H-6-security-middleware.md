---
title: "H-6: Security middleware: auth, scopes, rate limits, PII redaction"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:M", "layer:chassis", "layer:agent-profile"]
milestone: "W2 Chassis MVP"
index: 22
epic_id: H-6
depends_on: ["011 H-2", "018 H-18"]
blocks: ["040 D-0", "054 H-16", "065 D-2", "072 C-3", "076 R-1", "132 CH-13"]
epic_refs: [G.3]
---

## Why

Every agent needs the same auth, limits, and PII rules, so the chassis pipeline runs them, not each agent. The pipeline checks callers and content in every lane. It is not the security boundary against hostile code. Under [ADR-001](../adr/001-chassis-delivery-model.md) item 6, the hard limits live in shared services: LiteLLM scoped keys, MCP gateway allow-lists, and network policy. PII must be redacted before any log line or record is stored, which is why the recorder (040 D-0) depends on this issue. Least privilege, with only the scopes listed in config, is a property every agent must have.

## What

- Caller auth on every inbound adapter: OIDC bearer tokens or caller API keys.
  - Decided in [ADR-008](../adr/008-security-identity-guardrails-defense-in-depth.md) and [contract v6](../../contracts/contract-v6.md#d-auth-guard-stages-and-the-new-error-codes): `OidcAuth` behind `AuthPort`, one scope per route from `spec.security`, with 401 `unauthenticated`, 403 `forbidden`, and 503 `auth_unavailable`. `/manifest`, `/openapi.json`, and `/docs` need the read scope.
  - Caller API keys are not built (an ADR-008 open question). `/metrics` is open by default: P-4 in the [backlog review proposals](../../reviews/2026-11-06-backlog-review/proposals.md).
  - The in-process MCP hop is never trusted by loopback address or by `x-chassis-interface`, and `x-chassis-interface: mcp` is never used for billing or quotas.
- Scopes from `spec.security.scopes` (for example `simplify:run`), with separate read and write scopes.
- Rate limits per agent and per caller in `StatePort`, kept in Valkey in the stack profiles, so limits hold across pods and the process stays stateless: 429 `rate_limited` with `Retry-After` ([contract v7](../../contracts/contract-v7.md#c-rate-limits) C). A store outage lets calls through unlimited, decided 2026-11-06 (P-2). The limit is a rate, not a cap, so new idempotency keys per caller get their own cap.
- Input size limits: `spec.limits` bounds each call on every public interface, with 400 `limit_exceeded`, or 413 for the body (suggested: `max_tokens_max: 8000`, `timeout_ms_max: 120000`, `body_bytes_max: 1048576`, `messages_max: 256`).
- PII redaction before logging or storage. In the chassis it is regex only (emails, phone numbers, IDs). Named-entity redaction runs in LiteLLM's guardrails and in the OpenTelemetry Collector log pipeline, never in the chassis: a named-entity model would add about 0.5–1 GiB per replica and break ADR-001's estimate:
  - the chassis redacts the events it publishes and its own logs;
  - workload logs do not pass the chassis, so they are redacted in the OpenTelemetry Collector log pipeline from 014 H-7 (gap (f) in the [backlog plan](000-plan.md#adr-001-follow-ups)).
- Prompt-injection checks on user input and tool outputs, as LiteLLM guardrails. The workload cannot skip them, so they are the hard control. Decided in ADR-008: Presidio and Llama Prompt Guard 2 as LiteLLM guardrails on every model call, set per route, because guardrails per key are Enterprise-only (gap (d) in the backlog plan). The chassis guard stages `input`, `tool_output`, and `output` run through `GuardrailPort` as defense in depth. The chassis's virtual key needs `/guardrails/apply_guardrail` in `allowed_routes`, or the guard stages fail closed with `guardrail_unavailable`.
- Two checks on the answer, built in PoC-8 ([contract v7](../../contracts/contract-v7.md#d-two-checks-on-the-answer) D): the stream secret check (`guardrail_blocked` at `output_stream`) and the output URL allow-list `spec.guardrails.output_url_allow`.
- Two defaults are 001 DEC-1's: the URL allow-list (default deny, or default allow with a per-agent opt-in), and rate limits when caller auth is off.
- Secrets read from Vault through External Secrets, never from code or config values. They are mounted only into the chassis container.

## Reuse

- **Use:** LiteLLM guardrails: Presidio PII masking before and after model calls and before MCP calls, and prompt-injection checks on user input and tool outputs. Llama Prompt Guard 2 as the injection classifier. One LiteLLM virtual key per service (models, budgets, rate limits), held only by the chassis container. The Presidio library in the chassis for redaction before logging and publishing.
- **Build:** caller auth and scopes (OAuth2/OIDC from the company IdP, and API keys), input size limits, and rate limits in Valkey.
- **Watch:** per-key guardrail control in LiteLLM is Enterprise-only, but ADR-001 item 6 assumes guardrails per key; see the gaps in 000-plan.md. Prompt Guard 2 reads 512 tokens at a time, so long tool outputs are split. Presidio images now come from ghcr.io.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Signed images, verified at deploy (025 H-10).
- Input text treated as data, passed to the model in its own message and never merged into the system prompt. In the `sidecar` lane, the workload builds the prompt, so the chassis cannot enforce this. It becomes a rule in the service template, checked by a contract test (025 H-10).
- Chassis-only credentials for every internal service, default-deny egress, and the negative network tests from the workload container (026 CH-4).
- Guardrails per key, if the owner buys LiteLLM Enterprise (gap (d)).
- Tool allow-lists and write-tool rules (054 H-16).
- MCP read and write scopes, and confirmation on destructive tools (095 P-4).
- `security.allowed_agents`, the only agents this agent may call. It matters only when one agent calls another, so it goes to the orchestrator wave.
- A cluster-wide uncorrelated cap. `spec.limits.uncorrelated_tokens_per_minute` is per replica; LiteLLM's key budget is the cluster cap ([contract v4](../../contracts/contract-v4.md), Known gaps).

## Acceptance criteria

- [ ] A call with no bearer token or a wrong one gets 401 on every run and read route. Only `/health` and `/ready` are open. Open until P-4 decides `/metrics`, open by default today (`packages/chassis/tests/test_metrics.py::test_metrics_need_no_token_by_default_even_with_auth_on`).
- [ ] A call with no caller API key or a wrong one gets 401 on every run and read route.
- [x] A valid caller without the needed scope gets 403. · Delivered in PoC-7: `packages/chassis/tests/test_security_auth.py::test_run_scope_is_needed_on_every_run_interface`, `::test_read_scope_is_needed_for_the_manifest_and_docs` (403 `forbidden`, contract v6 D.4)
- [ ] A caller over its rate limit gets 429, and the limit holds across two pods. Open until a stack test runs two chassis processes on one Valkey and shows one bucket.
- [x] An input over the size limit is refused with 413 before the workload runs. · Delivered in PoC-3: `packages/chassis/tests/test_limits.py::test_oversized_body_is_413_before_validation`, `::test_oversized_streamed_body_with_no_length_is_413`
- [ ] A PII fixture (email, phone number) is redacted by the chassis in its logs and in the events it publishes. The name in the fixture is redacted in the Collector log pipeline, and in a model call by the LiteLLM guardrail. Open: names are never masked; it waits on the Collector's named-entity pick (001 DEC-1).
- [ ] The chassis container's memory under load stays within ADR-001's estimate with redaction on. Open: measured with phase E's measurement of the OTel SDK's cost.
- [ ] A prompt-injection fixture in a model call from the workload is flagged by the LiteLLM guardrail. Open: waits on the Prompt Guard 2 rerun on the Mac.
- [x] No secret value appears in config, code, or logs. · Delivered in PoC-5, PoC-8: `pocs/poc-08-cross-cutting-build/tests/test_poc08_litellm_static.py::test_no_key_literal_in_any_file`, `packages/chassis/tests/test_no_key_leak.py::test_a_client_key_never_leaks_from_any_interface`
- [x] In the `sidecar` lane, the workload container holds no secret: no key or token is in its environment or file system, checked from inside the container. · Delivered in PoC-5: `pocs/poc-05-sandboxed/tests/test_poc05_kind_sidecar_controls.py::test_h25_no_secret_value_reaches_the_workload`, `::test_h26_chassis_environ_not_readable_from_the_workload` (`pocs/poc-05-sandboxed/notes/2026-10-08-sidecar-suite.md`, "6 passed")
- [x] `AuthPort` and `GuardrailPort` each have a fake (static test tokens, a pass-through guardrail), and the real adapters pass the same contract suites. · Delivered in PoC-7: `packages/chassis/tests/test_contracts_cross_cutting_ports.py::TestStaticTokens`, `::TestKeywordGuardrails`, `packages/chassis/tests/test_oidc_adapter.py::TestOidcAuth`, `packages/chassis/tests/test_litellm_guardrails.py::TestLiteLLMGuardrails` (against a LiteLLM stub)
- [ ] One caller cannot add more new idempotency keys than its cap, so it cannot fill the shared store.
- [ ] A workload's `guardrails` key never reaches LiteLLM.
- [ ] The key-leak canary also looks for a refused key's last 4 characters and its hash.

## Dependencies

- Depends on: [011 H-2](011-H-2-inbound-adapters.md), [018 H-18](018-H-18-idempotency.md)
- Blocks: [040 D-0](040-D-0-recorder-agent.md), [054 H-16](054-H-16-tool-port.md), [065 D-2](065-D-2-review-ui.md), [072 C-3](072-C-3-audit-log-agent.md), [076 R-1](076-R-1-registry-data-model-api.md), [132 CH-13](132-CH-13-owasp-agentic-map.md)

## References

- Epic story: [H-6 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.3](../slm-agent-platform-epic-v3.md#g3)
- Backlog plan: [000-plan.md](000-plan.md)
