"""Reuse section per issue: epic_id -> bullets. `tools/sync.py` writes them as `## Reuse` before `## Out of scope`."""
R = {
    'G-1': [
        '**Use:** the LiteLLM proxy, as planned.',
        '**Watch:** pin LiteLLM by hash. PyPI releases 1.82.7 and 1.82.8 were malicious (2026-03-24). Some governance features are Enterprise-only.',
    ],
    'H-2': [
        '**Use:** FastAPI to host the HTTP adapters, and the request and response types from the official `openai` and `anthropic` Python SDKs, so no format schema is written by hand.',
        "**Build:** the three inbound adapters, each mapping to one canonical request; the event-to-SSE mapping per format; `/health`, `/ready`, and graceful shutdown. Managed runtimes are not hosted: they are reached through the `remote` lane (ADR-001).",
        '**Watch:** LiteLLM can translate Anthropic calls to the OpenAI format, but the chassis owns every input protocol, so LiteLLM is not used as the Anthropic front door.',
    ],
    'H-1': [
        '**Use:** uv, FastAPI, and Pydantic, as planned. JSON Schema (draft 2020-12) for the event model, so a workload in any language can check its events.',
        '**Build:** the `chassis` package, the generic chassis image with the `chassis serve` launcher, the ports, the `inprocess` connector, and the event schema.',
    ],
    'CH-1': [
        '**Use:** the official `a2a-sdk` 1.x: its client in the `sidecar` connector, and its server in the service template to wrap `handle`. The same client serves the `remote` lane later (055 CH-6).',
        '**Build:** the connector interface, the `inprocess` and `sidecar` connectors, the mapping between chassis events and A2A task updates, and the template A2A server.',
        '**Watch:** a2a-sdk 1.x is a new major version, so pin it.',
    ],
    'H-3': [
        '**Use:** LiteLLM virtual keys: one per service, scoped to its routes and budget. The router attributes spend by key.',
    ],
    'CH-2': [
        '**Use:** the official `openai` and `anthropic` SDK types, already used by 011 H-2, and `httpx` streaming for the pass-through.',
        '**Build:** a thin proxy: it adds the scoped key, counts tokens, traces the call, and applies per-call budgets. It does no routing of its own.',
    ],
    'CH-3': [
        '**Use:** a Helm library chart, Kubernetes native sidecar containers (stable since 1.33), and Kubernetes resource requests sized from the load test (122 X-5).',
        '**Build:** the chart templates, the chassis tag set once, and the per-service pin.',
        '**Watch:** a cluster webhook could inject the chassis instead, but it is hidden and harder to debug (ADR-001).',
    ],
    'CH-4': [
        '**Use:** LiteLLM virtual keys, the LiteLLM MCP gateway per-key tool grants, broker ACLs, Valkey ACLs, bucket policies, and Kubernetes NetworkPolicy with a CNI that enforces it (for example Cilium or Calico).',
        '**Build:** the per-service credential set, the default-deny egress policy in the library chart, and the negative tests from the workload container.',
        '**Watch:** per-key guardrails in LiteLLM are Enterprise-only.',
    ],
    'CH-5': [
        '**Use:** FastAPI routing and Pydantic models generated from each operation\'s JSON Schema.',
        '**Build:** the operation declarations in config, the router, and the refusals for what the chassis cannot carry.',
    ],
    'CH-6': [
        '**Use:** the a2a-sdk client from 009 CH-1; `kubernetes-sigs/agent-sandbox` and a gVisor RuntimeClass for sandboxed pods; AWS SigV4 or OAuth 2.0 for AgentCore and Google auth for Vertex AI Agent Engine, per the first cloud; Kyverno or ValidatingAdmissionPolicy for the admission check.',
        '**Build:** the `remote` connector, one cloud auth adapter, the admission policy, and the CI job with a fake remote workload.',
        '**Watch:** agent-sandbox reached v1.0 in 2026, so pin it. On EKS, gVisor or Kata must be installed on the nodes.',
    ],
    'CH-7': [
        '**Use:** Argo CD, as planned. suggested: Argo CD sync waves or Argo Rollouts for the rings. Kyverno or ValidatingAdmissionPolicy for the minimum-version rule, in the same policy set as 055 CH-6.',
        '**Build:** the ring order, the promotion checks, and the pin and rollback steps.',
    ],
    'CH-8': [
        '**Use:** the OpenInference instrumentors as a reference for which events each framework exposes. The a2a-sdk for JavaScript (or another language) for the non-Python workload.',
        '**Build:** one event mapping per supported framework, a contract test per mapping, and the non-Python example.',
    ],
    'H-19': [
        '**Use:** Kyverno, in the same policy set as the trust and minimum-version rules (055 CH-6, 056 CH-7).',
    ],
    'H-11': [
        '**Scope change:** superseded in its first form by ADR-001: one codebase, one image, one version, and no SDK. Other projects reuse the stock chassis image in front of their own A2A workload.',
    ],
    'X-1a': [
        '**Use:** Kubernetes 1.33 or later, a gVisor RuntimeClass (GKE Sandbox on GCP; gVisor or Kata on EKS nodes), a CNI that enforces NetworkPolicy, and Workload Identity or IRSA for the remote-lane auth adapter.',
    ],
    'H-5': [
        "**Use:** the official `a2a-sdk` 1.x (A2A spec 1.0, `[fastapi]` extra), the same SDK the connectors (009 CH-1) already use, and LiteLLM's `/a2a` gateway for access control, logging, and spend per agent.",
        '**Build:** the agent card, generated from config, and its signature.',
        "**Watch:** PydanticAI's own A2A helpers wrap the framework agent, not `handle`, so they do not emit the chassis event schema. The chassis and the template use `a2a-sdk`.",
    ],
    'H-6': [
        '**Use:** LiteLLM guardrails: Presidio PII masking before and after model calls and before MCP calls, and prompt-injection checks on user input and tool outputs. Llama Prompt Guard 2 as the injection classifier. One LiteLLM virtual key per service (models, budgets, rate limits), held only by the chassis container. The Presidio library in the chassis for redaction before logging and publishing.',
        '**Build:** caller auth and scopes (OAuth2/OIDC from the company IdP, and API keys), input size limits, and rate limits in Valkey.',
        '**Watch:** per-key guardrail control in LiteLLM is Enterprise-only, but ADR-001 item 6 assumes guardrails per key; see the gaps in 000-plan.md. Prompt Guard 2 reads 512 tokens at a time, so long tool outputs are split. Presidio images now come from ghcr.io.',
    ],
    'H-7': [
        '**Use:** the OpenTelemetry SDK with OpenInference instrumentors (they cover PydanticAI, LangGraph, OpenAI Agents SDK, Claude Agent SDK, smolagents, Google ADK, LiteLLM, and MCP), an OTel Collector, Langfuse v4 for LLM traces (`/api/public/otel`, HTTP only), and Prometheus, Grafana, Loki, and Tempo for the rest.',
        '**Build:** chassis spans, trace context passed to the workload over A2A so one trace covers both containers, the JSON log format with `request_id` and `trace_id`, and user and session IDs on spans through OTel baggage. In the `sidecar` lane, the OpenInference instrumentors run in the workload process.',
        '**Watch:** the OTel GenAI conventions are still in "Development" status. OpenInference uses its own attribute names; Langfuse reads both.',
    ],
    'H-10': [
        '**Use:** AWS AgentCore CLI and Google agents-cli as reference layouts (both scaffold agent projects with CI). Helm, Argo CD, cosign, and Kyverno, as planned. The shared Helm library chart from 024 CH-3 adds the chassis container.',
        '**Build:** the service template: the workload skeleton, the A2A server that wraps `handle`, a Dockerfile for the workload only, and the chart that includes the library chart.',
        '**Scope change:** the library chart was split out into 024 CH-3, and the credentials and egress rules into 026 CH-4.',
    ],
    'H-13': [
        '**Use:** FastMCP 4. `FastMCP.from_fastapi()` (or its OpenAPI provider) turns the OpenAPI spec into MCP tools, mounted in FastAPI with `http_app()`.',
        '**Build:** curated route maps (which operations become tools), and `/manifest` built from config plus the OpenAPI spec. The spec includes the service\'s declared operations (050 CH-5), and `/manifest` reports the chassis version, the lane, and the trust value.',
        '**Watch:** FastMCP 4 broke the 3.x APIs, so pin it. Do not use fastapi-mcp (no release since 2025-07).',
    ],
    'H-15': [
        '**Use:** suggested: Copier for the template, so existing agents can pull template updates. Chassis updates do not come this way: they come from the library chart\'s tag. The AgentCore CLI is a reference for the command shape.',
        '**Build:** `agentctl new` on top of the template.',
    ],
    'H-16': [
        '**Use:** vLLM tool parsers and guided decoding (already in the stack), and the LiteLLM MCP gateway (`/mcp`) for per-key tool allow-lists. The gateway\'s allow-list is the hard limit; the chassis config allow-list is a second check.',
        '**Build:** the tool port, the MCP tool proxy that workloads point their MCP client at, the JSON fallback mode, and passing `idempotency_key` to write tools.',
    ],
    'H-17': [
        '**Use:** Dapr pub/sub. The chassis publishes through the pod\'s Dapr sidecar, and Dapr wraps the payload in a CloudEvents envelope. The CloudEvents Python SDK 2.x for the extensions (pin it; 2.x is new).',
        '**Build:** the `EventPort` over the Dapr publish API, payload schemas, extensions, and the `run_id` partition key through Dapr metadata (check per broker).',
        '**Scope change:** "the first broker adapter" becomes the Dapr pub/sub component for the broker chosen in DEC-1.',
        "**Watch:** Dapr's NATS JetStream component is beta. Kafka is stable. Dapr is a third container in the pod, and its localhost API can be reached by the workload: turn on Dapr API-token auth with the token in the chassis container only, or use a broker client in the chassis (see the gaps in 000-plan.md).",
    ],
    'H-20': [
        '**Use:** Dapr subscriptions. Dapr delivers each CloudEvent to an HTTP route on the chassis port, never the workload\'s, and the HTTP status acknowledges or retries it.',
        '**Build:** the mapping from event to `TaskInput` and `Context`, and the reply events.',
    ],
    'H-21': [
        '**Use:** AsyncAPI 3.1, with the AsyncAPI generator (Node) for docs and validation.',
        '**Build:** the AsyncAPI file, generated from `spec.events` in config. Dapr does not generate it.',
        '**Watch:** FastStream generates AsyncAPI, but it supports neither SQS, Pub/Sub, nor CloudEvents, so it is not used.',
    ],
    'H-22': [
        '**Use:** Dapr resiliency policies (retries with backoff) and a `deadLetterTopic` per subscription.',
        '**Build:** the duplicate drop (through 018 H-18), the replay command, and metrics.',
        '**Watch:** retry behavior differs slightly per broker. Test each one.',
    ],
    'H-23': [
        '**Use:** Dapr pub/sub components: Kafka, AWS SNS/SQS, and GCP Pub/Sub are stable; NATS JetStream is beta.',
        '**Build:** one component file per broker, and the shared contract suite with local emulators.',
        '**Scope change:** no hand-written broker adapters.',
    ],
    'S-2': [
        '**Use:** vLLM offline batch inference with the open-weight teacher, and HF datasets for storage. Optional: distilabel pipelines (now community-maintained). Presidio for PII removal.',
        '**Build:** the teacher prompts, and the filters that use the S-3 metrics.',
    ],
    'S-5': [
        '**Use:** Unsloth (Apache 2.0 core; do not ship its AGPL Studio UI) or TRL 1.x (SFT, DistillationTrainer), with MLflow autologging.',
        '**Build:** the pipeline config, seeds, and the data-version wiring.',
    ],
    'S-7': [
        '**Use:** vLLM 0.30 with multi-LoRA (many fine-tuned adapters on one base model) and guided decoding. LoRA resolver plugins load adapters from local disk or S3.',
        "**Watch:** vLLM's docs call runtime LoRA loading a risk outside fully trusted setups. Load adapters only from our own storage.",
    ],
    'M-1': [
        '**Use:** self-hosted MLflow 3: tracking, the model registry, and eval datasets.',
        '**Build:** the links from each model to its data version and scores.',
    ],
    'M-4': [
        '**Use:** KServe `LLMInferenceService` canary traffic split on Kubernetes. suggested: LiteLLM route weights in Docker Compose.',
        '**Build:** the rollback command and the promotion event.',
    ],
    'D-0': [
        '**Use:** Postgres on CloudNativePG as the record store, per the epic. suggested: also link each record to its Langfuse trace, so review and datasets can use it (see 064 D-1).',
    ],
    'D-1': [
        '**Use:** Langfuse datasets (versioned), LLM-as-judge evaluators, and annotation queues. All three are in the MIT self-hosted edition.',
        '**Build:** the glue: copy recorder records into Langfuse, send uncertain scores to a queue, and publish the review events.',
        '**Decision:** confirm Langfuse as the golden set workspace at the start of this issue. If not, keep the design in What.',
    ],
    'D-2': [
        '**Use:** Langfuse annotation queues as the review UI. Label Studio (Apache 2.0) if richer edit screens or stricter roles are needed.',
        '**Build:** SSO login and roles, the bridge that publishes `oversight.review.decided.v1`, and the append-only review history.',
        '**Scope change:** no custom web UI.',
        '**Watch:** check which role and audit controls the MIT self-hosted Langfuse includes. Our sources disagree; if they are not enough, use Label Studio.',
    ],
    'D-3': [
        '**Use:** Langfuse dataset import (CSV, JSONL, JSON Schema checks), and HF datasets for files and bucket reads.',
        '**Build:** the bucket watch and the API wrapper.',
    ],
    'D-4': [
        '**Use:** HF datasets (JSONL, Parquet, `push_to_hub`) and pandas for CSV.',
        '**Build:** the export job by version ID.',
    ],
    'D-7': [
        '**Decision:** lakeFS moved to the Business Source License 1.1 from v1.87.0 (2026-09-22). Unmodified internal use is allowed. Options: keep lakeFS and accept the license, use DVC (Apache 2.0, Git-based), or use Langfuse dataset versions if 064 D-1 moves to Langfuse. Record the choice in DEC-1.',
    ],
    'C-3': [
        '**Use:** S3 Object Lock (or GCS retention lock) for append-only storage, and a Dapr subscription on the chassis port for the events.',
        '**Build:** the data agent and the export API.',
    ],
    'R-1': [
        '**Use:** the MCP Registry `server.json` format and API shape, and A2A agent cards, as record formats. Postgres 17 + pgvector, per the epic.',
        '**Decision:** evaluate ToolHive Registry Server (Apache 2.0, implements the MCP Registry API, pre-1.0 project) as the base. Use AWS Agent Registry only if the platform goes AWS-native.',
        '**Build:** our extra fields (trust, governance, fallbacks, metrics) and the change events.',
        '**Watch:** the official MCP Registry code is not built for self-hosting. Copy its API, not its code.',
    ],
    'R-4': [
        '**Use:** the MCP Python SDK client for `tools/list`.',
        '**Build:** the mapping from tools to registry entries.',
    ],
    'R-8': [
        '**Use:** Snyk Agent Scan in CI (tool poisoning, shadowing, toxic flows), and Llama Prompt Guard 2 as an in-house classifier on descriptions.',
        '**Build:** the scan at registration and the review flag.',
        '**Watch:** Snyk Agent Scan sends component data to Snyk and runs the servers it scans, so run it in a sandbox and keep the in-house check as the gate.',
    ],
    'R-11': [
        "**Use:** pgvector (dense vectors plus BGE-M3 sparse vectors), and Qwen3-Embedding and Qwen3-Reranker served by vLLM, all per the epic. ToolHive's `find_tool` search is a reference.",
        '**Build:** reciprocal rank fusion and the reranking step.',
    ],
    'R-14': [
        "**Use:** FastMCP 4, generating tools from the registry's OpenAPI spec, the same way as 051 H-13.",
    ],
    'P-1': [
        '**Use:** FastMCP 4, and the LiteLLM MCP gateway to put agent MCP endpoints behind one URL.',
    ],
    'P-4': [
        '**Use:** LiteLLM MCP gateway per-key tool allow-lists and OAuth, and the MCP `destructiveHint` tool annotation for confirmations. Option: agentgateway (per-tool CEL rules; v1.0 in March 2026, so newer).',
        '**Build:** the confirmation flow and the audit events.',
    ],
    'O-1': [
        "**Use:** Temporal, with the worker in the chassis. suggested: each step calls the workload through the connector. The framework integrations (OpenAI Agents SDK, PydanticAI `TemporalDurability`, the LangGraph plugin) run in the workload and need a Temporal credential, which ADR-001 hard requirement 1 rules out in the `sidecar` lane; see the gaps in 000-plan.md.",
        '**Build:** the registry search step and the orchestrator config.',
    ],
    'O-2': [
        '**Use:** Temporal. Its event history is the checkpoint, and resume is a replay. suggested: the Temporal Helm chart with Postgres persistence.',
        '**Build:** the workflow and activity definitions.',
    ],
    'O-3': [
        '**Use:** the KEDA Temporal scaler (task-queue backlog, since KEDA 2.17), and the Kafka or NATS lag scalers.',
        '**Watch:** scale-to-zero can cut in-flight work (kedacore/keda#7368). Keep a minimum of one replica for busy pools, and rely on graceful shutdown. The chassis and the workload scale together, and a cold start includes both containers.',
    ],
    'O-8': [
        '**Use:** Temporal signals and updates for approve and cancel.',
        "**Watch:** HumanLayer's open-source SDK is deprecated, so it is not used.",
    ],
    'O-11': [
        '**Use:** the CNCF Serverless Workflow DSL 1.0 as the model for the YAML shape.',
        '**Build:** the thin interpreter from our workflow YAML to Temporal workflows.',
    ],
    'C-4': [
        '**Use:** the HF model card template, CycloneDX 1.7 ML-BOM for model and dataset bills of materials, and MLflow model registry metadata.',
        '**Build:** the generator that fills them from config, OpenAPI, AsyncAPI, and MLflow.',
        '**Watch:** Google Model Card Toolkit is archived.',
    ],
    'C-9': [
        '**Use:** buy Vanta or Drata (ISO 42001 frameworks) for the audit workflow and evidence tracking. VerifyWise (Business Source License, self-hosted) if the tool must be self-hosted.',
        '**Build:** the export of platform evidence into the chosen tool.',
    ],
    'X-2': [
        '**Use:** NVIDIA GPU Operator, per the epic, and KServe or llm-d for model serving on Kubernetes (pick one).',
    ],
    'X-8': [
        '**Use:** Kafka through Strimzi or a managed service (MSK, Confluent), or the NATS JetStream Helm chart. With Dapr, Kafka is the stable path.',
    ],
}
