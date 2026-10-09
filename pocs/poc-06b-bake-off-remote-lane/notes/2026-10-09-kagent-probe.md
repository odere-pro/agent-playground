# kagent probe: can the chassis front kagent as the PoC-6b remote solution? (2026-10-09)

## Why

PoC-6 part B, scope item "one remote solution through the `remote` lane" (suggested: kagent on kind), task T-KPROBE. Exit criterion 2: the remote solution passes the contract suite with no change to the chassis core.

## Setup

- kagent: `kagent-dev/kagent` commit `e324f6d844da1d99cd035124db856c3b74fae63b` (2026-10-09, shallow clone; no tags, so no release number). All citations below are paths in that commit. Line numbers are from my reads and may be off by a few lines.
- Chassis worktree: `bd329e4` plus this note (a2a-sdk 1.2.0 in `uv.lock`), `RemoteConnector` from `packages/chassis/src/chassis/adapters/a2a/remote.py`.
- Machine: Linux 6.18, 4 CPU, 16 GB. Docker was not started; no kind cluster; no Helm render.
- Python runtime installed with `UV_PROJECT_ENVIRONMENT=/tmp/poc06-kprobe/venv-kagent uv sync --frozen --package kagent-adk --no-dev --python 3.13` in `kagent/python` (a2a-sdk 1.1.5, google-adk 2.11.0 from `python/uv.lock`).
- I did NOT clone `agent-substrate/substrate` (the tool policy denied it). Everything about Substrate comes from kagent's own docs and code.

## Item 1: the runtime, and how an Agent becomes a running agent

- Today's kagent (`api.kagent.dev/v1alpha3`) is not "an Agent becomes a Deployment". `docs/architecture/README.md` and `docs/architecture/configuration-and-compilation.md` (lines 82-120): Agent + Harness + AgentTemplate are compiled into an immutable revision, applied as an **ate-api ActorTemplate** to **Agent Substrate** (a separate project, `github.com/kagent-dev/substrate v0.5.0-alpha2`, `go/go.mod:498`). A Session creates a Substrate **Actor** (a sandboxed process in a WorkerPool, gVisor or microvm: `helm/kagent/values.yaml:437-457`). The controller has no code path that creates a Deployment for an agent (grep for `appsv1`/`Deployment` in `go/core/internal` finds only the tools code).
- Runtime images (`Makefile:65-69`): `golang-adk` (default: `helm/kagent/values.yaml:199-203`, `controller.agentImage.repository: kagent-dev/kagent/golang-adk`), `kagent-adk` (Python, `python/Dockerfile`: Debian bookworm-slim, Python 3.13, user 65532, entrypoint `kagent-adk run --host 0.0.0.0 --port 8080`), `claude-harness`, `codex-harness`, and BYO. Python package: `python/packages/kagent-adk` (Google ADK based, `google-adk[a2a,db]>=2.11.0`, `pyproject.toml`). Go: `go/adk` (Google ADK for Go).
- The Harness image must be pinned by digest (`go/api/v1alpha3/harness_types.go`, `HarnessWorkload.Image` pattern `@sha256:`).
- Who serves A2A: the **controller**, not the agent pod. `go/core/internal/a2agateway/http.go` (lines ~15-30, `HTTPPathPrefix = "/agents/"`): `POST /agents/{namespace}/{name}` is JSON-RPC and `GET /agents/{namespace}/{name}/.well-known/agent-card.json` is the card, on the controller port 8083 (`values.yaml:292-295`). The gateway then calls the actor over A2A gRPC through Substrate's `atenet-router` (`go/core/internal/a2agateway/runtime.go`). The actor's own A2A is private.
- Cost of this model: kagent needs PostgreSQL (the chart fails without `database.postgres.connectionStringSecretRef`: `helm/kagent/templates/controller-deployment.yaml:1-4`; sessions, tasks and events live there, `docs/architecture/a2a-gateway.md`) and a separate Substrate install (`ate-system`, PodCertificate controller, credential provider, HTTPS-intercepting egress gateway: `DEVELOPMENT.md:170-250`, `docs/architecture/credential-injection.md`). `controller.substrate.enabled` defaults to false (`values.yaml:402-405`), so a plain `helm install` has a controller that cannot run agents.

## Item 2: protocol version, SDK, card URL

