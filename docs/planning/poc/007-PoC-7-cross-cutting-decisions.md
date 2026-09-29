---
title: "PoC-7: Confirm the cross-cutting stack: security, observability, feedback, and evals"
labels: ["poc", "priority:P0", "area:security", "area:observability", "area:evals", "type:decision"]
milestone: "Agent MVP"
index: 7
iteration: PoC-7
timebox: "2–3 weeks"
depends_on: ["PoC-6"]
backlog_refs: ["022 H-6", "014 H-7", "017 H-4", "037 M-2", "023 C-5"]
---

## Question

Does the chosen stack give security, observability, feedback, and evals to every engine, in every lane, added once at the chassis and LiteLLM, with no engine-specific code?

## Why

These four are what make an agent safe to run and able to get better. The reuse analysis already picked a mature tool for each, so this iteration does not compare tools from scratch. It confirms the picks on two engines, finds what cannot live outside the workload's event mapping, and writes the decisions down before PoC-8 builds them in for real.

**Open concern (review 2026-09-29), to explore until we align:** the online evaluator gate is the one pipeline stage that adds a model call to every request. The judge-versus-encoder comparison here should report cost and latency per gated call next to agreement, and try a sample rate and an asynchronous gate, so the gate's design is decided with numbers.

## Scope

- [ ] **Observability:** OpenInference instrumentors in the workload plus chassis spans, through an OTel Collector, into Langfuse v4. Check that one trace joins the chassis, the engine steps in the workload container, LiteLLM, and the tool calls, through `traceparent` passed over A2A. Put user and session IDs on spans through OTel baggage.
- [ ] **Security:** OAuth2/OIDC scopes in the chassis, and one scoped LiteLLM virtual key per service, held by the chassis. LiteLLM guardrails with Presidio (PII) and Llama Prompt Guard 2 (injection) on inputs, outputs, and tool outputs, with long tool outputs split into 512-token chunks. AI-generated marking on outputs. Snyk Agent Scan in CI, run in a sandbox.
- [ ] **Feedback:** `POST /v1/feedback` with `request_id`, score, label, and comment, written through the Langfuse scores API onto the call's trace. suggested: a new event `agents.feedback.received.v1`. It is not in the epic's event catalog yet, so it needs adding there.
- [ ] **Evals:** promptfoo and DeepEval, each tried on the same 20 golden cases on two engines, to pick one for CI. Langfuse datasets and experiments hold the cases and results. Online: the evaluator gate in the chassis, which scores, retries, then falls back. Compare an LLM judge with a small encoder model on cost and agreement.
- [ ] `AuthPort`, `GuardrailPort`, `EvaluatorPort`, and `FeedbackPort` are defined here, in the same shape as the PoC-1 ports, each with a fake and a contract suite. Then real adapters for them and for `TelemetryPort`, each passing the same suite as its fake.
- [ ] The chassis's own redaction of its logs and events is regex only (emails, phone numbers, IDs). Named-entity redaction runs in LiteLLM's guardrails and in the OpenTelemetry Collector log pipeline, never in the chassis, so the chassis stays within ADR-001's memory estimate.
- [ ] A red-team set: injection in the input, injection in a tool output, data exfiltration through a tool, and a secret read.
- [ ] One ADR per concern: the pick, where the hook lives (the chassis, LiteLLM, the MCP gateway, network policy, or the workload's event mapping), and what each engine must expose. The security ADR builds on [ADR-001](../adr/001-chassis-delivery-model.md): the hard limits live in shared services, not in the chassis.

## Reuse

- **Use:** OpenInference, OpenTelemetry Collector, Langfuse v4 (MIT), Presidio, Llama Prompt Guard 2, LiteLLM guardrails and virtual keys, Snyk Agent Scan, promptfoo or DeepEval.
- **Build:** the chassis spans, `/v1/feedback`, the online evaluator gate, and the red-team set.
- **Dropped:** Arize Phoenix (not OSI open source), LLM Guard (no release in over a year), Inspect AI (still beta).
- **Watch:** the OTel GenAI conventions are not stable yet. Check which role and audit controls the MIT self-hosted Langfuse includes.
- Details: [reuse analysis](010-reuse-analysis.md)

## Out of scope

- Production rollout of the picks (PoC-8).
- The golden set agent and review flow (backlog W6).
- mTLS, and cluster policies beyond the ones PoC-5 sets up (backlog W10).

## Demo

For two engines, one trace shows the whole call: chassis, engine steps in the workload, model calls, and tool calls. A feedback call shows up on that trace. The eval run compares the two engines on the same cases. The red-team set is blocked.

## Exit criteria

- [ ] Telemetry swap shown: the same spans reach Langfuse and a second OpenTelemetry backend by exporter config only.
- [ ] Each new real adapter passes the same contract suite as its fake.
- [ ] Four ADRs are accepted: observability, security, feedback, and evals.
- [ ] One trace joins the chassis and workload containers through `traceparent` over A2A.
- [ ] Each concern works on at least two engines with no engine-specific code outside the workload's event mapping.
- [ ] Anything that must live in the workload's event mapping is listed per engine.
- [ ] The red-team set has a pass rate recorded, and every miss has an owner.
- [ ] One eval tool is picked for CI, and it produces the same report format for every engine.

## Links

- Plan: [000-plan.md](000-plan.md) · Previous: [PoC-6](006-PoC-6-framework-bake-off.md) · Next: [PoC-8](008-PoC-8-build-cross-cutting.md)
- Decision it builds on: [ADR-001](../adr/001-chassis-delivery-model.md)
- Backlog issues this informs: [022 H-6](../issues/022-H-6-security-middleware.md), [014 H-7](../issues/014-H-7-observability.md), [017 H-4](../issues/017-H-4-harness-features.md), [037 M-2](../issues/037-M-2-eval-gate-ci.md), [023 C-5](../issues/023-C-5-ai-generated-marking.md)
- Epic: [B.2](../slm-agent-platform-epic-v3.md#b2), [G.2](../slm-agent-platform-epic-v3.md#g2), [G.3](../slm-agent-platform-epic-v3.md#g3), [E.5](../slm-agent-platform-epic-v3.md#e5), [R5](../slm-agent-platform-epic-v3.md#r5)
