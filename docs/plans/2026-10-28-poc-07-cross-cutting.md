# PoC-7 cross-cutting decisions: work plan

Status: in progress
Date: 2026-10-28
Source: [007-PoC-7-cross-cutting-decisions.md](../planning/poc/007-PoC-7-cross-cutting-decisions.md). Tracking: [PoC-7 README](../../pocs/poc-07-cross-cutting-decisions/README.md).
Backlog informed: [022 H-6](../planning/issues/022-H-6-security-middleware.md), [014 H-7](../planning/issues/014-H-7-observability.md), [017 H-4](../planning/issues/017-H-4-harness-features.md), [037 M-2](../planning/issues/037-M-2-eval-gate-ci.md), [023 C-5](../planning/issues/023-C-5-ai-generated-marking.md).

The plan follows the planning doc. It adds no criterion. PoC-7 decides and proves the picks; PoC-8 builds them in for real (rate limits, Valkey counters, Vault, Grafana, the full C-5 rollout).

## Environment facts (2026-10-28)

- PyPI and npm answer through the proxy. Hugging Face answers 403. The Docker registries need auth, so no image runs in this container.
- So these run here: the OpenTelemetry SDK, Presidio with the spaCy small model, promptfoo, DeepEval, the chassis, the workloads, and stubs of Langfuse and the LiteLLM guardrail API.
- These do not: Langfuse itself, the OTel Collector, Tempo, LiteLLM's proxy with guardrails, and Llama Prompt Guard 2 (a gated model on Hugging Face). They run in CI on Compose (`poc07-compose.yml`) or on the Mac. Every number that needs them says so.

## Decisions (orchestrator, 2026-10-28; to confirm in the four ADRs)

1. **Four new ports, same shape as PoC-1.** `AuthPort.authenticate(token) -> Principal`, `GuardrailPort.check(text, stage) -> Verdict`, `EvaluatorPort.score(case) -> Score`, `FeedbackPort.submit(feedback)`. Each has a fake, a no-op default in `PortBundle` (so old bundles build), and a suite in `packages/contract-suites`. Adapters are picked by `spec.adapters.{auth,guardrails,evaluator,feedback}`.
2. **Real adapters.** `OtelTelemetry` (OpenTelemetry SDK, OTLP over HTTP), `OidcAuth` (JWT with JWKS, pyjwt), `LiteLLMGuardrails` (LiteLLM's `POST /guardrails/apply_guardrail`), `JudgeEvaluator` (an LLM judge through `ModelPort`), `EncoderEvaluator` (an HTTP classifier), `LangfuseFeedback` (Langfuse `POST /api/public/scores`). Each passes its port's suite offline against a stub of the service, and in `network` tests against the real one.
3. **Prompt Guard 2 sits behind LiteLLM's generic guardrail API.** LiteLLM's own `promptguard` hook is a vendor API, not Llama Prompt Guard. A small service, `packages/guardrail-server`, answers `POST /beta/litellm_basic_guardrail_api`, splits text into 512-token chunks, and runs the classifier. Presidio uses LiteLLM's built-in `presidio` hook.
4. **No new field in a frozen surface (contract v5, C.2).** `core/` and the five schemas stay byte for byte. Baggage, user and session ids travel as headers and span attributes, not as `Context` fields. AI-generated marking is a response header in PoC-7, not a `Response` field.
5. **The new event `agents.feedback.received.v1`** is a CloudEvent on `EventPort`, like the result events, with its own payload schema `feedback.v0.json`. The epic is never edited; the catalog change goes to `notes/backlog-changes.md`.
6. **The evaluator gate is an engine wrapper**, `GatedEngine`, built in the lifespan when `spec.harness.evaluator_gate` is on. Sync mode buffers, scores, retries, then falls back with `ctx.model_route = spec.model.fallback_route`. Async mode scores after the answer and writes the score to the trace. A stream is always async.
7. **Fan-out to two backends happens in the OTel Collector.** The chassis and the workload export OTLP to one endpoint. The swap and the fan-out are exporter config only.

## Work breakdown

| # | Work | Exit criteria | Agent |
| - | ---- | ------------- | ----- |
| W0 | The four ports, fakes, suites, bindings; `spec` sections; PoC README and tests skeleton | 2 | orchestrator |
| W1 | Observability: `OtelTelemetry`, run span on the request's trace id, baggage over A2A, `workload_a2a.telemetry`, OpenInference in two engines, the trace-join and swap tests, Collector and Compose config | 1, 2, 4, 5, 6 | `developer`, `observability-expert` |
| W2 | Security: `OidcAuth` and the scope middleware, `LiteLLMGuardrails`, `guardrail-server`, guard stages (input, tool output, output), regex redaction, AI-generated header, Snyk Agent Scan job, the red-team set | 2, 5, 7 | `developer`, `platform-security` |
| W3 | Feedback: `LangfuseFeedback`, `POST /v1/feedback`, request-to-trace lookup in `StatePort`, the feedback event | 2, 5 | `developer` |
| W4 | Evals: `JudgeEvaluator`, `EncoderEvaluator`, `GatedEngine`, 20 golden cases, promptfoo and DeepEval on two engines, the pick, Langfuse datasets | 2, 5, 8 | `developer`, `eval-expert` |
| W5 | Four ADRs, contract v6, the per-engine list, demo, backlog changes, README evidence | 3, 6 | orchestrator, `docs-editor` |

W1 to W4 run in parallel worktrees off W0 and merge into `poc-07/cross-cutting`.

## Verification

- Every merge: `make quick`. Before the push: `make check` and `make test-poc POC=07`.
- Real services: `network` tests and the `poc07-compose.yml` workflow. Measurements go to `notes/` with their commands.

## Risks

- Langfuse v4 on Compose needs six containers. If CI cannot hold it, the Langfuse half of criterion 1 waits on the Mac.
- Prompt Guard 2 is gated. Offline red-team numbers use a stand-in classifier and say so.
