# Security

The controls, and which of them are the boundary. Why: [ADR-001](../planning/adr/001-chassis-delivery-model.md) (hard requirement 1, the trust rule), [ADR-005](../planning/adr/005-remote-lane-auth-and-trust-admission.md) (remote auth and admission), [ADR-008](../planning/adr/008-security-identity-guardrails-defense-in-depth.md) (identity, guardrails, defense in depth).

## The principle

Assume the model will be tricked. The boundary is made of **fixed controls** in shared services: keys, allow-lists, network policy, the sandbox. The chassis's guard stages are **model-based controls** and hygiene; they lower the odds and are never the boundary. "No prompt, classifier or judge is a security boundary" (the vault, `wiki/security/Agent security strategy.md`). The four guards the vault names, input, tool, output, and budget, map onto the rows below.

## The controls

| Control | Kind | Where |
| ------- | ---- | ----- |
| One scoped LiteLLM virtual key per service: the routes it may use, a budget, a rate limit, a tag. Never shared | fixed | minted at deploy (`seed.sh`, `stack.sh`); only the chassis holds it |
| MCP gateway allow-list per key; a write tool needs an `idempotency_key`; `tool_denied` for a tool the key may not call | fixed, tool guard | `chassis/adapters/mcp/gateway.py` |
| NetworkPolicy, ingress and egress, per pod, default deny; peers by namespace and pod labels with ports, no `ipBlock`, no empty selector; a default deny per namespace; a remote workload reaches only the chassis's remote proxy, with no DNS | fixed | `deploy/helm/agent-lib/templates/_networkpolicy.tpl`, `_validate.tpl` |
| gVisor for `remote`; uids 10001 and 10002; read-only root; no capabilities; seccomp `RuntimeDefault`; no service account token; memory-backed `/tmp` | fixed | the chart |
| Admission: `untrusted` must use `remote`; the registry prefix and the trusted repositories; Secrets on the chassis only (`agent-trust-rule`); a minimum chassis version (rule 9, `agent-chassis-version`, whose binding warns for one ring cycle after a new minimum, then denies: `admission.chassisVersionAction`, `Deny` by default) | fixed | `deploy/helm/agent-lib/templates/_admission.tpl`, installed by `deploy/helm/agent-admission`; the same policies on the PoC-5 cluster, `deploy/kind/poc05/admission/` |
| The code-execution tool runs generated code in one gVisor sandbox per call, with no secrets and no network | fixed | `packages/code-runner` |
| Remote lane auth: a per-remote bearer token on the remote proxy listener (mTLS is not built) | fixed | `chassis/server/remote_auth.py` |
| Caller identity: OIDC JWT checked against JWKS; scopes `agent:run`, `agent:feedback`, `agent:read`; `/health` and `/ready` need none; 401, 403 `forbidden`, 503 `auth_unavailable` | fixed | `chassis/server/security.py`, `adapters/oidc`; the `cloud` profile refuses the `static` fake |
| Rate limit per caller, a token bucket in `StatePort` | budget guard | `chassis/server/rate_limit.py` |
| Per-call model budget: `max_tokens` capped at what the run has left; uncorrelated calls capped per minute | budget guard | `chassis/server/model_proxy.py`, `model_admission.py` |
| Guard stages `input`, `tool_output`, `output` through LiteLLM's guardrail API: Presidio for PII, Prompt Guard 2 for injection, `on_unavailable: fail_closed` | input, tool, output guards (model-based) | `chassis/server/guard.py`, `adapters/litellm/guardrails.py`, `packages/guardrail-server` |
| Stream secret guard: a credential-shaped token never leaves in a delta | output guard | `chassis/server/stream_guard.py` |
| Output URL allow-list, default deny | output guard | `chassis/server/output_filter.py` |
| AI marking: `X-AI-Generated` and `X-AI-Disclosure-Required` headers, the `ai_marking` body field, the `aigenerated` CloudEvent extension | hygiene | `chassis/adapters/ai_marking.py` |
| Regex redaction of logs and events in the chassis, and of spans and logs in the Collector | hygiene | `chassis/logs.py`, `chassis.redaction`, `deploy/compose/poc07/otel-collector.yaml` |
| Supply chain: images pinned by tag, never `latest`; CI actions pinned by SHA; SBOM, provenance, and keyless cosign in the template's CI; Snyk Agent Scan in a sandbox | fixed | the template, `deploy/agent-scan/` |

## The `cloud` profile defaults (CH-14)

