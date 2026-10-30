---
title: "H-6: Security middleware: auth, scopes, rate limits, PII redaction"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:M", "layer:chassis", "layer:agent-profile"]
milestone: "W2 Chassis MVP"
index: 22
epic_id: H-6
depends_on: ["011 H-2", "018 H-18"]
blocks: ["026 CH-4", "040 D-0", "065 D-2", "072 C-3", "076 R-1"]
epic_refs: [G.3]
---

## Why

Every agent needs the same auth, limits, and PII rules, so the chassis pipeline runs them, not each agent. The pipeline checks callers and content in every lane. It is not the security boundary against hostile code. Under [ADR-001](../adr/001-chassis-delivery-model.md) item 6, the hard limits live in shared services: LiteLLM scoped keys, MCP gateway allow-lists, and network policy. PII must be redacted before any log line or record is stored, which is why the recorder (040 D-0) depends on this issue. Least privilege, with only the scopes and agents listed in config, is a property every agent must have.

## What

- Caller auth on every inbound adapter: OAuth bearer tokens or API keys.
- Scopes from `spec.security.scopes` (for example `simplify:run`), with separate read and write scopes.
- `security.allowed_agents` as the only agents this agent may call. The chassis enforces it on its outbound agent calls, and the gateway grants for the service's key enforce it too.
- Rate limits per caller and input size limits (suggested: rate-limit counters in Valkey, so limits hold across pods and the process stays stateless).
- PII redaction before logging or storage. In the chassis it is regex only (emails, phone numbers, IDs). Named-entity redaction runs in LiteLLM's guardrails and in the OpenTelemetry Collector log pipeline, never in the chassis: a named-entity model would add about 0.5–1 GiB per replica and break ADR-001's estimate:
  - the chassis redacts the events it publishes and its own logs;
  - workload logs do not pass the chassis, so they are redacted in the OpenTelemetry Collector log pipeline from 014 H-7 (gap (f) in the [backlog plan](000-plan.md#adr-001-follow-ups)).
- Prompt-injection checks on user input and tool outputs, as LiteLLM guardrails. The workload cannot skip them, so they are the hard control. Guardrails per key are Enterprise-only in LiteLLM (gap (d) in the backlog plan). suggested: set them per route until the owner decides.
- Secrets read from Vault through External Secrets, never from code or config values. They are mounted only into the chassis container.
- Status after PoC-3: a stopgap ahead of this issue, `spec.limits` (suggested: `max_tokens_max: 8000`, `timeout_ms_max: 120000`, `body_bytes_max: 1048576`, `messages_max: 256`) bounds each call on every public interface: 400 `limit_exceeded`, or 413 for the body. Still open, from the PoC-3 debt: rate limits; content parts per message are not capped; the body cap trusts `Content-Length`; both listeners share one event loop, so a slow public request delays the proxies; logs and spans carry the engine's raw exception text; the key-leak canary covers `authorization` and `x-api-key` only. For auth: the in-process MCP hop must not be trusted by loopback address or by `x-chassis-interface`; `/manifest`, `/openapi.json`, `/docs`, and `/redoc` are public; `x-chassis-interface: mcp` must never be used for billing or quotas. See [the PoC-3 debt note](../../../pocs/poc-03-one-interface-every-client/notes/2026-10-01-debt.md).
- Status after PoC-4: idempotency adds a way to fill a shared store. Every keyed call with a new key adds an entry to Valkey for `ttl_s` (24 h by default), and there is no per-caller quota, so one caller can fill it and make every keyed call 503 (`deploy/compose/SECURITY.md`, section 7). The rate limits per caller here must also cap new idempotency keys per caller (suggested), with PoC-8's auth.
- Status after PoC-5: the uncorrelated cap is per replica (`spec.limits.uncorrelated_tokens_per_minute`, suggested: 20000), so N replicas allow N times the limit; LiteLLM's key budget is the cluster-wide cap ([contract v4](../../contracts/contract-v4.md), Known gaps). The remote listener logs method and path only, never the header. LiteLLM's 401 body echoes the last 4 characters of a refused key and its hash; the chassis now redacts both (`_redact`, `chassis/adapters/litellm/client.py`). The key-leak canary here should look for the suffix and the hash too.
- Proposed in PoC-7 ([ADR-008](../adr/008-security-identity-guardrails-defense-in-depth.md), [ADR-007](../adr/007-observability-otel-collector-langfuse.md), [contract v6](../../contracts/contract-v6.md); the user accepts them at the PoC-7 pull request): `OidcAuth` behind `AuthPort`, one scope per route from `spec.security` (401 `unauthenticated`, 403 `insufficient_scope`, 503 `auth_unavailable`); Presidio and Llama Prompt Guard 2 as LiteLLM guardrails on every model call, for every key, as the hard control (guardrails per route, not per key, until the owner decides gap (d)); chassis guard stages `input`, `tool_output`, and `output` through `GuardrailPort` as defense in depth (`guardrail_blocked`, `guardrail_unavailable`); regex-only redaction in the chassis; named-entity redaction in LiteLLM and the Collector. The PoC-7 results are added when it closes.
- Status after PoC-8 ([contract v7](../../contracts/contract-v7.md) parts C and D, [red-team note](../../../pocs/poc-08-cross-cutting-build/notes/2026-10-29-redteam.md), [backlog changes](../../../pocs/poc-08-cross-cutting-build/notes/backlog-changes.md)): built and tested in every lane: rate limits per agent and per caller in `StatePort` (429 `rate_limited` with `Retry-After`; `rate_limit_on_unavailable`, `fail_open` by default), the stream secret check (`guardrail_blocked` at `output_stream`, regex only), and the output URL allow-list `spec.guardrails.output_url_allow`. Red team with these controls: 14/18 on the `litellm` stack with the pattern stand-in, 13/18 on `keyword`, on all 16 engine and lane targets; still missed: `input-04-spaced-out` and `input-05-french` (here, rerun on Prompt Guard 2), `tool-04-base64` and `exfil-03-write-tool-gets-secret` (054 H-16). The red team is a CI job (`poc08-evals.yml`, `redteam`). LiteLLM's real Presidio hook runs as `pii-mask` on every call in the `deploy/compose/poc08-litellm` drill (LiteLLM v1.103.0); the chassis's virtual key needs `/guardrails/apply_guardrail` in `allowed_routes`, or its guard stages fail closed with `guardrail_unavailable` (`tests/test_poc08_stack_litellm.py`, `tests/test_poc08_litellm_static.py`; CI evidence waits on `poc08-litellm` run 41011346881 on `bc67179`). p50 and p95 of the stages, the Valkey rate limit included, are measured by `tests/poc08_stack_latency.py` (suggested budget: 25 ms p50, 50 ms p95, recorded, not enforced; CI evidence waits on `poc08-stack` run 41011346864). Still open: the Prompt Guard 2 rerun (no Hugging Face token in CI). Open, user decision: keep the URL allow-list default deny, or default allow with a per-agent opt-in. Open, user decision: no call is rate limited when caller auth is off; local and test profiles only, or an anonymous bucket.

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

## Acceptance criteria

- [ ] A call with no token or a wrong key gets 401 on every endpoint except `/health` and `/ready`.
- [ ] A valid caller without the needed scope gets 403.
- [ ] A caller over its rate limit gets 429, and the limit holds across two pods.
- [ ] An input over the size limit is refused with 413 before the workload runs.
- [ ] A call to an agent that is not in `security.allowed_agents` is refused by the chassis.
- [ ] A PII fixture (email, phone number) is redacted by the chassis in its logs and in the events it publishes. The name in the fixture is redacted in the Collector log pipeline, and in a model call by the LiteLLM guardrail.
- [ ] The chassis container's memory under load stays within ADR-001's estimate with redaction on.
- [ ] A prompt-injection fixture in a model call from the workload is flagged by the LiteLLM guardrail.
- [ ] No secret value appears in config, code, or logs.
- [ ] In the `sidecar` lane, the workload container holds no secret: no key or token is in its environment or file system, checked from inside the container.
- [ ] `AuthPort` and `GuardrailPort` each have a fake (static test tokens, a pass-through guardrail), and the real adapters pass the same contract suites.

## Dependencies

- Depends on: [011 H-2](011-H-2-inbound-adapters.md), [018 H-18](018-H-18-idempotency.md)
- Blocks: [026 CH-4](026-CH-4-chassis-only-credentials-egress.md), [040 D-0](040-D-0-recorder-agent.md), [065 D-2](065-D-2-review-ui.md), [072 C-3](072-C-3-audit-log-agent.md), [076 R-1](076-R-1-registry-data-model-api.md)

## References

- Epic story: [H-6 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.3](../slm-agent-platform-epic-v3.md#g3)
- Backlog plan: [000-plan.md](000-plan.md)
