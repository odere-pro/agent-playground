# Architecture

The shape of the agent container. Why each rule exists is in [ADR-001](../planning/adr/001-chassis-delivery-model.md) (the delivery model), [ADR-002](../planning/adr/002-template-a2a-server-placement.md) (the template A2A server), [ADR-004](../planning/adr/004-events-through-a-broker-client.md) (events), and [ADR-005](../planning/adr/005-remote-lane-auth-and-trust-admission.md) (the remote lane).

## The shape

- **One image, no business logic.** The chassis image is the `chassis` package plus the `chassis serve` launcher. Every service runs the same image, from one repo, one CI pipeline, and one version number.
- **The chassis is always in front.** It owns the public port and takes every request, including a service's own declared operations. The workload listens only on localhost, or on a private address in the `remote` lane.
- **The workload plugs in behind it over A2A**, through one of three lanes. There is no SDK and no local API of our own.
- **Only the chassis holds credentials** (hard requirement 1).
- **Every lane is tested on every commit** (hard requirement 2).

## The core behind ports

The chassis is a core behind ports, with one adapter per real dependency. `make lint` enforces the import rules.

| Layer | Folder | Rule |
| ----- | ------ | ---- |
| Core | `packages/chassis/src/chassis/core/` | The envelope, the events, `collect()`, the `Handle` type, `traceparent` helpers, the inbound adapter seam, the manifest, the result events. No network, no product SDK, no web framework |
| Ports | `chassis/ports/` | One `typing.Protocol` per dependency. `PortBundle` carries them |
| Fakes | `chassis/fakes/` | One in-memory double per port. The fake and every real adapter pass the same contract suite in `packages/contract-suites` |
| Adapters | `chassis/adapters/` | One package per product. The product SDK is imported only there |
| Server | `chassis/server/` | The pipeline, the interfaces, idempotency, readiness, lifecycle, the proxies, security, the guard stages, the evaluator gate, the feedback route, metrics |

The ports, with their fake and real adapters (`chassis/profiles.py` `REGISTRY`):

| Port | Fake | Real adapters |
| ---- | ---- | ------------- |
| `ModelPort` | `ScriptedModel` | `litellm` |
| `EngineConnector` | `FakeEngine` (a test double, not a lane) | `inprocess`, `sidecar`, `remote` |
| `ConfigPort` | `InMemoryConfig` | `minio`, `s3` |
| `TelemetryPort` | `InMemoryTelemetry` | `otel` |
| `ToolPort` | `InMemoryTools`, `WriteModeTools` | `mcp` (the gateway) |
| `StatePort` | `InMemoryState` | `valkey` |
| `EventPort` | `InMemoryBus` | `kafka`, `dapr` |
| `AuthPort` | `StaticTokens` | `oidc` |
| `GuardrailPort` | `KeywordGuardrails` | `litellm` |
| `EvaluatorPort` | `ScriptedEvaluator` | `judge`, `encoder` |
| `FeedbackPort` | `InMemoryFeedback` | `langfuse`; `langsmith` is decided (ADR-009 item 3a) and not built |

Adapters are picked by `spec.adapters`, never in code. The lane is `spec.engine.connector`, not an adapter. Three profiles set the defaults:

| Profile | What it binds |
| ------- | ------------- |
| `fake` | Every port to its fake. `make test` runs here: no socket, no key |
| `local` | `litellm`, `minio`, `otel`, `mcp`, `valkey`; the PoC-7 ports off |
| `cloud` | As `local`, with `s3`, `oidc`, `litellm` guardrails, `langfuse` feedback. It refuses `fake`, `memory`, and `static` |

A new port arrives with its first adapter, its fake, and its contract suite: [adding a port](../guides/adding-a-port.md).

## The pipeline

Every request, on every interface, runs the same pipeline (`chassis/server/pipeline.py`, `RunPipeline`). Auth runs as middleware on every public route but `/health` and `/ready`, before the body is read, and the rate limit runs inside it, because it reads the principal (`chassis/server/app.py`). Then: the inbound adapter maps the body to one `Request`; the limits are enforced; idempotency claims the key; the config version is pinned; the input guard runs; the engine connector runs the workload, wrapped by the evaluator gate when it is on; the tool-output and output guards run on what comes back; the result event is published; the span closes. The stage order as built is in the [contract](contract.md). The pipeline runs in every lane, but it is not the security boundary against hostile code: that is the lane and the shared services ([security](security.md)).

## The lanes and the trust rule

`spec.engine.connector` picks the lane. `spec.trust` declares `trusted` or `untrusted`, and an admission check refuses an `untrusted` workload outside `remote`.