Each is filled when the document names none. `fake` and `local` keep the old default.

- No uncorrelated model calls: `spec.limits.uncorrelated_tokens_per_minute: 0`, so a loopback proxy call with no run in flight is 403 `uncorrelated_refused` (suggested) (`chassis/server/config.py`, `model_admission.py`).
- A tool name the gateway's list lacks gets one refresh, then `unknown_tool` with no tool request, so it skips no write-key or schema check; in every profile (`chassis/adapters/mcp/gateway.py`).
- `auth: none` is refused at load, as the `static` fake is (`chassis/profiles.py`).
- `/metrics` needs a token with `spec.security.read_scope` (`agent:read`): `spec.observability.metrics_scope` (`chassis/server/config.py`, `observability.py`).
- A rate-limit store outage refuses counted calls with 503 `state_unavailable`: `spec.limits.rate_limit_on_unavailable: fail_closed` (`chassis/server/rate_limit.py`).
- `user.id` on spans and in baggage is `sha256(subject)[:32]`, the rate limit's hash, never the subject; in every profile and lane (`chassis/server/security.py` `subject_hash`, `pipeline.py`).

Three of these are refused in `cloud`, not only defaulted: a document (at load or on a reload) that names `spec.limits.uncorrelated_tokens_per_minute` other than 0, `spec.limits.rate_limit_on_unavailable: fail_open`, or `spec.observability.metrics_scope: null` is refused, and on a reload the last good config stays (`chassis/server/config.py`).

Residual risk: `user.id` is an unkeyed `sha256[:32]`, the same in every agent, so whoever reads baggage, traces, or the rate-limit store can test guessed subjects (emails) and link one user across agents. suggested follow-up: an HMAC with a key only the chassis holds.

The LiteLLM client sends only `traceparent` upstream, never baggage, and ignores proxy environment variables (`chassis/adapters/litellm/client.py`).

## What a workload may never hold

- A key to LiteLLM, the MCP gateway, the broker, the state store, or the config store.
- The pod's service account token.
- A route to the cloud metadata service or the Kubernetes API.
- The chassis's public port on localhost. Localhost carries the proxies only.

Its model URL and its MCP URL point at the chassis proxies, which add the credential. In the `remote` lane the proxies also listen on the pod IP and check the remote's token.

## Trust

`spec.trust` is `trusted` or `untrusted`. A workload is trusted only if the owning team wrote and reviewed it, its image is built in our registry, and it runs no code, shell commands, or file writes itself. Everything else runs in `remote`. Generated code through the code-execution tool, and prompt injection, do not move the line. The admission check enforces it.

## One service can be cut off alone

Revoking its key, its tool grants, and its network label isolates one service without touching the others.

## The OWASP agentic threats mapped to the controls (CH-13)

The OWASP agentic list, ASI01 to ASI10, set against the controls above. The threat names and where each one hits us come from the vault's `wiki/security/Threat model.md`; the vault lists ASI01 to ASI09, and ASI10 is the OWASP entry for a rogue agent. "Evidence" is a test or a doc that exists in this repo. A test marked (kind) needs a cluster and is not in the offline gate. "Gap" names the issue with the open box that closes it. A model-based control lowers the odds and is never counted as the boundary.

