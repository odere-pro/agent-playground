# Decisions this agent is built on

The platform's architecture decisions (ADRs) live in the platform monorepo, not in this repo. Each line says what the decision means for this agent. The ADR wins where this line and it differ. How to apply them: [how to add business logic](how-to-add-business-logic.md).

| ADR | What it decides for this agent |
| --- | ------------------------------ |
| [ADR-001](https://github.com/odere-pro/agent-playground/blob/main/docs/planning/adr/001-chassis-delivery-model.md) | The chassis runs in front of this workload, in the `sidecar` lane by default; only the chassis holds credentials; the trust rule picks the lane. |
| [ADR-002](https://github.com/odere-pro/agent-playground/blob/main/docs/planning/adr/002-template-a2a-server-placement.md) | `handle` is dicts in and event dicts out, served by the template A2A server; the workload never imports `chassis`. |
| [ADR-005](https://github.com/odere-pro/agent-playground/blob/main/docs/planning/adr/005-remote-lane-auth-and-trust-admission.md) | In the `remote` lane one bearer token goes both ways; `spec.trust` is checked at config load and at admission. |
| [ADR-006](https://github.com/odere-pro/agent-playground/blob/main/docs/planning/adr/006-agent-engines-default-supported-lanes.md) | PydanticAI is the default framework; the supported frameworks and their lanes; smolagents and the Claude Agent SDK run in `remote` only. |
| [ADR-007](https://github.com/odere-pro/agent-playground/blob/main/docs/planning/adr/007-observability-otel-collector-langfuse.md) | Spans go over OTLP to one OpenTelemetry Collector, then to Langfuse and a second backend; forward `traceparent` and `baggage` on every call. |
| [ADR-008](https://github.com/odere-pro/agent-playground/blob/main/docs/planning/adr/008-security-identity-guardrails-defense-in-depth.md) | Caller auth and the guard stages are in the chassis, content limits in LiteLLM; the workload adds no security code of its own. |
| [ADR-009](https://github.com/odere-pro/agent-playground/blob/main/docs/planning/adr/009-feedback-langfuse-scores-event.md) | User feedback goes to `POST /v1/feedback` on the chassis and onto the run's trace; the workload exposes nothing for it. |
| [ADR-010](https://github.com/odere-pro/agent-playground/blob/main/docs/planning/adr/010-evals-offline-tool-online-gate.md) | DeepEval is the offline eval in CI; the online evaluator gate wraps the engine in the chassis, so `handle` must be safe to run twice. |

ADR-001 is the delivery model the whole PoC track tests. ADR-002 is from PoC-1. ADR-005 is from PoC-5. ADR-006 is from PoC-6. ADR-007 to ADR-010 are from PoC-7. ADR-006 to ADR-010 are Proposed, not yet accepted; check each one's Status line.

The wire contract these build on: [contract v7](https://github.com/odere-pro/agent-playground/blob/main/docs/contracts/contract-v7.md), with its frozen surface in [contract v5, part C](https://github.com/odere-pro/agent-playground/blob/main/docs/contracts/contract-v5.md).