- A2A 1.0 (protobuf era): card interfaces carry `protocolBinding: JSONRPC`, `protocolVersion: 1.0`; the SDK sends `A2A-Version: 1.0` (seen in the capture below). Python: `a2a-sdk>=1.1.5,<2`, locked 1.1.5 (`python/uv.lock:60-61`). Go controller and Go ADK: `github.com/a2aproject/a2a-go/v2 v2.6.0` (`go/go.mod:10`). Chassis is a2a-sdk 1.2.0. Same major; the 1.2 client talked to the 1.1.5 server without trouble (run below).
- Card URL: the gateway rewrites it. `go/core/internal/a2agateway/gateway.go` (`GetExtendedAgentCard`, lines ~145-165): JSON-RPC interface = `strings.TrimRight(gatewayURL,"/") + "/agents/" + ns + "/" + name`, plus a gRPC interface at `gatewayURL`. `gatewayURL` is `controller.a2aGatewayUrl` (`values.yaml:196-198`), default `http://<fullname>-controller.<ns>.svc:<grpc-port>`. So the card URL equals the called URL only if `a2aGatewayUrl` is set to the same base the chassis uses. The chassis does not refuse a mismatch: `RemoteConnector._check_card` (remote.py) keeps only the JSON-RPC interface and **overwrites its URL with `spec.engine.url`**; it refuses only a card with no JSON-RPC interface. The gateway always emits one, so the check passes. In the local run the pinned URL was `http://127.0.0.1:18080` (`chassis-run.json`, `pinned_card_urls`).
- The gateway also sets `security_requirements`/`security_schemes` to nil (same function), so the card does not advertise bearer auth.

## Item 3: shape of a reply (real capture, Python runtime, fake model)

Captured with `kagent/scripts` below: SSE for `SendStreamingMessage`, trimmed to the keys that matter (full run in `kagent-probe/`; the raw files are in `/tmp/poc06-kprobe/run/out1/` and not committed):

```
task          TASK_STATE_SUBMITTED   (history holds the user message; contextId echoed)
statusUpdate  TASK_STATE_WORKING
artifactUpdate artifactId=A parts=[{"text":"Plain "}]                       (no append flag on the first)
artifactUpdate artifactId=A append=true parts=[{"text":"words. "}]  ... x5 more chunks
artifactUpdate artifactId=A lastChunk=true parts=[{"text":"Plain words. Short sentences. Same facts."}]
               artifact.metadata["kagent.dev/a2a/usage"]={promptTokenCount:42, candidatesTokenCount:9, totalTokenCount:51}
statusUpdate  TASK_STATE_WORKING  message{role:ROLE_AGENT, no parts}
statusUpdate  TASK_STATE_COMPLETED metadata["kagent.dev/a2a/usage"]=same
```

Unary `SendMessage` returns one `task` with `status.state=TASK_STATE_COMPLETED`, `artifacts[0].parts[0].text` = the full answer, and the usage in `metadata`. The streamed chunks are followed by a final `lastChunk` artifact event that repeats the whole text. A plain mapping must not emit that repeat as a delta.

What the real chassis `RemoteConnector` did with it (`kagent-probe/probe_chassis_remote.py`, exit 0):

```
chassis events: [{"schema_version": "0", "type": "end", "status": "ok", "output": null}]
```

The card fetch, the stream and the bearer worked. The answer text was dropped, because the chassis reads text only from `metadata["chassis.event"]`. COMPLETED already maps to `end ok` through `_from_state` (mapping.py). No `start` was emitted.

Other parts and states exist but were not exercised: `TASK_STATE_INPUT_REQUIRED`/`AUTH_REQUIRED` for human-in-the-loop (the card advertises the extension `https://kagent.dev/extensions/hitl/v1`), tool calls as data parts (`python/packages/kagent-adk/src/kagent/adk/converters/event_converter.py`; not read in detail), and failure as `TASK_STATE_FAILED` (`converters/error_mappings.py`; not run).

Latency finding: with default OTel settings the COMPLETED event came **16.8 s** after the last chunk (an SSE `: ping` at 15 s sat in between); with `OTEL_TRACES_EXPORTER=none OTEL_METRICS_EXPORTER=none OTEL_LOGS_EXPORTER=none` the whole stream took **0.64 s** (`time_stream.py`). Likely the final event waits for a telemetry flush against an unreachable collector. On kind, set the exporters on a real collector or to none. Not root-caused.

