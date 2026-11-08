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
| Admission: `untrusted` must use `remote`; the registry prefix and the trusted repositories; Secrets on the chassis only; a minimum chassis version (rule 9) | fixed | `deploy/helm/agent-lib/templates/_admission.tpl`, installed by `deploy/helm/agent-admission`; the same policy on the PoC-5 cluster, `deploy/kind/poc05/admission/` |
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

## The test sets

The red-team set and the hostile suites are the checks on these controls: the 18 red-team cases in `packages/contract-suites/src/chassis_contracts/redteam/cases.yaml`, run by `python -m eval_kit.redteam`, and the kind suites under `pocs/poc-05-sandboxed/`. Their current pass rates are status, and live in the PoC READMEs and [what is left](../planning/poc/011-what-is-left.md).