| ID | Threat | Control | Evidence | Gap, owner |
| -- | ------ | ------- | -------- | ---------- |
| ASI01 | Agent goal hijack (direct and indirect injection) | Guard stages `input`, `tool_output`, `output` (model-based, fail closed); output URL allow-list; the fixed controls cap what a hijacked agent can reach | `packages/chassis/tests/test_guard_stages.py`, `test_litellm_guardrails.py`, `test_output_filter.py`; the 18 red-team cases (`packages/contract-suites/src/chassis_contracts/redteam/cases.yaml`) in `packages/eval-kit/tests/test_eval_kit_redteam.py`; [ADR-008](../planning/adr/008-security-identity-guardrails-defense-in-depth.md) | None. A classifier can be beaten, so the boundary is ASI02, ASI03, ASI05. |
| ASI02 | Tool misuse | MCP gateway allow-list per key; a write tool needs an `idempotency_key`; `tool_denied` and `unknown_tool` | `packages/chassis/tests/test_tool_gateway_contract.py`, `test_tool_idempotency.py`, `test_tool_arguments.py`; (kind) `pocs/poc-05-sandboxed/tests/test_poc05_kind_tool_gateway.py` | None. |
| ASI03 | Identity and privilege abuse | One scoped virtual key per service, held by the chassis only; OIDC caller scopes; per-remote bearer token; no service account token in the pod | `packages/chassis/tests/test_security_auth.py`, `test_oidc_adapter.py`, `test_no_key_leak.py`, `test_remote_auth.py`, `test_remote_token_scrub.py`, `test_cloud_profile_defaults.py`; (kind) `pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py` | One service's key used for another: 026 CH-4. |
| ASI04 | Agentic supply chain, including tool-description poisoning and rug pull | Images pinned by tag, actions pinned by SHA, SBOM, provenance, cosign. **Snyk Agent Scan** in CI (`deploy/agent-scan/`) for poisoning, shadowing, toxic flows. At registration, **081 R-8** (`later`) scans every description; 082 R-9 pins its hash against a change after approval | `pocs/poc-07-cross-cutting-decisions/tests/test_poc07_agent_scan_static.py` (15 offline checks); `pocs/poc-07-cross-cutting-decisions/notes/2026-10-28-agent-scan.md` | **No real scan has run** (needs `SNYK_TOKEN`), and the registration scan and the hash pin are not built: 081 R-8, 082 R-9. |
| ASI05 | Unexpected code execution | Generated code runs in one gVisor sandbox per call, with no secrets and no network; `remote` lane for anything untrusted; read-only root, no capabilities, seccomp | `packages/code-runner/tests/test_code_runner_isolation.py`, `test_code_runner_runner.py`; `pocs/poc-05-sandboxed/tests/test_poc05_hardening_static.py`, `test_poc05_netpol_static.py`; (kind) `test_poc05_kind_code_runner.py` | None. |
| ASI06 | Memory and context poisoning | Agents are stateless; the workload holds no store credential; session memory is opt-in and refused in `cloud` with auth off. Nothing yet checks what a memory returns | [ADR-014](../planning/adr/014-langgraph-state-between-runs.md); `packages/chassis/tests/test_idempotency_scope.py` (a key is scoped to its caller) | Memory content is not screened (140 CH-20); the memory design is open (125 L-1, 113 O-9). |
| ASI07 | Insecure inter-agent communication | One wire contract over A2A with typed fields; a remote reaches only the chassis proxy, with its own token; no rights gained from a handoff | `packages/chassis/tests/test_lane_contract.py`, `test_lane_contract_remote.py`, `test_remote_auth.py`; [ADR-005](../planning/adr/005-remote-lane-auth-and-trust-admission.md) | mTLS between services is not built: 038 X-1a. Typed orchestrator handoffs: 098 O-10. The token has one key per direction: 055 CH-6. |
| ASI08 | Cascading failure | Rate limit per caller; per-call model budget; uncorrelated-call cap; backpressure; retries only in the router | `packages/chassis/tests/test_rate_limit.py`, `test_uncorrelated_cap.py`, `test_backpressure.py`, `test_model_proxy_failures.py`; [ADR-013](../planning/adr/013-model-call-retries-belong-to-the-router.md) | Run-level budgets across a chain of agents: 107 O-6. |
| ASI09 | Human-agent trust exploitation | AI marking on every response and event, so a person knows the text is generated. A single agent run never waits for a person | `packages/chassis/tests/test_marking.py`, `test_marking_body.py`; [ADR-015](../planning/adr/015-interrupts-end-the-run.md) | Approval before an external tool or a high spend, and the review queue: 109 O-8, 110 C-6. |
| ASI10 | Rogue agent | `spec.trust` and the admission rules (`untrusted` must be `remote`, trusted repositories only, minimum chassis version); one service is cut off alone by its key, grants, and label | `pocs/poc-05-sandboxed/tests/test_poc05_admission_static.py`, `test_poc05_admission_rule9_static.py`, `test_poc05_service_rule_static.py`; (kind) `test_poc05_kind_admission.py` | Registry gate and trust levels: 077 C-1, 080 R-7. Tamper-evident record of what an agent did: 072 C-3. |

Read first: `~/Git/sdlc-vault`, `wiki/security/Threat model.md` (the threat and trust-boundary tables). Review by `platform-security`: not yet recorded.

## The test sets

The red-team set and the hostile suites are the checks on these controls: the 18 red-team cases in `packages/contract-suites/src/chassis_contracts/redteam/cases.yaml`, run by `python -m eval_kit.redteam`, and the kind suites under `pocs/poc-05-sandboxed/`. Their current pass rates are status, and live in the PoC READMEs and [what is left](../planning/poc/011-what-is-left.md).
