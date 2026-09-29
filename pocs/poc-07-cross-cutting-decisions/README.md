# PoC-7: Confirm the cross-cutting stack: security, observability, feedback, and evals

Status: not started
Planning doc: [007-PoC-7-cross-cutting-decisions.md](../../docs/planning/poc/007-PoC-7-cross-cutting-decisions.md)
Time box: 2–3 weeks

## Question

Does the chosen stack give security, observability, feedback, and evals to every engine, in every lane, added once at the chassis and LiteLLM, with no engine-specific code?

## Scope

- [ ] **Observability:** OpenInference instrumentors in the workload plus chassis spans, through an OTel Collector, into Langfuse v4. Check that one trace joins the chassis, the engine steps in the workload container, LiteLLM, and the tool calls, through `traceparent` passed over A2A. Put user and session IDs on spans through OTel baggage.
- [ ] **Security:** OAuth2/OIDC scopes in the chassis, and one scoped LiteLLM virtual key per service, held by the chassis. LiteLLM guardrails with Presidio (PII) and Llama Prompt Guard 2 (injection) on inputs, outputs, and tool outputs, with long tool outputs split into 512-token chunks. AI-generated marking on outputs. Snyk Agent Scan in CI, run in a sandbox.
- [ ] **Feedback:** `POST /v1/feedback` with `request_id`, score, label, and comment, written through the Langfuse scores API onto the call's trace. suggested: a new event `agents.feedback.received.v1`. It is not in the epic's event catalog yet, so it needs adding there.
- [ ] **Evals:** promptfoo and DeepEval, each tried on the same 20 golden cases on two engines, to pick one for CI. Langfuse datasets and experiments hold the cases and results. Online: the evaluator gate in the chassis, which scores, retries, then falls back. Compare an LLM judge with a small encoder model on cost and agreement.
- [ ] `AuthPort`, `GuardrailPort`, `EvaluatorPort`, and `FeedbackPort` are defined here, in the same shape as the PoC-1 ports, each with a fake and a contract suite. Then real adapters for them and for `TelemetryPort`, each passing the same suite as its fake.
- [ ] The chassis's own redaction of its logs and events is regex only (emails, phone numbers, IDs). Named-entity redaction runs in LiteLLM's guardrails and in the OpenTelemetry Collector log pipeline, never in the chassis, so the chassis stays within ADR-001's memory estimate.
- [ ] A red-team set: injection in the input, injection in a tool output, data exfiltration through a tool, and a secret read.
- [ ] One ADR per concern: the pick, where the hook lives (the chassis, LiteLLM, the MCP gateway, network policy, or the workload's event mapping), and what each engine must expose. The security ADR builds on [ADR-001](../../docs/planning/adr/001-chassis-delivery-model.md): the hard limits live in shared services, not in the chassis.

## Exit criteria

Each one has a scenario test in `tests/` or a recorded reason it cannot have one yet.

- [ ] Telemetry swap shown: the same spans reach Langfuse and a second OpenTelemetry backend by exporter config only.
- [ ] Each new real adapter passes the same contract suite as its fake.
- [ ] Four ADRs are accepted: observability, security, feedback, and evals.
- [ ] One trace joins the chassis and workload containers through `traceparent` over A2A.
- [ ] Each concern works on at least two engines with no engine-specific code outside the workload's event mapping.
- [ ] Anything that must live in the workload's event mapping is listed per engine.
- [ ] The red-team set has a pass rate recorded, and every miss has an owner.
- [ ] One eval tool is picked for CI, and it produces the same report format for every engine.

## How to run

```bash
make test-poc POC=07
```

## Demo

Not recorded yet. The script and its output go to `demo/`.

## Notes and decisions

Dated files in `notes/`. `notes/backlog-changes.md` lists what the backlog should change when this iteration closes.
