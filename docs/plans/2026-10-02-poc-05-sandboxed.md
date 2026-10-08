# PoC-5 sandboxed: the remote lane and the trust rule: design and work plan

Status: in progress
Date: 2026-10-02
Author: `chassis-architect`. The orchestrator assigns the tasks in "Work breakdown".
Source: `docs/planning/poc/005-PoC-5-sandboxed.md`. Tracking: `pocs/poc-05-sandboxed/README.md`.
Decision: [ADR-001](../planning/adr/001-chassis-delivery-model.md) items 4 to 8, hard requirements 1 and 2. New: ADR-005 (section 9), written as Proposed in wave 0 (task T02).
Backlog previewed: [022 H-6](../planning/issues/022-H-6-security-middleware.md) (in part), [054 H-16](../planning/issues/054-H-16-tool-port.md) (allow-list and write mode), [026 CH-4](../planning/issues/026-CH-4-chassis-only-credentials-egress.md), [055 CH-6](../planning/issues/055-CH-6-remote-lane-trust-rule.md) (without the cloud auth adapter).

This plan decides every open shape in PoC-5, so a builder does not have to ask. Values the epic does not give are marked `suggested:`. Items marked **spike** wait for the gVisor, CNI, agent-sandbox, and admission spike that runs on this host now; each has a fallback. Where this plan and the code disagree later, the code wins and `contract-v4.md` records the difference.

## Summary of decisions

- **The `remote` connector** is `A2AConnector` with a third transport: any `http` or `https` URL from `spec.engine.url`, and a bearer token read from the environment variable named in `spec.engine.auth.token_env`. It never follows the agent card's URL: it always sends to the configured URL.
- **One token per remote, both ways** (suggested). The chassis sends it on every A2A call. The remote sends it on every model and tool call. No mTLS and no cert-manager in PoC-5.
- **A third listener.** The model and tool proxies also listen on the pod IP, port 8091 (suggested), only when the lane is `remote`. Every call there needs the token and a `traceparent` that names a run in flight. The loopback listener is unchanged.
- **`spec.trust: trusted | untrusted`.** The config refuses `untrusted` in the `sidecar` and `inprocess` lanes. The `cloud` profile must name it. Pod labels carry the same value.
- **Admission is a ValidatingAdmissionPolicy**, not Kyverno. It needs no controller and no memory. It rejects an `untrusted` pod in the `sidecar` lane, a missing trust label, and an image outside our registry prefix in the `sidecar` lane.
- **`ToolPort` gains an `idempotency_key` keyword and three error codes.** A write tool (`read_only: false`) is refused without a key. The tool endpoint derives one key per call from the run's key, the tool name, and the arguments.
- **The real tool adapter is `McpGatewayTools`:** an MCP client to LiteLLM's MCP gateway, with the service's own LiteLLM virtual key. The gateway's per-key allow-list is the hard limit.
- **The code-execution tool is an MCP server, `code-runner`,** in an agent-sandbox `Sandbox` on gVisor, behind the gateway. So it is allow-listed per key like every other tool.
- **One virtual key per service,** made by a seed step on the host, stored in a Secret mounted in the chassis container only. It scopes models, budget, and MCP tools.
- **NetworkPolicy:** default deny in and out in every PoC-5 namespace. Each pod gets only what it needs. A static test proves no rule allows the metadata service or the Kubernetes API. The remote pod gets no DNS.
- **Hardening:** Pod Security Admission `restricted` on every PoC-5 namespace, distinct user ids for the chassis and the workload, a pod PID limit, memory-backed `/tmp` with a size cap.
- **Hard requirement 1 on kind:** LiteLLM (virtual keys, Postgres), the MCP gateway (same process), Valkey (password), MinIO (keys), and Kafka (SASL, in a separate pass for memory) refuse a call with no credential.
- **Contract:** no wire change. `schema_version` stays `"0"`. `contract-v4.md` records the additive changes (config, ports, proxy codes).
- **Tests:** offline parts run in `make test` (static manifest checks, the remote lane over a Unix socket, an in-process hostile workload against the fake stack). Cluster tests are `network` and gated by `POC05_KIND=1`. A new CI workflow runs the remote lane on kind on every push.

## 1. What exists and what PoC-5 builds

Paths are short: `chassis/` is `packages/chassis/src/chassis/`, `ctests/` is `packages/chassis/tests/`, `suites/` is `packages/contract-suites/src/chassis_contracts/`, `poc/` is `pocs/poc-05-sandboxed/`, `kind5/` is `deploy/kind/poc05/`.

| Scope item | Exists? | Where | What PoC-5 builds |
| ---------- | ------- | ----- | ----------------- |
| Hardening of both containers and the remote pod | In part | `deploy/kind/poc04/native-sidecar/deployment.yaml`: non-root, read-only root, drop ALL, seccomp `RuntimeDefault`, memory limits, `automountServiceAccountToken: false` | Distinct uids (chassis 10001, workload 10002, suggested), a pod PID limit (kubelet `podPidsLimit`), memory-backed `/tmp` with `sizeLimit`, CPU limits on the workload, PSA `restricted` labels, the same on the remote pod and the code runner |
| kind with native sidecars, an enforcing CNI, a gVisor RuntimeClass | In part | `deploy/kind/cluster.yaml` (cluster `poc04`, kindnet, which the PoC-4 comment says enforces NetworkPolicy; not tested against a hostile pod) | A new cluster `poc05` (`kind5/cluster.yaml`), runsc in the node, RuntimeClass `gvisor`, the CNI the spike confirms, agent-sandbox pinned |
| The `remote` connector | No | `profiles.REGISTRY["engine"]["remote"] = "PoC-5"`; `A2AConnector` in `adapters/a2a/connector.py` is lane-neutral | `adapters/a2a/remote.py`, `RemoteConnector` |
| Proxies on the pod IP with per-remote auth | No | `server/cli.py` refuses a non-loopback proxy host unless `--allow-any-proxy-host`; no auth anywhere on the proxies | A third listener, `server/remote_auth.py`, CLI flags, drain order |
| Remote pod on gVisor through agent-sandbox | No | — | `kind5/remote/`: a `Sandbox` per remote workload, NetworkPolicy, the fixed ClusterIP Service for the chassis's remote proxy |
| Hard requirement 1 on kind | In part | Valkey password on kind (PoC-4); MinIO keys and Kafka on Compose only; LiteLLM not on kind | LiteLLM with Postgres and virtual keys, MinIO, Kafka with SASL, all on kind |
| One virtual key per service, a tool allow-list per key | No | LiteLLM runs open in the Compose `fake` variant; `allow_all_keys: true` on the PoC-3 MCP server | The seed step, `kind5/platform/seed.sh` |
| Default-deny NetworkPolicy, metadata and API named, public port on the pod IP | In part | `deploy/kind/poc04/base/network-policy.yaml` (default deny, allow-by-label); the chassis binds `$(POD_IP)` | The policies for every PoC-5 pod, the static deny-list test, positive controls |
| `spec.trust` and admission | No | — | `Spec.trust`, the config rule, `kind5/admission/` (VAP, binding, params, fixtures) |
| `ToolPort`, fake, suite, real adapter, write mode | In part | `ports/tool.py`, `fakes/tool.py` (`InMemoryTools`, `glossary_lookup`), `suites/tool.py` (5 cases), `server/tool_endpoint.py` (`/mcp` on the proxy port); `REGISTRY["tools"]["mcp"] = "PoC-5"` | The keyword, the codes, the suite cases, the fake's allow-list and write tool, `adapters/mcp/gateway.py`, `packages/fake-mcp-server` |
| Code execution behind `ToolPort` | No | — | `packages/code-runner` (an MCP server), its `Sandbox` |
| Hostile suite, `sidecar` lane | No | `deploy/compose/SECURITY.md` section 6 lists the cases | `packages/workloads/hostile`, `poc/tests/test_poc05_kind_hostile_sidecar.py` |
| Hostile suite, `remote` lane | No | — | `poc/tests/test_poc05_kind_hostile_remote.py` |
| CI runs the remote lane on every commit | No | `.github/workflows/ci.yml` runs `make check` only | Offline: the remote lane in `LaneContract` (in `make check`). On kind: `.github/workflows/remote-lane.yml` |
| Envoy egress (optional) | No | — | Not built. No PoC-5 workload needs another host. Recorded in the blind-spots note |

## 2. Design decisions

Each decision: the choice, the reason, and the alternative rejected.

### 2.1 The `remote` connector

**File:** `chassis/adapters/a2a/remote.py`, `RemoteConnector(A2AConnector)`, `kind = "remote"`.

`setup(config, ports)` reads `spec.engine`:

- `url` (required): `http://` or `https://`, a host, a port, no user info, no query, no fragment. A path is allowed (managed runtimes use one).
- `auth` (required in the `remote` lane, suggested): `{scheme: bearer, token_env: <NAME>, previous_token_env: <NAME> | null}`. The token is read from `os.environ[token_env]` at setup. A missing or empty variable fails setup with a message that names the variable, never a value.
- `uds` (optional): a Unix socket for tests and local runs. The config refuses it in the `cloud` profile.
- `probe_timeout_s` (suggested default 2.0): the probe crosses the network.

Behavior:

- One `httpx.AsyncClient` with `trust_env=False`, `follow_redirects=False`, and the header `Authorization: Bearer <token>` on every request: the card fetch, every message, the cancel, the probe.
- **The card's URL is ignored.** `_check_card` keeps the card, but the connector rewrites every `supported_interfaces[].url` to the configured `url` before `ClientFactory.create`. So a card can never send the token to another host. A card with no JSON-RPC interface is refused.
- `https` is required in the `cloud` profile (suggested). `http` is allowed in `fake` and `local`, for kind and tests. The config check lives in `ChassisConfig` (it knows the profile).
- Everything after setup is `A2AConnector`: the same mapping, deadline, cancel, span, and `traceparent`.
- `probe()`: GET the agent card with the token, over its own client, `probe_timeout_s`. `True` on 200 only. Never raises.
- The token never enters a log, a span, an error message, or `repr`. A test greps the captured logs and spans for it.

**Why.** ADR-001 item 7: one A2A client, a different URL. The PoC-2 client already has the mapping and the cancel; only the transport and the auth differ.

**Rejected:** following the card's interface URL (the sidecar connector refuses non-loopback card URLs; a remote card could name any host and collect the token); an auth plug-in interface now (the cloud auth adapters are PoC-6; `auth.scheme` leaves room for `sigv4` and `google` later).

### 2.2 The per-remote credential

**Choice:** one random token per remote (suggested: 32 bytes, hex, `openssl rand -hex 32`), used both ways.

- The chassis holds it in its own container, from the Secret `remote-<name>-token`, as the variable named in `spec.engine.auth.token_env` (suggested: `REMOTE_TOKEN`).
- The remote pod holds it as `CHASSIS_API_TOKEN` (suggested name), from the same Secret, mounted only into the remote's workload container as an env var. It is the only secret in the remote pod.
- The remote's A2A server requires it on every request (section 2.4). The remote's model and tool clients send it as their API key, so frameworks send it as `Authorization: Bearer` with no code of their own.
- Rotation without downtime: `previous_token_env` names a second variable. The chassis's remote listener accepts either; the connector sends the current one. The runbook rotates in three steps (add as previous, swap, drop).

**Why a token and not mTLS.** mTLS through cert-manager adds a controller (suggested: 150 to 250 MiB on a VM with 7.75 GiB), a CA, and certificate rotation, and every framework's HTTP client must present a client certificate. A managed runtime (PoC-6b) can send a bearer header but often cannot present a client certificate. NetworkPolicy already limits who can reach the listener by pod label. The token adds the "own credential" the scope asks for.