## Item 4: model access

- Any OpenAI-compatible base URL: yes. ModelConfig `openAI.baseUrl` (`helm/kagent-crds/templates/api.kagent.dev_modelconfigs.yaml:56`), `apiKeySecret`+`apiKeySecretKey` (lines 155-161), `defaultHeaders` (line 296). Python config field `base_url` (`kagent/adk/types.py:275`).
- Key from a Secret: the key never enters the actor. Substrate's HTTPS-intercepting gateway swaps a placeholder for the Secret value per destination host and header (`docs/architecture/credential-injection.md:1-30, 48-66`). Constraints there: model endpoints must be **DNS names, not IPs** (lines 14-18, 64-66); different credentials for one hostname are rejected.
- **Passthrough**: `ModelConfig.spec.apiKeyPassthrough: true` (`go/api/v1alpha3/modelconfig_types.go:584-589`, mutually exclusive with `apiKeySecret`) uses the inbound request's `Authorization: Bearer` as the model API key. A passthrough model cannot share a hostname with static gateway credentials (`credential-injection.md:75-77`).
- Traceparent: yes, measured. Python runtime, inbound `traceparent: 00-17fe6f30...-5d5c5b55414bb7fe-01` from the chassis; the fake model server saw `traceparent: 00-17fe6f30...-ab704fe0adffa2ea-01` (same trace id, new span id) and `Authorization: Bearer` equal to the inbound token (`model-requests.jsonl`, `authorization_is_probe_token: true`, all three calls). `RequireRun` keys on the trace id, so this passes. Go runtime: source says the same, `go/adk/pkg/models/base.go:59-62` wraps the model client in `otelhttp.NewTransport`, and `go/pkg/telemetry/telemetry.go:82` installs the global propagator; not run.
- **Not shown end to end**: the same propagation through the controller gateway and Substrate (controller to actor over gRPC with `a2aext.NewClientPropagator` and `otelgrpc`, `runtime.go`; Substrate egress MITM). Only the actor was tested, directly. The insecure authenticator forwards the caller's `Authorization` upstream (`go/core/internal/httpserver/auth/authn.go:48-60`), so passthrough should work through the gateway, but that is unverified.

So the chassis proxy path looks workable: ModelConfig `baseUrl` = `http://<chassis-remote-listener DNS name>:8091/v1`, `apiKeyPassthrough: true`, and the chassis sends its per-remote token as the bearer (ADR-005 decision 1 already uses one token both ways). The ADR-001 item 8 relaxation (own scoped LiteLLM key) is the fallback if passthrough or propagation fails through Substrate.

## Item 5: inbound auth

- Default `controller.auth.mode: insecure` (`values.yaml:189-193`): `InsecureAuthenticator` (`authn.go:22-46`) **ignores any bearer** and treats every caller as `admin@kagent.dev` (or `X-User-Id`). No token is checked.
- `trusted-proxy`: `ProxyAuthenticator` (`go/core/internal/httpserver/auth/proxy_authn.go:28-79`) requires `Authorization: Bearer <JWT>`, decodes the payload **without verifying the signature** (line 37) and takes the user from a claim. A random hex token is not a JWT, so it gets 401. It needs a validating proxy in front (oauth2-proxy subchart).
- So kagent does not itself check our token in either mode. The chassis's lane check must come from NetworkPolicy plus a front proxy, or the chassis mints a JWT (unsigned claims are accepted; the proxy must be what validates).

## Item 6: footprint and fit

Requests from `helm/kagent/values.yaml`: controller 100m CPU / 128Mi, limit 2 CPU / 512Mi (lines 284-290); UI 100m / 256Mi, limit 1 CPU / 1Gi (lines 484-490); `kagent-tools` 50m / 128Mi (790-795); `grafana-mcp` 100m / 128Mi (829-833). `kmcp` subchart has no values here. Agent pod: **none**; the actor lives in a Substrate WorkerPool, sized by `substrateWorkerPool.template.resources` (empty by default, `values.yaml:439-457`) and the Harness capacity. Not measured. Not counted: PostgreSQL, Substrate (ate-api, atenet-router, atelet, credential provider, MITM gateway, PodCertificate controller). The kind VM has 7.75 GiB (ADR-005); I expect Substrate plus Postgres to be the large part, but I could not measure it.