| Lane | For | Shape |
| ---- | --- | ----- |
| `sidecar` (default) | Trusted services, any language | Two containers in one pod. The chassis holds the scoped key. The workload serves A2A on localhost (or a Unix socket) and points its model and MCP URLs at the chassis proxies |
| `remote` | Untrusted workloads, third-party images, managed runtimes | The workload runs in its own pod under gVisor with egress denied, or at its own endpoint. It reaches the chassis's remote proxy with a per-remote bearer token (mTLS is not built). Plain-A2A mode fronts an agent that emits no chassis events |
| `inprocess` | The chassis's own tests and local runs only | The template A2A server runs inside the chassis process over an in-memory ASGI transport. A workload's own tests never use it; they run the chassis as a separate process in the `fake` profile (`agent-testkit`) |

A workload is trusted only if the owning team wrote and reviewed it, its image is built in our registry, and it does not run code, shell commands, or file writes itself. Generated code that runs through the code-execution tool (`packages/code-runner`, one gVisor sandbox per call) does not make an agent untrusted. Prompt injection does not either.

## The two hard requirements

1. **Only the chassis holds credentials.** Every internal service (LiteLLM, the MCP gateway, the broker, the state store, the config store) requires a credential the chassis alone holds. The workload has no service account token (`automountServiceAccountToken: false`), no route to the metadata service or the API server (egress deny), and the chassis's public port binds to the pod IP, so localhost carries only the proxies.
2. **Every lane is tested on every commit.** The contract suite runs each case over A2A on localhost, as in production, and over A2A in memory, as in the chassis's tests. CI also runs a fake workload through the `remote` lane.

## Stateless replicas

A replica keeps nothing a retry needs, so the service scales by adding pods.

- **Idempotency and leases** live in `StatePort` (`chassis/server/idempotency.py`): a repeated `idempotency_key` replays the same result from any replica, with `Idempotent-Replayed: true`; a claim is fenced at 80% of its lease, and a retry after a dead owner runs once.
- **Config comes from the store** (`chassis/server/config_loader.py`): a change takes effect on the next request with no restart; a bad document is refused and the last good one stays; a bad document at startup fails startup.
- **`/ready` and drain** (`readiness.py`, `lifecycle.py`): a draining replica should fail no request. **Not met by the chart today**: it stops the workload first while the chassis still answers `/ready` 200 ([ADR-012](../planning/adr/012-drain-order-on-rollout.md), P-29).
- **Events go out through a broker client** behind `EventPort`, not through Dapr: CloudEvents 1.0, at least once, with a `.dlq` topic.
- The chassis and the workload share a pod, so they add replicas together.

## The deploy shape

The `agent-lib` Helm library chart (`deploy/helm/agent-lib`) is the one way a service is deployed. Every service includes it.

- The chassis image tag is set once in `agent-lib.defaults`. A service may pin an older tag. `latest` is refused. Releases are meant to roll out in rings with no service rebuild. **Rings are not built** ([056 CH-7](../planning/issues/056-CH-7-chassis-release-rings.md)): today a tag bump reaches every service at once, and one bad image can too.
- `sidecar` lane: the chassis runs as a native sidecar (an init container with `restartPolicy: Always`). Ports: 8080 public, 8090 the proxies, 8091 the remote proxy; the workload on 127.0.0.1:9000.
- `remote` lane: a second pod for the workload under `runtimeClassName: gvisor`, a token secret, a fixed proxy ClusterIP.
- Secrets reach the chassis container only (`secretEnv`, `envFromSecrets`, `secretVolumes`). The workload gets plain env from an allow-list, and credential-looking names are refused.
- Fixed in the chart: uids 10001 and 10002, a read-only root, all capabilities dropped, seccomp `RuntimeDefault`, no service account token, a memory-backed `/tmp`.
- NetworkPolicy, ingress and egress, per pod, default deny: the chassis pod takes traffic on its port only and reaches DNS plus named egress peers, each by namespace and pod labels and a port, never an `ipBlock` or an empty selector; a remote workload pod reaches the chassis's remote proxy only, with no DNS; each namespace the chart renders pods into gets a default deny of its own.
- The Service exposes the chassis port only. An HPA on CPU is optional.
- The admission rules (lane and trust, the minimum chassis version) are the chart's: `agent-lib.admission` renders the trust-rule `ValidatingAdmissionPolicy`, its binding to every namespace labeled `agents.platform/admission: enforce`, and its params; the platform installs them once per cluster through `deploy/helm/agent-admission`, and a service cannot switch them off. The chassis tags the rule allows are the library chart's, bumped with the tag. The PoC-5 cluster applies the same policy from `deploy/kind/poc05/admission/`, held equal by a test, and proves it in CI.