**Why one token and not two (one per direction).** Two tokens double the rotation work. The token opens only this chassis's proxies for this remote, which are budgeted per run. The remote already has it to call the chassis, so a second token for the inbound direction protects nothing more.

**Rejected:** a projected service account token with an audience (exit criterion 4 says the remote pod holds no service account token, and checking it needs the Kubernetes API, which is in the deny list); mTLS (above; revisit in ADR-005 when a cloud mesh gives it for free).

**Interpretation to confirm (question 1):** the scope says the remote pod has "no secrets mounted". This plan reads it as "no provider key and no internal credential": the per-remote token is the remote's "own credential" that the same scope item names. It is mounted as one env var, not a volume. Confirmed by the user on 2026-10-08.

### 2.3 The remote-lane proxy listener

**Choice:** a third uvicorn server in `chassis serve`, on `--remote-proxy-host` (the pod IP) and `--remote-proxy-port` (suggested: 8091). It serves the same model proxy and tool endpoint as the loopback listener, wrapped in two ASGI middlewares from `chassis/server/remote_auth.py`:

1. `BearerAuth(tokens)`: compares `Authorization: Bearer <t>` with the current and previous tokens using `hmac.compare_digest`. Missing or wrong: 401 `{"error": {"code": "remote_unauthenticated", ...}}`, the same body for both. It strips the header before the route sees it (the model proxy already never forwards it). Counted as `chassis.remote.auth_failed` (suggested), logged without the header.
2. `RequireRun`: the `traceparent` must name a run in flight in `state.runs`. Otherwise 403 `run_required`. So a remote can spend tokens and call tools only inside a run the chassis opened, and the run's budget applies.

Not on this listener: `/dapr/*` (404), and anything else on the proxy app.

CLI rules (`server/cli.py`):

- `--remote-proxy-host` is refused unless `spec.engine.connector` is `remote` and the token variable is set. It must not be loopback (that would be the existing listener) and must not be `0.0.0.0` (suggested: bind the pod IP only, like the public port).
- `--allow-any-proxy-host` stays as is, for the loopback listener's tests. The help text says it is not the remote lane.
- Drain: the remote proxy listener stops last, with the loopback proxy, after in-flight runs (contract v3, "Shutdown").

**Why a separate listener.** Nothing about the loopback listener changes, so the `sidecar` lane keeps its PoC-2 to PoC-4 behavior and tests (H15 stays the documented, accepted case). The remote listener is the only one that faces another pod, so it is the only one that needs auth. Two listeners also keep the two trust levels apart in code: a request on 8091 can never reach a route that was meant for loopback only, because the remote app is built from an explicit list of routes, not by filtering the loopback app. The NetworkPolicy then names one port (8091) that only the remote pod's label may reach (H18 has its mirror, H17).

**Why 403 `run_required`.** A remote that holds the token could otherwise spend the chassis's model budget outside any run (the H16 problem, but from another pod). Tying every call to a run in flight makes the run's `budget` the cap and the run's span the record. A remote has no reason to call a proxy outside a run.

**Rejected:**

- Bind the existing proxy listener to `0.0.0.0` with `--allow-any-proxy-host` and add the token check to it. Then the sidecar workload would also need the token (a break for every PoC-2 workload), or the check would need to tell loopback callers from others by source address, which is fragile behind a CNI and a Service.
- A separate proxy process or container for the remote lane. It would need its own copy of `state.runs` to enforce `run_required`, so it would need state sharing; one process with two listeners shares `app.state` for free.
- Auth in the remote's model and tool clients only (no listener check). The chassis must not trust the caller to behave; the listener is the control (B8).

**Tests (offline):** `ctests/test_remote_auth.py` drives the remote app with `httpx.ASGITransport`: no token 401, wrong token 401 (same body), previous token 200, current token 200, no `traceparent` 403, an unknown run 403, a run in flight 200, `/dapr/subscribe` 404, and the token absent from the captured logs. `ctests/test_server_cli.py` gains the flag rules above.

### 2.4 The remote workload's A2A server requires the token

**Choice:** `packages/workload-a2a` gains an optional bearer check, `--require-token-env NAME` (suggested name of the flag). When given, the server reads the token from that variable at start, refuses to start if it is unset or empty, and checks `Authorization: Bearer` on every request, the agent card included, with `hmac.compare_digest`. Missing or wrong: 401 with a fixed JSON body and no detail. When not given, nothing changes (the `sidecar` lane on loopback needs no token).