- CRDs (`helm/kagent-crds/templates`): `agents`, `agenttemplates`, `harnesses`, `modelconfigs`, `modelproviderconfigs`, `remotemcpservers`, `sandboxtemplates` (group `api.kagent.dev`), plus Substrate's `ate.dev` CRDs (WorkerPool, ActorTemplate) and KMCP's `MCPServer`.
- Images: `ghcr.io/kagent-dev/kagent/{controller,ui,golang-adk,kagent-adk,sandbox-guest}` (`values.yaml:44-45`, registry `ghcr.io`), `oci://ghcr.io/kagent-dev/kmcp/helm`, `oci://ghcr.io/kagent-dev/tools/helm`, oauth2-proxy from `oauth2-proxy.github.io` (disabled by default), Substrate images (not read). `global.imageRegistry` is a mirror knob (lines 20-26).
- securityContext (chart pods): `runAsNonRoot`, `seccomp RuntimeDefault`, `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, drop ALL (`values.yaml:75-88`). The Python image runs as 65532. Actor pods: not determined (Substrate source not read).
- Cluster needs: Kubernetes feature gates `ClusterTrustBundle`, `ClusterTrustBundleProjection`, `PodCertificateRequest` and `certificates.k8s.io/v1beta1` (`scripts/kind/kind-config.yaml:22-30`; kagent's own kind uses 1.35). Our `deploy/kind/poc05/cluster.yaml` uses `kindest/node:v1.37.0`; its gates were not checked.
- Clashes with our admission rules:
  1. Pod-level rules were written for pods we submit. Agent execution is inside Substrate WorkerPool pods that Substrate creates, so our "no service-account token", "gVisor RuntimeClass" and registry-prefix rules would apply to Substrate's pods, not to kagent's agent. Whether those pods mount a token or use `runtimeClassName: gvisor` (vs. gVisor inside the worker) is **not determined**.
  2. The controller needs a service account with Kubernetes API rights, and `substrate-podidentity`/`servicedns` projected volumes (`controller-deployment.yaml:60-75`). It also has a large ClusterRole. Our rule 6c (only the chassis container may reference a Secret) would need an exception for it.
  3. Egress for the actor is controlled by Substrate's gateway, not by our NetworkPolicy: HTTPS MITM with a CA at `/run/kagent/egress/trust-bundle.pem`. That conflicts with "only the chassis holds credentials" in spirit: the Secret is read by Substrate's credential provider, not by the chassis.
  4. Session state is in kagent's PostgreSQL, so "stateless replicas" does not hold for the remote (fine for the lane: it is a list item for "what the chassis cannot control").

## Item 7: running the runtime locally

Done for the Python runtime (`kagent-adk` 0.4.0, in-memory task store, no controller, no Substrate), against the repo's fake model server wrapped to log request headers. Not done for the Go runtime (needs Go 1.27, `go/go.mod:3`; local Go is 1.24.7) and not for the controller gateway (needs Postgres and Substrate).

Commands (scripts in `kagent-probe/`, saved as `.py.txt` so the repo lint skips them; copy to `.py` to run; paths there are `/tmp/poc06-kprobe/...`):

```
uv run --package fake-model-server python fake_model_logged.py script.yaml 18090 model-requests.jsonl
start_kagent_adk.sh kagent.log OTEL_TRACES_EXPORTER=none OTEL_METRICS_EXPORTER=none OTEL_LOGS_EXPORTER=none
uv run --project . --all-packages python probe_chassis_remote.py http://127.0.0.1:18080 out1   # exit 0
python time_stream.py http://127.0.0.1:18080
```

Output (tails): `chassis events: [... "type": "end", "status": "ok", "output": null}]`, `card 200`, `stream 200 4911`, `unary 200 1036`; timing with exporters off `total 0.64s`, with defaults `total 16.77s`. The SDK request the chassis sent (bearer redacted): `POST http://127.0.0.1:18080` JSON-RPC `SendStreamingMessage`, headers `a2a-version: 1.0`, `traceparent`, `accept: text/event-stream`, message `contextId` = the chassis trace id, `metadata` keys `chassis.ctx`, `chassis.input`, `chassis.schema_version`.

Controls: the 401 side was not tested (the runtime has no auth). The model-side check has its own control: the same calls without passthrough would carry no probe token; the log shows it present on all three calls.