- The check is a small pure ASGI middleware in `workload_a2a/auth.py`. It imports only the standard library and Starlette (already a dependency). It does not import `chassis`: ADR-002 keeps the template server free of the chassis, and `make lint` (import-linter) enforces it. The chassis side has its own `BearerAuth` in `chassis/server/remote_auth.py`; the two are about 30 lines each and are not shared on purpose.
- `--host` may be a non-loopback address only when `--require-token-env` is set. Today the CLI defaults to `127.0.0.1`; a remote pod binds `0.0.0.0` inside its own network namespace (suggested: the pod has no other container, and NetworkPolicy limits ingress to the chassis's label).
- The same flag works for a TypeScript workload in principle, but PoC-5 ships only the Python template with it. The TypeScript template (`echo-typescript`) gains it in PoC-6 if a TypeScript engine needs the `remote` lane; recorded in the blind-spots note as a gap, not built.
- The remote's model and tool clients read `CHASSIS_API_TOKEN` as their API key (section 2.2). The template exposes it to `handle` through nothing new: OpenAI-compatible clients take `api_key` from the environment the workload sets up. The base URLs point at the chassis's remote listener Service (`http://chassis-<name>-remote:8091/v1`, suggested), given to the remote pod as plain env (not a secret).

**Why.** The token both ways (section 2.2) only holds if the remote refuses a caller that does not have it. NetworkPolicy already limits ingress to the chassis's label (H18), so the token is the second, independent check: a pod that wrongly carries the label still gets 401.

**Rejected:** a shared auth module imported by both the chassis and the template (breaks ADR-002); putting the check in the remote's ingress (there is no ingress controller in front of the remote in kind, and adding one adds memory and parts).

**Tests (offline):** `packages/workload-a2a/tests/test_workload_a2a_auth.py`: no token 401, wrong token 401, right token 200 on the card and on `message/send`, start refused when the variable is empty, start refused for a non-loopback `--host` without the flag. The `LaneContract` remote binding (section 4) runs through this server with the flag on.
### 2.5 `spec.trust` and the admission policy

**Config.** `Spec.trust: Literal["trusted", "untrusted"]` in `chassis/server/config.py`, restart-only.

- Default `trusted` in the `fake` and `local` profiles (suggested), so every existing config still loads. The `cloud` profile has no default: a config without `spec.trust` is refused, naming the field.
- `untrusted` with `spec.engine.connector` `sidecar` or `inprocess` is refused at load: `spec.trust: untrusted needs spec.engine.connector: remote`. This is the trust rule in the chassis, checked before anything starts.
- `trusted` with `remote` is allowed (a third-party image or a managed runtime may be trusted and still remote).
- `spec.trust` appears in `/manifest` (suggested: `agent.trust`), so a caller can see it. It is not a security control by itself.

**Admission.** A `ValidatingAdmissionPolicy` named `agent-trust-rule` (suggested), with a binding in `Deny` and `Audit` modes on the PoC-5 workload namespaces (namespace label `agents.platform/admission: enforce`, suggested). Files under `kind5/admission/`: `policy.yaml`, `binding.yaml`, `params.yaml`, `fixtures/`.

The policy decides the lane from the pod's shape, not from a label the submitter writes. A pod is in the `sidecar` lane when it runs the chassis image (the registry prefix plus `/chassis`) and any other container next to it. A pod with no chassis image and the label `agents.platform/lane: remote` is a remote workload. Rules, each with its own message:

1. Every pod in an enforced namespace carries `agents.platform/trust` with `trusted` or `untrusted`. Missing: rejected.
2. A `sidecar`-lane pod with `agents.platform/trust: untrusted` is rejected.
3. In a `sidecar`-lane pod, every container and init container image starts with the registry prefix from the params (suggested for kind: `agent-platform/`, the local image names; in a cloud, the private registry host). A third-party image is rejected.
4. A `sidecar`-lane pod labeled `trusted` must use a workload image whose repository is in the params list `trustedRepositories`. Otherwise rejected.
5. A remote workload pod must set `runtimeClassName: gvisor`, `automountServiceAccountToken: false`, and no `envFrom` or volume from a Secret other than `remote-<name>-token` (section 2.2). Otherwise rejected.

**Where the trust signal comes from.** The submitter writes the `agents.platform/trust` label, so the label alone must not be able to make code trusted. The policy treats it as a one-way signal:

- `untrusted` is always believed: a submitter may always ask for the stricter lane.
- `trusted` counts only when the platform-owned params agree. The params are a ConfigMap `agent-trust-params` in the namespace `agent-platform-system` (suggested). Only the platform's role may write there. The submitter's role (in kind, a test `ServiceAccount` used through `--as`) has no write access to it. In PoC-5 the params list the repositories CI builds from this repo (`agent-platform/echo-python`, `agent-platform/echo-typescript`, the PoC-6 engines later). `packages/workloads/hostile` is built as `agent-platform/hostile` and is not on the list.
- In a cloud, the same list would come from the registry's own metadata or a signature (H-10, out of scope). The blind-spots note records that in PoC-5 the list is a hand-kept ConfigMap.

So a submitter who labels the probe workload `trusted` and puts it in a sidecar pod is rejected by rule 4. The demo shows exactly that (section 7).

**Why VAP and not Kyverno.** Built into Kubernetes v1.37, no controller, no memory, no image to pin. The rule is narrow and fits in CEL. Kyverno stays the answer for image signatures (H-10) and the minimum-version rule (CH-7), both out of scope. ADR-005 records this.

**Rejected:** a lane label written by the submitter (they could label a sidecar pod `remote`); trust from a pod annotation alone (the submitter controls it); a mutating policy that moves an untrusted workload to the remote lane (admission should refuse, not rewrite a deploy).

**Tests.** Offline, `poc/tests/test_poc05_admission_static.py`: the policy, binding, and params parse; the binding is `Deny`; each fixture in `kind5/admission/fixtures/` names its expected outcome and message in a comment header; the five rules each have a rejected fixture and an admitted twin that differs only in the field the rule checks (the pitfall rule). On kind (`POC05_KIND=1`), `poc/tests/test_poc05_kind_admission.py` applies each fixture with `kubectl apply --dry-run=server --as=system:serviceaccount:poc05-tenant:submitter` and asserts the outcome and the message; it also asserts the submitter cannot write `agent-trust-params`.

### 2.6 `ToolPort`: the idempotency key, the error codes, write mode

**Port change** (`chassis/ports/tool.py`, additive):

```python
async def call(
    self, name: str, arguments: Mapping[str, Any], *, idempotency_key: str | None = None
) -> ToolResult: ...
```

- **Write mode** is `ToolDefinition.read_only = False`. The scope's `mode: write` is that field; `contract-v4.md` says so. Over MCP it is the `readOnlyHint` annotation. A tool with no hint from a real server is treated as a write tool (fail safe: it needs a key).
- **A write tool without a key** raises `ToolError("idempotency_key_required", ...)` before anything is called. Every adapter and the fake do this; the suite checks it.
- **A write tool with a key** has at most one effect per key: a second call with the same key returns the first result. The key reaches the tool server; the tool server keeps the promise (the fake and `code-runner` do; a third-party tool server is listed in the blind-spots note if it does not).

**Error codes.** Today: `unknown_tool`, `bad_arguments`. PoC-5 adds three (suggested names):

| Code | When | `retryable` |
| ---- | ---- | ----------- |
| `idempotency_key_required` | a write tool called without a key | no |
| `tool_denied` | the gateway refused the call for this credential (not on the allow-list, or the key is not valid) | no |
| `tool_unavailable` | the gateway or the tool server did not answer, timed out, or failed with a 5xx | yes |

A tool not on the key's allow-list is not listed. A call to it raises `unknown_tool` or `tool_denied`, whichever the gateway gives (**spike**: record which LiteLLM returns; the suite accepts either and the PoC test pins the observed one). A failure the tool itself reports stays `ToolResult(is_error=True)`, not an exception. Over MCP, the chassis's tool endpoint sends a `ToolError` as an MCP tool error whose structured content is `{"code": ..., "retryable": ...}` and whose text is `public_message(code)`.

**Where the key comes from.** The tool endpoint (`server/tool_endpoint.py`, `PortTool.run`) derives one key per call:

```
key = "tk1:" + sha256(run_key | tool name | canonical JSON of arguments | workload_nonce).hexdigest()[:40]
```

- `run_key` is the client's `Idempotency-Key` hash when the run has one (`key_hash`, PoC-4), else the run's `request_id`. So a replayed run (PoC-4 takeover) sends the same keys again, and the tool server dedups the writes. Two runs without a client key never share a key.
- `workload_nonce` is `_meta.idempotency_key` from the workload's MCP call, when it sends one (suggested, optional). It lets a workload make two intended, identical writes in one run. It is mixed in, never used raw.
- With no run in flight (no `traceparent`, or an unknown one), there is no key, so a write tool is refused with `idempotency_key_required`. Writes happen only inside a run. On the remote listener this case is already 403 `run_required` (section 2.3).

**Fake.** `InMemoryTools` gains `allowed: frozenset[str] | None` (the per-key allow-list in miniature: tools outside it are not listed and calls raise `tool_denied`) and one write tool, `note_write` (suggested), that stores notes by key and returns the first result for a repeated key. `default_tools()` stays read-only, so no PoC-1 to PoC-4 test changes.

**Suite** (`suites/tool.py`, new cases; existing five unchanged): a write tool without a key is `idempotency_key_required`; the same key twice is one effect and the same result; two keys are two effects; a tool outside the allow-list is not listed and its call raises `unknown_tool` or `tool_denied`; optional `make_unavailable` fixture (skipped when a binding does not provide it): the call raises `tool_unavailable` with `retryable=True`. The binding provides `write_call` and `denied_name` fixtures.

**Rejected:** the workload's own key used raw (a workload could reuse another run's key and read its result: the cross-run read that B12 warns about); a separate `WriteToolPort` (one port, one suite, one more keyword is enough); `mode` as a new string field (`read_only` already exists and maps to MCP's hint).

### 2.7 `McpGatewayTools` and `packages/fake-mcp-server`

**Adapter:** `chassis/adapters/mcp/gateway.py`, `McpGatewayTools`, registered as `REGISTRY["tools"]["mcp"]` (a lazy factory, like PoC-4). Built by `from_env()`:

- `LITELLM_MCP_URL` (suggested default `http://litellm:4000/mcp/`) and `LITELLM_API_KEY`, the service's own virtual key (the same variable the model adapter reads; one key per service, section 2.9). The header is `Authorization: Bearer <key>` (**spike**: confirm LiteLLM's MCP gateway reads it there; fallback `x-litellm-api-key`).
- A FastMCP client over streamable HTTP, `trust_env=False`, no redirects. The key is never logged, never in a span, never in `repr`.
- `list_tools()` is sync in the Protocol, so the adapter keeps a cached list. `await refresh()` fills it in the tool endpoint's lifespan before `/mcp` opens, then every `TOOLS_REFRESH_S` (suggested 60) in the background. A failed refresh keeps the last list and counts `chassis.tools.refresh_failed`. The list is whatever the gateway shows this key: the gateway's allow-list is the hard limit (B11), the cache is not a control.
- `call()` sends `_meta.idempotency_key` for a write tool (**spike**: confirm the gateway forwards `_meta`; fallback: also set an `idempotency_key` argument, only when the tool's schema declares that property). Mapping: gateway 401 or 403 to `tool_denied`; not found to `unknown_tool`; connect error, timeout (suggested 30 s), or 5xx to `tool_unavailable`; an MCP tool error to `ToolResult(is_error=True)`.

**`packages/fake-mcp-server`** (new, a test package, may import FastMCP; never imported by `chassis`): an MCP server with three tools, all harmless:

- `glossary_lookup` (read-only; the same data as the chassis fake).
- `note_write` (write; dedups by `_meta.idempotency_key`; refuses a call without one).
- `unlisted_probe` (suggested name): returns a fixed marker string. It is never on any allow-list. If a test ever sees its marker, the allow-list failed. It is the H08 target.

It runs two ways. On kind, behind LiteLLM's MCP gateway as a registered MCP server, with no auth of its own and NetworkPolicy ingress from LiteLLM only. Offline, in-process over an ASGI transport, with a small `--allow TOKEN=tool,tool` option that imitates the gateway's per-key list, so `McpGatewayTools` binds the full `ToolPortContract` with no network. `GET /calls` (offline mode only, suggested) lists the calls it received, for the tests.

**Bindings.** Offline: `ctests/test_tool_gateway_contract.py` (`McpGatewayTools` against the in-process fake MCP server). On kind (`network`, `POC05_KIND=1`): `poc/tests/test_poc05_kind_tool_gateway.py`, the same class against the real gateway with the chassis's virtual key, plus H07 and H08 from the probe workload.

**Rejected:** calling MCP servers directly from the chassis (then the allow-list would live in chassis config, which the pipeline enforces: not a hard limit); a second credential for the gateway (one virtual key carries models, budget, and tools).

### 2.8 The code-execution tool: `packages/code-runner`

**Choice:** a small MCP server, `packages/code-runner` (FastMCP; never imported by `chassis`), with one tool, `run_python` (suggested). It runs in an agent-sandbox `Sandbox` on the `gvisor` RuntimeClass, in its own namespace, behind LiteLLM's MCP gateway. A workload reaches it only as a tool, through the chassis's tool endpoint, so the per-key allow-list applies like every other tool.

- **Input:** `{code: str, timeout_s: int}` (suggested: `timeout_s` 1 to 10, default 5; `code` at most 64 KiB). **Output:** `{stdout, stderr, exit_code, timed_out}`, each stream cut at 64 KiB (suggested).
- **Write mode:** `read_only: false`. Running code is not a pure read, and a retry must not run it twice. The server keeps the last 256 results by `_meta.idempotency_key` (suggested, in memory; a restart forgets them, recorded as a limit).
- **Each call** runs `python -I -S` in a child process with `setrlimit` on CPU time, address space (suggested 256 MiB), open files, and file size, in a fresh directory under the memory-backed `/tmp`, removed after. The child is killed at `timeout_s`. These limits are the tool's own hygiene. The hard limits are the pod's: gVisor, the pod PID limit, the memory limit, the `/tmp` size cap, and a NetworkPolicy with no egress at all (not even DNS).
- **One long-lived sandbox** (suggested), not one per call. agent-sandbox's warm pool and per-session sandboxes are noted for later; one pod is what fits the memory budget.
- **Pinned:** agent-sandbox at the release the spike installs (**spike**: record the version and the CRD group and kind). **Fallback** if the CRD does not fit on kind: a plain `Pod` with `runtimeClassName: gvisor` and the same hardening; the blind-spots note records that agent-sandbox was not used.

**Why it does not make an agent untrusted.** The generated code never runs in the workload's process or pod. The agent stays in its lane (often `sidecar`), and the code runs in the code-runner's sandbox, which holds no credential and has no network. This is ADR-001's reason for routing generated code through a tool.

**Rejected:** E2B (an outside service and its own key, egress from the cluster, no offline test); running code inside the remote workload pod (that would be the remote lane for every coding agent, the outcome the scope wants to avoid); one sandbox per call (memory, and start time under gVisor).

**Tests.** Offline: `packages/code-runner/tests/` (a call returns stdout; a timeout sets `timed_out`; the same key returns the same result without a second run; a call without a key is refused). On kind: `poc/tests/test_poc05_kind_code_runner.py` (the pod's runtime is `runsc` by the gVisor marker check from the threat model's pitfalls; code that opens a socket gets an error, and the same call from a pod the policy allows reaches its target as the paired control; a call through the chassis returns stdout).

### 2.9 Virtual keys and the seed step

**Choice:** one host script, `kind5/platform/seed.sh` (suggested), run by `make kind-poc05` after the platform services start and before the agents deploy. It makes every credential in the cluster. No credential lives in a file in the repo, in a manifest, in a command line, or in a log.

Rules the script follows, each checked by a static test:

- Every value is made with `openssl rand -hex 32` and goes straight into a Secret through a pipe: `... | kubectl create secret generic NAME --from-file=KEY=/dev/stdin --dry-run=client -o yaml | kubectl apply -f -`. A value that must go into two Secrets (the remote token, in two namespaces) is held in a shell variable for those two pipes only, then unset.
- `set -euo pipefail`, never `set -x`, no `curl -v`. No `echo` or `printf` of a credential to the terminal.
- Calls to LiteLLM's admin API go through a `kubectl port-forward` on a random local port. The master key is read from its Secret into a variable and passed to curl as a header on stdin (`curl -H @-`), never as an argument (argv shows in the process list). The new virtual key is taken from the response with `jq -r .key` and piped into its Secret.
- The script is idempotent: an existing Secret is kept, unless `seed.sh rotate NAME` is asked.

**What it makes:**

| Secret | Namespace | Mounted into | Holds |
| ------ | --------- | ------------ | ----- |
| `litellm-master` | `poc05-platform` | LiteLLM only | the master key |
| `litellm-db` | `poc05-platform` | LiteLLM, Postgres | the database password |
| `valkey-auth` | `poc05-platform`, `poc05-agents` | Valkey (as a config file, not argv), each chassis container | the password |
| `minio-root`, `minio-chassis` | `poc05-platform`, `poc05-agents` | MinIO; each chassis container (read-only user) | key pairs |
| `kafka-sasl` | `poc05-platform`, `poc05-agents` | Kafka; each chassis container | SCRAM users (deferred pass, section 2.12) |
| `chassis-<svc>-litellm` | `poc05-agents` | that service's chassis container only | its virtual key |
| `remote-<name>-token` | `poc05-agents`, `poc05-remote` | the chassis container; the remote's workload container | the per-remote token (section 2.2) |

**Virtual keys** (one per chassis instance, `POST /key/generate`): `key_alias` `svc-<name>`; `models` the routes the service uses (suggested: `fake-chat` only); `max_budget` and `budget_duration` (suggested: 1.0 and `1d`, fake spend); `rpm_limit` (suggested 600); `metadata.tags: ["agent:<name>"]` (so the budget binds to the key, not a tag the caller sends: H31); and the MCP permission for the tools on the service's allow-list. **Spike:** the field name for per-key MCP tool permissions differs across LiteLLM releases; pin LiteLLM and record the field. Fallback: one MCP access group per allow-list, named on the key.

Allow-lists (suggested): `echo` gets `glossary_lookup`, `note_write`, `run_python`. Each probe service gets `glossary_lookup` and `note_write` (its positive controls), never `unlisted_probe` or `run_python`.

**Tests.** Offline, `poc/tests/test_poc05_seed_static.py`: the rules above as greps over `seed.sh` and every file under `kind5/` (no `kind: Secret` with `data` or `stringData`, no `set -x`, no credential-like literal), plus `deploy/compose/litellm` and the new kind LiteLLM config: a master key is required and `allow_all_keys` is absent or false (H05, H07 offline). On kind: the test reads each Secret's value in-process, never prints it, and asserts it appears in no pod log, no event, and no span the fake collector stored.

### 2.10 NetworkPolicies, pod by pod

**Namespaces** (suggested): `poc05-platform` (LiteLLM, Postgres, the fake model and MCP servers, Valkey, MinIO, Kafka), `poc05-agents` (every chassis pod), `poc05-remote` (remote workload sandboxes), `poc05-tools` (the code-runner sandbox), `agent-platform-system` (the trust params), `poc05-tenant` (the submitter's service account, no pods). Each workload namespace has a `default-deny` policy for ingress and egress, so anything not in the table is refused.

Selectors are pod labels plus namespace labels (`kubernetes.io/metadata.name`). **No egress rule anywhere uses an `ipBlock`.** So no rule can allow the metadata service (169.254.169.254), the Kubernetes API Service, or a node address on 6443. A static test asserts this.

| Pod (namespace) | Ingress allowed | Egress allowed |
| --------------- | --------------- | -------------- |
| `agent-echo` (agents): chassis + `echo-python`, `sidecar` lane | 8080 (public) from any source | kube-dns 53; LiteLLM 4000; Valkey 6379; MinIO 9000 |
| `agent-probe-sidecar` (agents): chassis + probe workload, `sidecar` lane | 8080 from any source | the same as `agent-echo`: the probe workload rides the chassis's egress (shared network namespace), so B1 must hold by auth (H05, H06, H07, H10, H12) |
| `chassis-probe-remote` and `chassis-echo-remote` (agents): chassis alone, `remote` lane | 8080 from any source; **8091 only from its own remote's pod label** in `poc05-remote` | kube-dns 53; LiteLLM 4000; Valkey 6379; MinIO 9000; its remote pod's A2A port 9000 |
| `remote-probe`, `remote-echo` (remote): the workload, gVisor | 9000 only from its own chassis pod's label (H18) | its chassis's port 8091 only. **No DNS**: the chassis's remote Service has a fixed ClusterIP, given to the pod as plain env |
| `code-runner` (tools), gVisor | its MCP port only from LiteLLM | none |
| `litellm` (platform) | 4000 from pods labeled `agents.platform/role: chassis` in `poc05-agents` | kube-dns 53; Postgres 5432; fake model server 8081; fake MCP server; code-runner |
| `postgres` (platform) | 5432 from LiteLLM | none |
| `fake-model-server` (platform) | 8081 **from LiteLLM only** (the H09 fix) | none |
| `fake-mcp-server` (platform) | its port from LiteLLM only | none |
| `valkey`, `minio` (platform) | their port from chassis pods | none |
| `kafka` (platform, deferred pass) | 9093 SASL from chassis pods | none (single-node KRaft, controller on loopback) |

Notes:

- The policy for an agent pod cannot tell its two containers apart (they share the network namespace). That is why the `sidecar` lane depends on the services' own auth, and why untrusted code goes remote. The table says so; the guide says so.
- Tests reach a chassis's public port with `kubectl port-forward`, which enters the pod through the kubelet and is not subject to NetworkPolicy. The 8080 rule matters for in-cluster callers only.
- The remote pod's egress allows one pod on one port. A remote that tries DNS gets no answer at all (H20 is closed for the remote, not just narrowed). The sidecar lane keeps kube-dns, so H20 stays a recorded blind spot there.
- **Named deny (spike).** kind's default CNI, kindnet, ships `kube-network-policies` (the spike's `q2-netpol.yaml` tests it). If the spike shows it also enforces `AdminNetworkPolicy`, add one cluster-wide ANP, `deny-metadata-and-api`, that denies egress to 169.254.0.0/16, the API Service IP, and the node addresses on 6443 for every PoC-5 namespace. It is evaluated before namespace policies, so it names the deny list in an object, as the scope asks. **Fallback:** the static no-`ipBlock` test plus a comment header in each `default-deny.yaml` that names the three destinations. If kindnet does not enforce egress at all, switch to Calico (`disableDefaultCNI: true`), recorded in ADR-005.

**Tests.** Offline, `poc/tests/test_poc05_netpol_static.py`: parse every policy under `kind5/`; every PoC-5 namespace has a default deny for both directions; no `ipBlock`; the allowed edges equal this table (a fixture holds the table as data); 8091 is reachable only from the remote's label; the remote's egress is exactly one port. On kind, the probe suites test the packets (H01, H03, H04, H18, H19, H20, H30), each with its paired allowed control (section 5).

### 2.11 Container hardening

Every PoC-5 namespace carries Pod Security Admission `enforce: restricted` (and `audit`, `warn`). Every pod: `automountServiceAccountToken: false`, its own `ServiceAccount` with no RBAC bindings, no `shareProcessNamespace`, no `hostNetwork`, `hostPID`, or `hostIPC`, no projected token volume. Every container: `runAsNonRoot: true`, `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, `seccompProfile: RuntimeDefault`. The PID limit is the kubelet's `podPidsLimit` (suggested 256, as in the spike's `cluster.yaml`), which applies to every pod on the node.

| Container | uid:gid (suggested) | runtime | CPU limit | Memory limit | `/tmp` (memory-backed, `sizeLimit`) | Secrets it gets |
| --------- | ------------------- | ------- | --------- | ------------ | ----------------------------------- | --------------- |
| chassis (every agent pod) | 10001:10001 | runc | 500m | 256Mi | 16Mi | its virtual key, Valkey, MinIO, (Kafka), and in the `remote` lane its remote's token |
| sidecar workload (`echo-python`, probe) | 10002:10002 | runc | 500m | 256Mi | 32Mi | none |
| remote workload (`remote-*`) | 10002:10002 | `gvisor` | 500m | 256Mi | 32Mi | only `CHASSIS_API_TOKEN` (section 2.2) |
| `code-runner` | 10003:10003 | `gvisor` | 500m | 256Mi | 64Mi | none |
| platform services | each image's non-root uid | runc | 500m to 1 | section 8 | as needed | their own only |

- Two uids in one pod mean the workload cannot read the chassis's files even if a volume were shared by mistake (H26 has the manifest check too).
- `/tmp` is an `emptyDir` with `medium: Memory` and a `sizeLimit`, mounted per container, never shared. A write over the cap fails at the cap (H22); the memory counts against the container's limit, so the cap is small.
- The workload image for the probe is the same Python base as `echo-python`, plus nothing: the checks use the Python standard library (the threat model's "image lacks the tool" pitfall). The image is built from `packages/workloads/hostile/Dockerfile`, tagged `agent-platform/hostile:poc05`, and is not on the trusted list.
- **PID limit under gVisor (spike).** In a `runsc` pod, the sandbox's own process table may not count against the host cgroup's `pids.max` the same way. The spike checks whether H23 stops at the cap under gVisor. **Fallback:** if it does not, the H23 result records which control stopped the fork bomb (the memory limit), and the exceptions list in the gVisor note says so. The test never claims the PID cap held when another control did.

**Rejected:** a custom seccomp profile (RuntimeDefault plus gVisor is enough for PoC-5 and a custom profile needs per-engine tuning); a Pod-level `pids` field (Kubernetes has none; the kubelet setting is the supported control).

### 2.12 Hard requirement 1 on kind, service by service

"Refuses" means: the probe workload attempts the call without a credential (or with a guessed one) and gets an auth error; the chassis makes the same call with its credential and succeeds, in the same test run (pitfall: "service down vs auth refusing").

| Service | How it refuses | Probe check (refused) | Paired control (allowed) | PoC-5 status |
| ------- | -------------- | --------------------- | ------------------------ | ------------ |
| LiteLLM (models) | master key set; virtual keys in Postgres; no open mode | H05 401 with no key; H06 401 with a guessed key | the chassis's model call with its virtual key: 200 | **fix** |
| MCP gateway (same LiteLLM process) | per-key tool permission; `allow_all_keys` removed | H07 no tools or 401 with no key; H08 `unlisted_probe` not listed and refused | the chassis lists and calls `glossary_lookup`: 200 | **fix** |
| Valkey | `requirepass` from a mounted config file, not argv; ACL user `chassis` (suggested) | H10 `NOAUTH` with no login, `WRONGPASS` with a guessed one | the chassis's `ValkeyState` round trip | **fix** (adds the config-file move) |
| MinIO (config store) | per-service key pair; the chassis user is read-only on its bucket | H12 403 with a guessed key pair | the chassis reads its config document | **fix** (new on kind) |
| Postgres (LiteLLM's) | password; ingress from LiteLLM only | covered by H30 (no route from an agent pod) | LiteLLM reaches it (keys work) | **fix** |
| Kafka (broker) | SASL SCRAM, per-principal ACLs, no PLAINTEXT listener | H11 refused without SASL credentials | the chassis publishes a result event | **deferred pass** |
| fake model server | no key; ingress from LiteLLM only | H09 no route from an agent pod | LiteLLM's route to it works (H05's control) | **fix** by network |

**Decisions on the open items:**

- **H09, the fake model server reachable directly.** Fixed in PoC-5 on kind. The chassis on kind talks to LiteLLM, never to the fake model server, and the fake model server's ingress allows LiteLLM's label only. No key is added to the fake model server: LiteLLM holds the upstream credential, which is the production shape. The Compose `local` variant (`llama-cpp` with no key) stays an **exception**, owner `platform-security`, recorded in `deploy/compose/SECURITY.md`: Compose is never the boundary for untrusted code, and the PoC-5 README says so.
- **H14, the proxy port bind order.** Fixed in PoC-5. `chassis serve` binds the loopback proxy listener first, before it waits for the workload and before the public listener, and exits non-zero with a clear message if the port is taken (never runs without its proxy). Waiting for the workload moves to readiness (PoC-4's `workload_unreachable`), not to the bind. On kind, the chassis is the native sidecar (an init container with `restartPolicy: Always`) with a `startupProbe` on `/health`, so it is up before the workload container starts. The Compose card-wait entrypoints (`docker-compose.scale.yaml` and `docker-compose.sidecar.yaml`, up to 120 s before binding) are removed too. Probe check: the workload attempts to bind `127.0.0.1:8090` and gets `EADDRINUSE`; paired control: it binds a free loopback port.
- **H16, uncorrelated spend.** Fixed in PoC-5, in two layers. The hard cap is the virtual key's budget and rate limit in LiteLLM (B11), which holds even if the chassis is bypassed. The chassis adds a per-replica cap on the loopback model proxy: `spec.limits.uncorrelated_tokens_per_minute` (suggested 20000; `0` refuses every uncorrelated call), a token bucket, 429 `budget_exhausted` past it, counted. On the remote listener, uncorrelated calls are already 403 `run_required`. Additive; recorded in `contract-v4.md`.
- **Kafka with SASL.** A **separate pass**, task T24 in the last wave, run with the agent pods scaled to zero for memory (section 8). PoC-5's agent configs keep `events: none`, so no other suite needs Kafka. If the pass does not fit in memory, H11 is recorded as an **exception**, owner `platform-security`, closing in [020 X-8](../planning/issues/020-X-8-event-broker.md); the offline static check (no PLAINTEXT listener in the kind manifest, SASL users from a Secret) still runs.
- **Valkey password on argv.** Fixed: a mounted `valkey.conf` from the Secret (CH-3 debt paid for kind).
- **The MinIO image.** PoC-5 uses the image already pinned by digest for Compose, with fake data only. **Exception**, owner `platform-security`: build from source and sign before any real data (SECURITY.md section 7).
- **B12, per-caller scope of cached results.** Not closed. It is PoC-8 work; the blind-spots note says so and no test claims it.

## 3. Contract and schema changes

`docs/contracts/contract-v4.md` records PoC-5's changes. Every one is additive. Nothing a PoC-1 to PoC-4 workload or caller sends or receives changes.

**Wire contract (envelope, events, `handle`, A2A mapping): no change.** `schema_version` stays `"0"`. The `remote` lane carries the same A2A messages as `sidecar`; the only difference is an `Authorization: Bearer` header on the transport, which the mapping never sees. `mapping.py` stays byte-for-byte equal in `chassis` and `workload-a2a`.

**Config (`spec.*`)**, in `chassis/server/config.py` and `packages/chassis/schemas/chassis-config.v0.json` (regenerated by `make schemas`; the schema stays v0 because every field is optional or has a default outside `cloud`, as in PoC-4):

| Field | Type, default (suggested) | Rule |
| ----- | ------------------------- | ---- |
| `spec.engine.connector` | adds `remote` | |
| `spec.engine.url` | for `remote`: `http(s)://host:port[/path]` | no user info, query, or fragment; `https` in `cloud` |
| `spec.engine.auth` | `{scheme: bearer, token_env, previous_token_env: null}` | required for `remote`; refused for `sidecar` and `inprocess` |
| `spec.engine.probe_timeout_s` | float, 2.0 | > 0 |
| `spec.engine.uds` | as today | refused in `cloud` for `remote` |
| `spec.trust` | `trusted` (`fake`, `local`); required in `cloud` | `untrusted` refused unless `connector: remote` |
| `spec.limits.uncorrelated_tokens_per_minute` | int, 20000 | ≥ 0; `0` refuses every uncorrelated model call. Reloadable |
| `spec.adapters.tools` | adds `mcp` (`McpGatewayTools`) | `cloud` refuses `memory` |

`spec.trust`, `spec.engine.*` are restart-only. Only the new limit joins `RELOADABLE`.

**Ports.** `ToolPort.call` gains the keyword `idempotency_key: str | None = None`. `ToolError` codes add `idempotency_key_required`, `tool_denied`, `tool_unavailable` (section 2.6). `ToolDefinition` is unchanged; `read_only: false` is write mode. No other port changes. `EngineConnector` is unchanged: `RemoteConnector` implements it.

**Proxies.** On the remote listener only: 401 `remote_unauthenticated`, 403 `run_required`, 404 for everything not in its route list. On the loopback model proxy: 429 `budget_exhausted` past the uncorrelated cap. On the tool endpoint (both listeners): a `ToolError` is an MCP tool error with structured content `{code, retryable}`.

**Manifest.** `agent.trust` added to `GET /manifest` (manifest v0, optional field).

**CLI.** `chassis serve`: `--remote-proxy-host`, `--remote-proxy-port` (8091), and the bind order (proxy first, exit non-zero if taken). `workload-a2a serve`: `--require-token-env`; a non-loopback `--host` needs it.

**Cost per lane:**

- `inprocess`: none. `spec.trust: untrusted` is refused here.
- `sidecar`: the bind-order change and the uncorrelated cap. No new hop, no new credential. Tests from PoC-2 to PoC-4 keep passing unchanged, except those that start a workload before the chassis binds 8090 (none known; the wave-1 task checks).
- `remote`: one network hop each way (A2A to the remote, model and tool calls back to 8091), one bearer check per request (`hmac.compare_digest`, microseconds), gVisor's overhead (measured, criterion 9), and one Secret per remote.

**Rejected:** a new `schema_version` (nothing on the wire changes); putting the token in `ctx` or in A2A metadata (it would cross into `handle` and into logs; a transport header is never seen by the mapping).

## 4. Exit criteria to tests

Tiers: **offline** runs in `make test` and `make check` (sockets off, no keys). **kind** is marked `network` and skipped unless `POC05_KIND=1` and the `kind-poc05` context exists; it runs in `make kind-poc05 ARGS=test` and in the remote-lane CI job. Every file is under `pocs/poc-05-sandboxed/tests/` unless a path says otherwise, and every basename is unique in the repo.

| # | Exit criterion | Tests (tier) | H ids |
| - | -------------- | ------------ | ----- |
| 1 | Hostile suites in CI; the no-cluster parts offline on every commit | `test_poc05_hostile_offline.py` (offline), `test_poc05_netpol_static.py`, `test_poc05_hardening_static.py`, `test_poc05_admission_static.py`, `test_poc05_seed_static.py` (offline), `test_poc05_ci_wiring.py` (offline: the workflow runs both kind suites on push; `make check` collects the offline files) | all, via the rows below |
| 2 | A fake workload through `remote` on every commit, same contract suite as `sidecar` | `test_poc05_remote_lane_contract.py` (offline: `LaneContract` over `inprocess`, `sidecar`, and `remote`, the last two on Unix sockets, the remote with the token on); `test_poc05_kind_remote_lane.py` (kind: the same requests to `chassis-echo-remote`, events compared with the `sidecar` pod's) | H17 (control) |
| 3 | Every internal service refuses without the chassis's credential; through the chassis the same call works | `test_poc05_kind_hardreq1.py` (kind), `test_poc05_seed_static.py` (offline) | H05, H06, H07, H09, H10, H11, H12 |
| 4 | No provider key or internal credential in a workload or remote pod; no service account token | `test_poc05_hardening_static.py` (offline); `test_poc05_kind_hostile_sidecar.py`, `test_poc05_kind_hostile_remote.py` (kind) | H02, H25, H26 |
| 5 | Metadata service, Kubernetes API, and the public port on localhost unreachable from the workload | `test_poc05_netpol_static.py`, `test_poc05_hostile_offline.py` (offline: the public bind argument); `test_poc05_kind_hostile_sidecar.py` (kind) | H01, H03, H04, H13 |
| 6 | The remote reaches only the proxies, only with its own credential | `ctests/test_remote_auth.py`, `test_poc05_hostile_offline.py` (offline); `test_poc05_kind_hostile_remote.py` (kind) | H17, H18, H19, H20, H30 |
| 7 | Admission rejects `untrusted` and a third-party image in `sidecar` | `test_poc05_trust_config.py`, `test_poc05_admission_static.py` (offline); `test_poc05_kind_admission.py` (kind) | — |
| 8 | Hostile suites pass in both lanes | `test_poc05_kind_hostile_sidecar.py`, `test_poc05_kind_hostile_remote.py` (kind), `test_poc05_hostile_offline.py` (offline) | H01 to H30 by lane (section 5); H31 and H32 documented |
| 9 | gVisor works for every remote engine (or exceptions listed); latency and memory overhead measured | `test_poc05_kind_gvisor.py` (kind: each engine's card and one run under `runsc`, then the same under `runc`); `test_poc05_records.py` (offline: the note exists, has the command, the figures, and an exceptions list) | H28 |
| 10 | What the chassis cannot see in `remote` is listed | `test_poc05_records.py` (offline: the blind-spots note covers each item of the threat model's section 5) | — |

**The offline part of criterion 1** is everything that does not need a packet or a syscall on a real node: the static checks of every manifest under `kind5/` (NetworkPolicy edges and no `ipBlock`, hardening fields, admission rules with paired fixtures, the seed script's rules, LiteLLM configs), the remote listener's auth and run checks, the trust rule in config, and the probe workload driven in-process through the remote lane over a Unix socket against the fake stack (H08, H13, H15, H16, H17, H29). The kind tier proves the same ids with real packets and syscalls.

## 5. Hostile suites: how the probe workload is driven

**The probe workload** is `packages/workloads/hostile`, a Python workload on the `workload-a2a` template, image `agent-platform/hostile:poc05`. It runs a fixed set of checks, one per threat-model id, and nothing else. It never runs code or targets taken from its input: the input only names a check id, and the targets (literal IP addresses and ports) come from env the manifest sets, filled by `run.sh` at deploy time. So a DNS failure can never pass for a policy refusal (pitfall 1), and the checks use only the Python standard library (pitfall 2).

**Driving it.** The test sends a normal request to the chassis's public port (`POST /v1/run`, through `kubectl port-forward` on kind, through the in-process app offline) with input `{"check": "H19"}`. The chassis forwards it over A2A like any request. The probe workload runs the check and ends the run with one `end` event whose output is the result below. One check per run keeps each result tied to one trace id.

**Result** (`probe-result`, suggested schema `packages/workloads/hostile/schemas/probe-result.v1.json`, checked by the test):

```json
{
  "check": "H19",
  "lane": "remote",
  "runtime": "runsc",
  "attempt": {"action": "connect to the outside test address", "outcome": "refused", "detail": "timeout"},
  "control": {"action": "connect to the chassis remote listener", "outcome": "allowed", "detail": "401 without token"},
  "control_kind": "in_workload",
  "elapsed_ms": 3012
}
```

- `outcome` is `refused`, `allowed`, or `error` (the check itself failed; a test treats `error` as a failure, never as a pass).
- `detail` is a short class (`timeout`, `ECONNREFUSED`, `EROFS`, `EAGAIN`, `ENOSPC`, `ENOENT`, `401`, `403`, `OOMKilled`, `EADDRINUSE`), never response bodies or file contents.
- `runtime` is read from a gVisor marker (pitfall "gVisor running but on runc").
- `control_kind` is `in_workload` (the same mechanism against an allowed target, from the same container) or `via_chassis` (the test makes the same call through the chassis with its credential in the same test function; the probe returns `control: null`).

**Every test asserts both halves.** `attempt.outcome == "refused"` with the expected `detail`, AND the control is `allowed`. If the control fails, the test fails with "control failed: the refusal proves nothing" (threat model section 6).

**The checks**, as "the probe workload attempts X; the control refuses with Y", paired with the allowed control:

| id | Lane | The probe workload attempts | Refused with | Paired allowed control |
| -- | ---- | --------------------------- | ------------ | ---------------------- |
| H01 | S, R | to reach the metadata address | timeout (no route) | S: reach Valkey's pod IP; R: reach the chassis's 8091 |
| H02, H25 | S, R | to find a service account token or any secret in env or mounts | `ENOENT`, nothing found | reads its own non-secret env (`CHASSIS_API_TOKEN` present only in R, by design) |
| H03, H04 | S, R | to reach the API Service IP and a node address on 6443 | timeout | as H01 |
| H05, H06 | S | a model call to LiteLLM with no key, then a guessed key | 401 | via chassis: the same model call, 200 |
| H07, H08 | S, R | to list and call tools without a key; to call `unlisted_probe` through the chassis | 401 or no tools; `unknown_tool` or `tool_denied` | via chassis: `glossary_lookup`, 200 |
| H09 | S | to reach the fake model server directly | timeout | via chassis: a model call through LiteLLM |
| H10, H12 | S | Valkey and MinIO without, then with guessed credentials | `NOAUTH`/`WRONGPASS`; 403 | via chassis: a state round trip; a config read |
| H11 | S | Kafka without SASL credentials (deferred pass) | refused | via chassis: a result event published |
| H13 | S | to connect the public port on `127.0.0.1` | `ECONNREFUSED` | connects the proxy on `127.0.0.1:8090` |
| H14 | S | to bind `127.0.0.1:8090` | `EADDRINUSE` | binds a free loopback port |
| H15, H16 | S | model calls with no `traceparent` past the cap | served, then 429 `budget_exhausted` | a call inside a run, 200 |
| H17 | R | the proxies on 8091 with no token, then a wrong one | 401 `remote_unauthenticated` | the same call with its own token inside the run, 200 |
| H18, H30 | R, S | a test pod (not the chassis) attempts the remote's A2A port; the workload attempts another agent pod | timeout | the chassis reaches the remote (every run proves it) |
| H19, H20 | R | an outside address; a name lookup | timeout; no resolver reached | reaches 8091 by its fixed IP |
| H21, H22 | R, S | to write in its app directory; to write past the `/tmp` cap | `EROFS`; `ENOSPC` at the cap | a small write in `/tmp` succeeds |
| H23 | R, S | to start processes up to a ceiling (bounded by the check, then reaped) | `EAGAIN` at the PID cap | a few processes start |
| H24 | R | to allocate past the memory limit in a child process | the child `OOMKilled`, the workload answers | a small allocation succeeds |
| H26 | S | to read the chassis's process environment or files | `ENOENT`/`EACCES` | reads its own |
| H27 | R, S | to gain privileges | refused (`EPERM`) | its own uid unchanged |
| H28 | R | to confirm the runtime | `runtime == "runsc"` | the sidecar pod reports `runc` |
| H29 | R | a model route outside its key, then past the run's budget | refused; 429 | an in-scope call, 200 |

H31 (tag spoofing) and H32 (managed-runtime key) are documented in the blind-spots note, not run: H31's budget binds to the key (section 2.9), H32 is PoC-6b.

**Where results are logged** ("blocked and logged" in the demo):

1. The probe workload writes one JSON line per check to stdout (`probe.check`, the id, the outcome, the trace id); `kubectl logs` shows it.
2. The chassis logs the run and its span carries the trace id; the remote listener logs every 401 and 403 (`chassis.remote.auth_failed`, no header values).
3. The services log their own refusals (LiteLLM 401s, Valkey auth failures).
4. The test writes the results to `pocs/poc-05-sandboxed/notes/hostile/<date>-<lane>.jsonl`, and the demo prints them as a table. NetworkPolicy drops are not logged by kindnet (a blind spot; the probe's `timeout` result is the record).

## 6. Work breakdown

Rules for every task: read the folder's `CLAUDE.md` first; touch only the files listed as yours; create new files rather than edit a shared one; start from the failing test named; finish with the done check and `make quick`; paste the commands and their output. A file not listed belongs to nobody in PoC-5: ask the orchestrator. No task commits. Test basenames are unique in the repo (pytest runs without per-folder packages): PoC tests start with `test_poc05_`, and tests in the new packages start with the package name (`test_fake_mcp_*`, `test_code_runner_*`, `test_hostile_*`). `pyproject.toml` files, `uv.lock`, the `Makefile`, `chassis/profiles.py`, and `chassis/server/config.py` are T01's only.

**Docker and kind:** this VM has 7.75 GiB. Only one task at a time may run Docker or the kind cluster (column "kind" = y). The orchestrator runs kind tasks one after another, in the order given. Every other task is offline and may run in parallel within its wave. At the end of each wave, `reviewer` and `platform-security` review the wave's diff (read-only) before the next wave starts.

Short paths as in section 1, plus `wa2a/` = `packages/workload-a2a/src/workload_a2a/`.

### Wave 0: the seams (alone, then the ADR in parallel)

| Task | Owner | Owns | Starts from (failing) | Done check | Needs | kind |
| ---- | ----- | ---- | --------------------- | ---------- | ----- | ---- |
| **T01 Seams** | `developer` | `chassis/profiles.py` (lazy `REGISTRY["engine"]["remote"]` → `adapters.a2a.remote`, `REGISTRY["tools"]["mcp"]` → `adapters.mcp.gateway`, each `AdapterNotAvailable("... arrives in PoC-5")` until its module lands); `chassis/server/config.py` (`Spec.trust`, `EngineAuthSpec`, `spec.engine` fields, `LimitsSpec.uncorrelated_tokens_per_minute`, the `cloud` and trust rules, `RELOADABLE`); `chassis/ports/tool.py` (keyword, codes in the docstring, `NoTools.call` signature); every `pyproject.toml` (new workspace members `packages/fake-mcp-server`, `packages/code-runner`, `packages/workloads/hostile`; import-linter: none of them, nor `workload_a2a`, may import `chassis`, and `chassis` imports none of them); `uv.lock`; `Makefile` (`kind-poc05`, `.PHONY`, `help`); `packages/chassis/schemas/chassis-config.v0.json` (`make schemas`); `poc/tests/conftest.py` (skips kind tests unless `POC05_KIND=1`); `poc/tests/test_poc05_trust_config.py`; empty package skeletons for the three new packages | `test_poc05_trust_config.py`: `untrusted` + `sidecar` refused, `cloud` without `trust` refused, defaults load | `make check`; `uv run pytest pocs/poc-05-sandboxed/tests/test_poc05_trust_config.py -q` | — | n |
| **T02 ADR-005 Proposed** | `docs-editor` (content from section 9) | `docs/planning/adr/005-<slug>.md`; links in `docs/planning/poc/000-plan.md`, `docs/planning/poc/005-PoC-5-sandboxed.md` (Links only), `docs/planning/tools/plan-template.md` | `make planning-check` after adding the link (fails until the file exists) | `make planning-sync planning-check` | — | n |

### Wave 1: chassis, template, and packages in parallel; the cluster base

| Task | Owner | Owns | Starts from (failing) | Done check | Needs | kind |
| ---- | ----- | ---- | --------------------- | ---------- | ----- | ---- |
| **T03 `RemoteConnector`** | `developer` | `chassis/adapters/a2a/remote.py`, `ctests/test_remote_connector.py` | the card URL is rewritten to the configured URL; the token is on the card fetch, message, cancel, and probe; missing env var names the variable; the token is absent from logs and spans (over a Unix socket to an in-process template server) | `uv run pytest packages/chassis/tests/test_remote_connector.py -q` | T01 | n |
| **T04 Remote listener and bind order** | `developer` | `chassis/server/remote_auth.py`, `chassis/server/cli.py`, `chassis/server/lifecycle.py` (a third server in `Drain`), `ctests/test_remote_auth.py`, `ctests/test_server_cli.py` (new cases only), the card-wait entrypoints in `deploy/compose/docker-compose.scale.yaml` and `docker-compose.sidecar.yaml` (H14: drop the wait; readiness covers it) | `test_remote_auth.py` cases in section 2.3; `chassis serve` exits non-zero when 8090 is taken; the proxy binds before the card wait | `uv run pytest packages/chassis/tests/test_remote_auth.py packages/chassis/tests/test_server_cli.py packages/chassis/tests/test_shutdown.py -q` | T01 | n |
| **T05 Template requires the token** | `developer` | `wa2a/auth.py`, `wa2a/cli.py`, `packages/workload-a2a/tests/test_workload_a2a_auth.py` | the cases in section 2.4 | `uv run pytest packages/workload-a2a -q`; `make lint` (no `chassis` import) | T01 | n |
| **T06 `ToolPort` write mode** | `developer` | `chassis/fakes/tool.py`, `suites/tool.py`, `chassis/server/tool_endpoint.py`, `chassis/adapters/mcp/server.py`, `ctests/test_tool_idempotency.py` | the new suite cases bound to `InMemoryTools`; key derivation; a write with no run is `idempotency_key_required` | `uv run pytest packages/chassis/tests/test_contracts.py packages/chassis/tests/test_tool_idempotency.py -q` | T01 | n |
| **T07 Uncorrelated cap** | `developer` | `chassis/server/model_proxy.py`, `ctests/test_uncorrelated_cap.py` | past the cap is 429 `budget_exhausted`; `0` refuses; the cap reloads | `uv run pytest packages/chassis/tests/test_uncorrelated_cap.py -q` | T01 | n |
| **T08 `fake-mcp-server`** | `developer` | `packages/fake-mcp-server/**` | three tools; `note_write` dedups by key; `--allow` hides and refuses `unlisted_probe` (in-process tests) | `uv run pytest packages/fake-mcp-server -q` | T01 | n |
| **T09 `code-runner`** | `developer` | `packages/code-runner/**` (with `Dockerfile`) | the cases in section 2.8 | `uv run pytest packages/code-runner -q` | T01 | n |
| **T10 Probe workload** | `developer` | `packages/workloads/hostile/**` (checks, the `probe-result.v1` schema, `Dockerfile`, tests) | each check id returns a schema-valid result; unknown id is a `bad_input` error; targets come only from env; the bounded checks clean up (tests use temp dirs and stubs, no sockets) | `uv run pytest packages/workloads/hostile -q` | T01 | n |
| **T11 Cluster base** | `developer`, `platform-security` review | `kind5/cluster.yaml`, `kind5/install-gvisor.sh` (from the spike files), `kind5/run.sh` (`create`, `delete`, `status`, `build`, `load`; context pinned to `kind-poc05`), `kind5/base/` (RuntimeClass `gvisor`, the namespaces with PSA labels, the `default-deny` policies, the ANP if the spike allows), agent-sandbox install pinned | a `runsc` pod reports the gVisor marker; a `runc` pod does not; PSA rejects a root pod | `make kind-poc05 ARGS="create"` then `make kind-poc05 ARGS="smoke"`; `make kind-poc05 ARGS="delete"` | T01, spike | **y** |

### Wave 2: manifests (offline, checked by static tests), the gateway adapter, the offline suites

Manifest tasks write YAML and their static test only. Nothing is applied in wave 2; T19 brings it all up in wave 3.

| Task | Owner | Owns | Starts from (failing) | Done check | Needs | kind |
| ---- | ----- | ---- | --------------------- | ---------- | ----- | ---- |
| **T12 Platform services and the seed** | `developer`, `platform-security` review | `kind5/platform/` (LiteLLM with its config, Postgres, fake model server, fake MCP server, Valkey with a mounted `valkey.conf`, MinIO; their policies), `kind5/platform/seed.sh`, `deploy/compose/litellm/config.yaml` (`allow_all_keys` removed, master key required in the non-`fake` variants), `poc/tests/test_poc05_seed_static.py` | section 2.9 rules; LiteLLM configs need a master key; no Secret with data in the repo | `uv run pytest pocs/poc-05-sandboxed/tests/test_poc05_seed_static.py -q` | T08, T11 | n |
| **T13 Agent pods** | `developer` | `kind5/agents/` (`agent-echo`, `agent-probe-sidecar`, `chassis-echo-remote`, `chassis-probe-remote`, chassis configs, Services incl. the fixed-ClusterIP remote Services, policies), `poc/tests/test_poc05_hardening_static.py` | section 2.11 table; the chassis is the native sidecar with a `startupProbe`; secrets only in the chassis container; distinct uids | `uv run pytest pocs/poc-05-sandboxed/tests/test_poc05_hardening_static.py -q` | T03, T04, T10, T11 | n |
| **T14 Remote sandboxes** | `developer`, `platform-security` review | `kind5/remote/` (`Sandbox` for `remote-echo` and `remote-probe`, `gvisor`, the one token env var, policies; the plain-Pod fallback beside it) | extends `test_poc05_hardening_static.py` through a fixture file it owns, `poc/tests/fixtures/remote_expect.yaml` | the same test | T05, T10, T11 | n |
| **T15 Code-runner sandbox** | `developer` | `kind5/tools/` (its `Sandbox`, no-egress policy) | extends through `poc/tests/fixtures/tools_expect.yaml` | the same test | T09, T11 | n |
| **T16 Admission** | `developer`, `platform-security` review | `kind5/admission/**` (policy, binding, params, submitter RBAC, fixtures), `poc/tests/test_poc05_admission_static.py`, `poc/tests/test_poc05_kind_admission.py` (written now, run in T21) | section 2.5: five rules, each with a rejected fixture and an admitted twin | `uv run pytest pocs/poc-05-sandboxed/tests/test_poc05_admission_static.py -q` | T11 | n |
| **T17 `McpGatewayTools`** | `developer` | `chassis/adapters/mcp/gateway.py`, `ctests/test_tool_gateway_contract.py`, `poc/tests/test_poc05_kind_tool_gateway.py` (written now, run in T21) | `ToolPortContract` bound to the adapter over the in-process fake MCP server; the key never in logs | `uv run pytest packages/chassis/tests/test_tool_gateway_contract.py -q` | T06, T08 | n |
| **T18 Offline suites** | `tester` | `poc/tests/test_poc05_hostile_offline.py`, `poc/tests/test_poc05_remote_lane_contract.py`, `poc/tests/poc05_harness.py` (the chassis app with `RemoteConnector` over a Unix socket to the probe workload served with the token) | criterion 1 offline and criterion 2 offline (section 4) | `make test-poc POC=05` | T03 to T07, T10 | n |

### Wave 3: on the cluster, one kind task at a time (T19, T21, T22, T23 in that order)

| Task | Owner | Owns | Starts from (failing) | Done check | Needs | kind |
| ---- | ----- | ---- | --------------------- | ---------- | ----- | ---- |
| **T19 Bring-up** | `developer` | `kind5/run.sh` (`up`, `seed`, `apply`, `test`, in the order of section 8), every file under `kind5/` for fixes found on apply | every pod Ready; a normal request answers through `agent-echo` and `chassis-echo-remote` | `make kind-poc05 ARGS="up"` then `make kind-poc05 ARGS="status"`; `make test-poc POC=05` still green | T11 to T17 | **y** |
| **T20 NetworkPolicy static test** | `tester` | `poc/tests/test_poc05_netpol_static.py`, `poc/tests/fixtures/netpol_edges.yaml` (section 2.10 as data) | every namespace default-denies; no `ipBlock`; edges equal the table | `uv run pytest pocs/poc-05-sandboxed/tests/test_poc05_netpol_static.py -q` | T19 | n |
| **T21 Sidecar suite on kind** | `tester` | `poc/tests/test_poc05_kind_hostile_sidecar.py`, `poc/tests/test_poc05_kind_hardreq1.py`; runs T16's and T17's kind files | criteria 3, 4, 5, 7 on kind; each refusal with its control | `POC05_KIND=1 uv run pytest -m network pocs/poc-05-sandboxed/tests -k "sidecar or hardreq1 or admission or tool_gateway" -q` | T19 | **y** |
| **T22 Remote suite on kind** | `tester` | `poc/tests/test_poc05_kind_hostile_remote.py`, `poc/tests/test_poc05_kind_remote_lane.py`, `poc/tests/test_poc05_kind_code_runner.py` | criteria 2, 4, 6, 8 for the remote lane | `POC05_KIND=1 uv run pytest -m network pocs/poc-05-sandboxed/tests -k "remote or code_runner" -q` | T21 | **y** |
| **T23 gVisor overhead** | `tester` | `poc/tests/test_poc05_kind_gvisor.py`, `poc/notes/2026-10-xx-gvisor-overhead.md` (section 7) | each remote-capable engine (`echo-python`, `echo-pydanticai`, `echo-langgraph`, `echo-typescript`) answers under `runsc`; latency and memory under `runsc` and `runc` | `POC05_KIND=1 uv run pytest -m network pocs/poc-05-sandboxed/tests/test_poc05_kind_gvisor.py -q` | T22 | **y** |
| **T24 Remote-lane CI** | `developer`, `platform-security` review | `.github/workflows/remote-lane.yml`, `poc/tests/test_poc05_ci_wiring.py` | the workflow runs on push, creates the cluster, runs `make kind-poc05 ARGS="up test-remote"`, uploads the probe results; actions pinned by SHA | `uv run pytest pocs/poc-05-sandboxed/tests/test_poc05_ci_wiring.py -q`; `actionlint` if present | T19 | n |

### Wave 4: the deferred Kafka pass, the demo, and the documents

| Task | Owner | Owns | Starts from (failing) | Done check | Needs | kind |
| ---- | ----- | ---- | --------------------- | ---------- | ----- | ---- |
| **T25 Kafka with SASL** | `developer`, `platform-security` review | `kind5/platform/kafka.yaml`, its policy, the Kafka part of `seed.sh`, the H11 case in `test_poc05_kind_hardreq1.py` | H11 refused without SASL; the chassis publishes with it | `make kind-poc05 ARGS="kafka"`; `POC05_KIND=1 uv run pytest -m network pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py -k kafka -q` (or the exception recorded, section 2.12) | T22 | **y** |
| **T26 Demo** | `tester` | `poc/demo/demo.sh`, `poc/demo/README.md` | the demo's steps (section 7) print their results | `make kind-poc05 ARGS="up"`; `pocs/poc-05-sandboxed/demo/demo.sh` exits 0 | T21, T22 | **y** |
| **T27 Operating skill and runbooks** | `docs-editor` | `.claude/skills/poc-05-operate/SKILL.md`, `docs/guides/poc-05-runbooks.md` | — (each command in them is run once by T26's owner or pasted from T19 to T23) | `make harness-lint` | T19 to T24 | n |
| **T28 How it works** | `docs-editor` | `docs/guides/poc-05-how-it-works.md` | — | the Mermaid blocks render (`npx -y @mermaid-js/mermaid-cli` if available, else review) | T19 to T22 | n |
| **T29 Blind-spots note** | `docs-editor` with `platform-security` | `poc/notes/2026-10-xx-blind-spots.md` | — | T30's records test | T22, T23 | n |

### Wave 5: close

**T30 Close** (`docs-editor` with `chassis-architect`). Creates `docs/contracts/contract-v4.md` (section 3, with any difference the code made), `poc/notes/backlog-changes.md`, `poc/tests/test_poc05_records.py` (the gVisor and blind-spots notes exist and cover their lists). Edits `packages/chassis/CLAUDE.md` (the new files), `packages/workload-a2a` README (the flag), `deploy/CLAUDE.md` and `deploy/README.md` (a kind PoC-5 section), `deploy/compose/SECURITY.md` (the exceptions of section 2.12), `poc/README.md` (boxes with evidence), `poc/CLAUDE.md`, issues 022 H-6, 054 H-16, 026 CH-4, 055 CH-6 (status after PoC-5), `docs/planning/poc/000-plan.md`; ADR-005 stays Proposed until the user accepts it. Then `make planning-sync planning-check` and `make check`.

### Shared files: who edits, in which order

| File | Order |
| ---- | ----- |
| `chassis/profiles.py`, `chassis/server/config.py`, `chassis/ports/tool.py`, all `pyproject.toml`, `uv.lock`, `Makefile`, `poc/tests/conftest.py` | T01 only |
| `chassis/server/cli.py`, `lifecycle.py`, `remote_auth.py` | T04 only |
| `chassis/server/tool_endpoint.py`, `chassis/adapters/mcp/server.py`, `fakes/tool.py`, `suites/tool.py` | T06 only |
| `chassis/server/model_proxy.py` | T07 only |
| `kind5/run.sh` | T11 → T19 |
| `kind5/platform/seed.sh` | T12 → T19 → T25 |
| other files under `kind5/` | their wave-2 owner → T19 (fixes on apply) → T25 (Kafka only) |
| `poc/tests/test_poc05_hardening_static.py` | T13 (T14, T15 add fixture files only) |
| `poc/tests/test_poc05_kind_hardreq1.py` | T21 → T25 |
| `packages/chassis/CLAUDE.md`, `docs/contracts/`, `poc/README.md`, `deploy/compose/SECURITY.md` | T30 only (T12 edits only `deploy/compose/litellm/config.yaml`) |
| `deploy/compose/docker-compose.scale.yaml`, `docker-compose.sidecar.yaml` | T04 only (the entrypoint wait) |

## 7. Deliverables the user asked for

Each has an owner task. Commands in the documents are the ones the tasks ran, pasted with their output where it helps.

**`make kind-poc05` (T01 target, T11 and T19 script).** `make kind-poc05 ARGS="<verb>"` calls `deploy/kind/poc05/run.sh`. Verbs: `create`, `smoke` (the `runsc` and PSA checks), `build`, `load`, `seed`, `apply`, `up` (all of those in the order of section 8), `test` (both kind suites), `test-remote` (the remote suite and the remote lane only, for CI), `kafka` (the deferred pass), `status`, `results` (prints the last probe results as a table), `delete`. Every `kubectl` call pins `--context kind-poc05`; every `kind` call passes `--name poc05`. It refuses to run if the PoC-4 cluster `poc04` is up (memory), with a message that says how to delete it.

**`.claude/skills/poc-05-operate/SKILL.md` (T27).** Outline: when to use it; prerequisites (Docker memory, `kind`, `kubectl`, `jq`, `openssl`; the `poc04` cluster deleted); bring up (`make kind-poc05 ARGS=up`, what Ready looks like); run the suites (offline `make test-poc POC=05`; kind `make kind-poc05 ARGS=test`; one check by id); run the demo; read results (`ARGS=results`, the JSONL in `notes/hostile/`, what `refused` plus a `control: allowed` means, what `error` means); tear down; troubleshoot (links to the runbooks by name). Under 120 lines; no secret values, ever; how to read a Secret's presence without printing it.

**`docs/guides/poc-05-how-it-works.md` (T28).** In the style of `docs/guides/poc-04-how-it-works.md`. Sections and Mermaid diagrams:

1. The question and the answer in three sentences.
2. Pod topology of both lanes (`flowchart`): the `sidecar` pod (chassis + workload, shared network namespace, loopback proxies), the `remote` pair (chassis pod, remote `Sandbox` on gVisor, 8091 and 9000), the platform namespace, the code-runner sandbox.
3. The trust rule decision flow (`flowchart`): config load (`spec.trust`, lane) → admission (pod shape → lane; label; params list; image prefix) → admitted or rejected with the rule's message.
4. The NetworkPolicy map (`flowchart`): every allowed edge from section 2.10, and the named denied destinations drawn as dashed edges to a "deny" node.
5. The remote-lane request path (`sequenceDiagram`): caller → chassis public port → A2A with the token → remote `handle` → model call to 8091 with the token and `traceparent` → `BearerAuth` → `RequireRun` → model proxy → LiteLLM with the virtual key → back; and a tool call through `/mcp` → gateway → tool server.
6. The credential map (table plus a `flowchart`): each Secret, where it is mounted, who can read it, what refuses without it.
7. What each control proves (link to section 5's table) and what it does not (link to the blind-spots note).

**`docs/guides/poc-05-runbooks.md` (T27).** One section each, as symptom, check (commands), cause, fix:

- The cluster will not start (Docker memory, the `poc04` cluster still up, the gVisor install checksum, containerd not restarted).
- A sandbox pod is not Ready (`runsc` handler missing, the agent-sandbox controller not running, PSA rejection, the image not loaded into the node, the token Secret missing).
- A probe check passes when it should be refused (first check the paired control; then the CNI enforcing, the policy edge, the runtime marker; what to record; never edit the test to pass).
- Rotating the remote token (the three steps: `seed.sh rotate` as previous, restart the remote, swap, drop the previous; how to verify with H17 without printing the token).
- Adding an allow-listed tool (register the MCP server in LiteLLM, the key's permission, the NetworkPolicy edge from LiteLLM, verify with the tool gateway test; write tools need idempotency).
- Admission rejects a deploy (read the rule's message; trust label, image prefix, params list; who may change `agent-trust-params`).

**`pocs/poc-05-sandboxed/demo/demo.sh` (T26).** Assumes `make kind-poc05 ARGS=up`. Steps, each printing one line per check (id, attempt, outcome, control):

1. A normal request through `chassis-echo-remote` and `agent-echo`: both answer.
2. The remote probe: secrets (H25), a public address (H19), disk (H21), processes (H23), the unlisted tool (H08), the proxy without its token (H17). Each refused and logged; the control allowed.
3. A normal request again: still answers.
4. The sidecar probe: LiteLLM (H05) and the MCP gateway (H07) directly, refused; through the chassis, allowed.
5. Admission: `kubectl apply --dry-run=server` of an `untrusted` sidecar pod and a third-party image, both rejected with their messages.
6. Where it was logged: the `probe.check` lines, the chassis's `remote_unauthenticated` count, LiteLLM's 401 lines (no secret values).

**`.github/workflows/remote-lane.yml` (T24).** On push and pull request. One job, `ubuntu-latest`, timeout 30 minutes: checkout and setup-uv pinned by SHA; install kind and kubectl pinned by version and checksum; `make kind-poc05 ARGS="up test-remote"` (create, gVisor, build, load, seed, apply, the remote lane contract and the remote suite); upload `notes/hostile/*.jsonl` and pod logs on failure; `make kind-poc05 ARGS=delete` always. **Spike:** gVisor's `systrap` platform on a GitHub runner; fallback: the job runs the remote lane on `runc` and marks H28 skipped there, and the offline remote lane in `make check` stays the every-commit gate (hard requirement 2).

**The blind-spots note (T29),** `poc/notes/2026-10-xx-blind-spots.md`, criterion 10. Outline: each item from the threat model's section 5, with what the chassis sees instead and who does see it; plus the PoC-5 additions: NetworkPolicy drops are not logged on kindnet; the trust params list is a hand-kept ConfigMap; a third-party tool server may not honor the idempotency key; the code runner's result cache is in memory; DNS in the sidecar lane (H20); H31 and H32 not run; B12 open (PoC-8); the TypeScript template has no token flag yet.

**The gVisor overhead note (T23),** `poc/notes/2026-10-xx-gvisor-overhead.md`, criterion 9. Outline: the command that produced each figure; per engine (`echo-python`, `echo-pydanticai`, `echo-langgraph`, `echo-typescript`) under `runsc` and `runc`: start time to Ready, p50 and p95 of 200 sequential runs against the fake model, memory (the pod's working set after warm-up); the `systrap` platform named; exceptions (an engine that does not start under gVisor, with the error and the decision); what the numbers do not cover (KVM platform, real models).

## 8. Risks, the memory budget, and the bring-up order

### Memory budget on the 7.75 GiB VM

Figures are suggested limits, to be replaced by the measured working set in T19's notes. The PoC-4 cluster `poc04` and the Compose stacks must be down; other Docker stacks on this VM (the `paligo-*` stacks) must be stopped for the kind tasks.

| Part | Pods | Limit each | Total |
| ---- | ---- | ---------- | ----- |
| kind node: control plane, kubelet, containerd, kindnet, CoreDNS | 1 node | — | ~1.1 GiB (measured in PoC-4 as the floor; re-measure) |
| agent-sandbox controller | 1 | 64Mi | 64Mi |
| LiteLLM (models and MCP gateway) | 1 | 640Mi | 640Mi |
| Postgres (LiteLLM's keys) | 1 | 128Mi | 128Mi |
| fake model server, fake MCP server | 2 | 64Mi, 96Mi | 160Mi |
| Valkey, MinIO | 2 | 64Mi, 256Mi | 320Mi |
| agent pods in `sidecar` (`agent-echo`, `agent-probe-sidecar`): chassis + workload | 2 | 256Mi + 256Mi | 1 GiB |
| chassis pods for `remote` (`chassis-echo-remote`, `chassis-probe-remote`) | 2 | 256Mi | 512Mi |
| remote sandboxes (`remote-echo`, `remote-probe`), plus the gVisor sentry (suggested 64Mi) | 2 | 320Mi | 640Mi |
| code-runner sandbox, plus sentry | 1 | 320Mi | 320Mi |
| **Total without Kafka** | | | **~4.9 GiB of limits** (expected working set ~3 GiB) |
| Kafka with SASL, deferred pass (JVM heap 512m) | 1 | 896Mi | 896Mi; the agent pods except `agent-echo` are scaled to zero first |

The gVisor note (T23) runs one engine at a time in the remote lane, with the probe pods scaled to zero.

### Bring-up order (`make kind-poc05 ARGS=up`)

1. Preflight: `poc04` is not running; Docker has at least 6 GiB free (suggested); `jq` and `openssl` present.
2. Create the cluster, install gVisor, restart containerd, `smoke` (`runsc` marker, PSA rejects root).
3. Base: namespaces with PSA labels, RuntimeClass `gvisor`, `default-deny` in every namespace, the ANP if the spike allows.
4. agent-sandbox controller (pinned); wait for its CRD.
5. Admission: params, policy, binding, submitter RBAC, before any workload, so every later deploy is checked.
6. Build and load the images (chassis, fake model server, fake MCP server, code-runner, echo engines, hostile).
7. Seed, first pass: the master key, the database password, Valkey, MinIO.
8. Platform: Postgres, then LiteLLM (wait for its migrations), then the fake model and MCP servers, Valkey, MinIO.
9. The code-runner sandbox; register it and the fake MCP server in LiteLLM.
10. Seed, second pass: one virtual key per service with its tool permissions; the remote tokens.
11. Remote sandboxes, then the chassis pods (the remote's chassis needs its remote's fixed Service IP).
12. A normal request through `agent-echo` and `chassis-echo-remote`. Only then the suites.

### Risks and fallbacks

- **gVisor on this VM** (arm64, Docker Desktop, `systrap`, no KVM). **Spike.** Fallback: none for the remote lane on this host; the runc fallback is for CI only, and criterion 9 would list the failure.
- **kindnet does not fully enforce egress or link-local denial.** Fallback: Calico with `disableDefaultCNI` (about 200 MiB more), recorded in ADR-005.
- **agent-sandbox does not fit kind v1.37.** Fallback: plain Pods with `runtimeClassName: gvisor` (section 2.8); the blind-spots note says so.
- **LiteLLM details** (the per-key MCP permission field, the header the MCP gateway reads, `_meta` forwarding). **Spike** each; fallbacks in sections 2.7 and 2.9.
- **The PID cap under gVisor** may not stop a fork loop. Fallback in section 2.11: record which control held.
- **LiteLLM's start time and memory** (migrations on start). `run.sh` waits up to 180 s (suggested) and prints the pod's last events if not Ready.
- **PoC-4 is built but uncommitted** and PoC-5 edits the same files (`cli.py`, `lifecycle.py`, `model_proxy.py`, `tool_endpoint.py`). PoC-5 work should start on a branch after PoC-4's commit split, or the diffs mix (question 3).
- **Timebox (3 weeks).** Cut order if late: the Kafka pass (record H11 as the exception), the ANP (static fallback), agent-sandbox for the code runner (plain Pod), the TypeScript engine in the gVisor note (listed as an exception). Never cut: the remote lane in `make check`, H17 with its control, the admission rules, hard requirement 1 for LiteLLM and the MCP gateway.

## 9. ADR-005

T02 writes it from `docs/templates/adr.md` with the `adr` skill, as `docs/planning/adr/005-remote-lane-auth-and-trust-admission.md` (suggested slug), status **Proposed**. T30 updates it with what the cluster showed; the user accepts it.

**Title:** The remote lane's credential and the trust rule's admission check.

**Context:** ADR-001 items 4 to 8 and hard requirement 1 send untrusted code to the `remote` lane and ask each remote to authenticate to the chassis's proxies. The scope offers mTLS through cert-manager or a per-remote token, and Kyverno or ValidatingAdmissionPolicy. The VM has 7.75 GiB; the managed runtimes of PoC-6b can send a bearer header but often not a client certificate.

**Decision:**

1. One random bearer token per remote, used both ways (the chassis to the remote's A2A server, the remote to the chassis's remote listener on the pod IP, port 8091 suggested), with a previous token for rotation. No mTLS and no cert-manager in PoC-5.
2. The remote listener also requires a `traceparent` that names a run in flight (403 `run_required`), so a remote spends only inside a run's budget.
3. The connector never follows the agent card's URL.
4. The trust rule is checked twice: in the chassis config (`spec.trust: untrusted` needs `connector: remote`) and at admission by a ValidatingAdmissionPolicy (no Kyverno). Admission finds the lane from the pod's shape, believes `untrusted` always, and believes `trusted` only when a platform-owned params list agrees.
5. Code from a model runs in the `code-runner` MCP server in a gVisor sandbox, behind the MCP gateway's per-key allow-list, so it does not make an agent untrusted.
6. The CNI is kindnet if it enforces egress and link-local denial, else Calico (filled in by T30 from the spike and T19).

**Consequences:** no controller and no CA to run; a leaked token opens only one chassis's proxies, inside a run, from the remote's pod label; the token is a bearer, so it must never be logged (tested). Admission's trust list is a hand-kept ConfigMap until signed images (H-10). Kyverno remains the likely tool for signatures and the minimum-version rule (CH-7).

**Revisit when:** a cloud mesh gives mTLS with no added controller (then replace the token); a managed runtime needs a different auth scheme (PoC-6b, `auth.scheme`); image signatures land (H-10, then the trust list comes from the signature); more than a few admission rules are needed, or a mutation is needed (Kyverno).

## 10. Questions for the user

1. **"No secrets mounted" in the remote pod.** This plan reads it as "no provider key and no internal credential": the per-remote token is the remote's own credential, one env var (section 2.2). Confirmed by the user on 2026-10-08: yes. If it had not been, the alternative is a projected service account token checked by the chassis, which needs the Kubernetes API from the chassis (it is in the deny list today).
2. **Kafka with SASL.** The plan runs it as a separate, last pass and records H11 as an exception (owner `platform-security`, closing in 020 X-8) if it does not fit in memory. Is the exception acceptable for PoC-5's exit, or must H11 pass on kind?
3. **PoC-4 is built but not committed.** PoC-5 edits the same chassis files. Should PoC-4's commit split land first (recommended), and PoC-5 start on its own branch from there?
4. **One ADR or two.** The plan records the token, the listener rule, admission, the trust signal, the code runner, and the CNI in one ADR-005. Split admission into its own ADR?
5. **The remote-lane CI job on every push.** A kind job with gVisor takes about 10 to 20 minutes (suggested). The offline remote lane in `make check` already gates every commit. Run the kind job on every push (as the plan says), or on pull requests and `main` only?
6. **Stopping other Docker stacks.** The kind tasks need the `paligo-*` stacks stopped on this VM. May the orchestrator stop them during kind tasks, or must you do it?