This is a local TCP run, not the offline gate. It is not repeatable offline.

## Reading

The A2A wire is compatible: the chassis's own `RemoteConnector` connected, fetched the card, streamed, and got `end ok`. The answer text and usage are lost until a plain-A2A mapping exists. The blocker is not the wire; it is the stack behind it (Substrate plus PostgreSQL).

## Go or no-go

**No-go for running kagent on our kind cluster in CI as it is today. Go for a bounded follow-up spike.**

- Reasons: (a) a working install needs Substrate (alpha, `v0.5.0-alpha2`, a separate project with node-level components, new Kubernetes feature gates, an HTTPS-intercepting egress gateway) and PostgreSQL, none of which are in our kind budget or our admission model; (b) unverified: Substrate pods' token, RuntimeClass and registry behaviour; (c) the repo's own docs say the runtime-identity header "can be forged" and builds "are for isolated deployments" (`docs/architecture/a2a-gateway.md`, "Runtime authority"); (d) inbound auth cannot check our token.
- What would flip it to go: a spike that installs Substrate and Postgres on a kind node with the three feature gates, measures memory (budget: whatever remains of 7.75 GiB), shows the passthrough and traceparent path through the controller, and shows Substrate's pods against our admission rules. I could not do that here (no cluster, no Substrate source).
- Cheaper alternative for the "remote solution" slot: run `kagent-adk` itself (a Python A2A server, 0.64 s per call, no Postgres, `local=True` style) as a `remote` workload in a gVisor pod behind our bearer check. It tests the same plain-A2A mapping and the same model-path result, but it is not "kagent on Kubernetes". Decide with the owner whether that counts for exit criterion 2.

## Recommended integration (if the spike passes)

- URL the chassis calls: `spec.engine.url = http://kagent-controller.<ns>.svc:8083/agents/<ns>/<agent>` (the controller's gateway). Set `controller.a2aGatewayUrl` to `http://kagent-controller.<ns>.svc:8083` so the card agrees (the chassis pins anyway).
- Auth: kagent ignores the bearer in insecure mode. Keep NetworkPolicy by label as the control and put the chassis token check in front (a small proxy), or accept NetworkPolicy only and record it.
- Model path: through the chassis proxy. ModelConfig `openAI.baseUrl` = the chassis remote listener by DNS name (`http://<svc>:8091/v1`), `apiKeyPassthrough: true`, so the model call carries the remote token and the propagated trace id. Fall back to its own scoped LiteLLM key (ADR-001 item 8) only if passthrough or propagation fails through Substrate. Do not use `apiKeySecret` on the chassis hostname (it conflicts with passthrough).
- Plain-A2A mode the chassis must handle (no change to `chassis.core`, only the A2A adapter):
  1. No `chassis.event`: map `artifactUpdate` text parts to `delta` (the first artifact event has no `append`; later ones `append=true`).
  2. Ignore the final `lastChunk=true` artifact event if deltas were already sent (it repeats the whole text), or use it as `end.output` if none were.
  3. Emit `start` itself (kagent sends none); `TASK_STATE_COMPLETED` to `end ok`, `FAILED`/`REJECTED` to `error`, `INPUT_REQUIRED`/`AUTH_REQUIRED` to an error (the contract has no pause), `CANCELED` as today.
  4. Take token usage from `metadata["kagent.dev/a2a/usage"]` (artifact and final status).
  5. Unary fallback: a `task` with `artifacts[].parts[].text`.
  6. `context_id`: the chassis sets it to the trace id. The gateway treats a context id as an existing Session (`docs/architecture/a2a-gateway.md:19-27`). I did not read `InteractionService.PrepareSend`; a plain mode probably must send no `contextId` (a new Session per run) and take the one returned. Unverified; check before the spike.
  7. Ignore SSE comments (`: ping`) and a status update with an empty agent message.
  8. Per-run Sessions and Actors are stateful in kagent's Postgres; the deadline and cancel (`CancelTask`) go through the gateway.

## Open risks

- Substrate source not read; its pod specs are the main unknown for admission and footprint.
- The Go runtime (the default image) was not run; only the Python one.
- The 16 s delay with default telemetry settings is not root-caused.
- No kagent release number: the clone is a shallow `main` commit with an alpha API (`v1alpha3`) that changes between commits.
- Failure, tool-call and input-required replies were not captured.
