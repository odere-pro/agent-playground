---
title: "DEC-1: Resolve the open decisions that block the backlog"
labels: ["decision", "priority:P0", "phase:cross", "area:planning", "size:S", "layer:platform"]
milestone: "W0 Decisions"
index: 1
epic_id: DEC-1
depends_on: []
blocks: ["019 H-17", "020 X-8", "027 S-1", "038 X-1a", "061 H-5"]
epic_refs: [open-decisions, success-metrics, D.1, H]
---

## Why

The epic lists four open decisions. Each one blocks work in the first waves: the event port, the first cloud, the public A2A endpoint, and the training data rules. Deciding them in week 0 keeps those issues from stalling. This is the only P0 issue that is not code.

[ADR-001](../adr/001-chassis-delivery-model.md) is accepted. It settles how the chassis runs next to a service, so that is no longer open. It leaves a few smaller choices to planning, and this issue records them too.

## What

Decide and write down, with an owner and a date for each:

- **Event broker:** NATS JetStream (the epic's recommendation) or the company's existing Kafka. Note retention needs and who runs the broker.
- **First cloud:** AWS or GCP, for staging and prod. The first cloud also picks the `remote`-lane auth adapter (SigV4 or OAuth 2.0 for AWS Bedrock AgentCore, Google auth for Vertex AI Agent Engine) and the sandbox runtime for `remote` pods.
- **A2A:** A2A to workloads is decided by ADR-001: A2A is the one contract between the chassis and every workload. Still open: whether the public A2A endpoint ([061 H-5](061-H-5-a2a-adapter.md)) ships in version 1.
- **Banned-word list and success-metric targets:** a first version of the list, and draft targets for the success metrics (big-model token spend −30%, `facts_kept` ≥ 0.95, fallback rate < 10%, registry top-3 ≥ 90%, run recovery 100%).
- **Reuse stack:** accept or replace the picks in the [reuse analysis](../poc/010-reuse-analysis.md). The main calls: Dapr for events (which makes Kafka the stable broker choice), Langfuse as the golden set workspace, lakeFS under its new license or DVC, and our own registry on Postgres or ToolHive as the base. Dapr or a broker client in the chassis: the PoC track's PoC-4 tries both behind `EventPort` and recommends one, and this issue records it. suggested: the broker client, because Dapr is a third container in the pod, with its own localhost API to close and its own release train. See gap (c) in the [backlog plan](000-plan.md#adr-001-follow-ups).
- **Where the router runs** before the cloud exists, so the baseline (005 G-3) counts real traffic.
- **Choices ADR-001 leaves to planning:** Kyverno or ValidatingAdmissionPolicy for the admission rules; gVisor or Kata for `remote` pods; which managed runtime and which non-Python workload the MVP fronts; agent-sandbox or E2B for the code-execution tool; and whether per-key guardrails need LiteLLM Enterprise (gap (d) in the backlog plan).
- **Owners for the epic's dependencies:** GPU access, cloud account, real outputs for training data, golden set reviewer, legal and compliance owner.
- Status after PoC-4: the Dapr decision is proposed in [ADR-004](../adr/004-events-through-a-broker-client.md): result events go through a broker client in the chassis behind `EventPort`, not through Dapr pub/sub. Measured on one Docker Desktop pair, `echo-python`, 3 runs of 60 s at 10 RPS per path: both paths delivered every event (Kafka 2,134 of 2,134, Dapr 2,137 of 2,137); the chassis's CPU at 10 RPS showed no resolved difference (none 0.116 to 0.141 vCPU, Kafka 0.120 to 0.174, Dapr 0.132 to 0.181; the spread between runs of one path is as large as the gap between paths); daprd used 0.0024 to 0.0037 vCPU and 34 to 151 MiB at 10 RPS, a 4x swing between runs with no known cause. Dapr fails two `EventPortContract` cases, kept as strict xfails: the dead-lettered event lacks `deadletterreason` and `deadletterattempts` and follows daprd's own retry count, not `max_attempts`; and there is one consumer group per app id. It also reads its subscriptions once at start, overwrites `traceparent`, saved no code (101 lines against 78 in the marked sections, plus 46 lines of component YAML), and after 9 settings and two tokens still leaves its metrics port and an internal gRPC port open on every interface. The broker product stays open here: ADR-004 does not pick between NATS JetStream and Kafka. PoC-4 used Kafka only because Dapr's Kafka component let both paths share one broker; a NATS adapter must pass the same `EventPortContract`. Not ticked until the owner accepts ADR-004. Evidence: [the Dapr note](../../../pocs/poc-04-stateless-scalable/notes/2026-10-01-dapr-vs-broker.md).

## Out of scope

- Building anything. This issue only records decisions.
- Legal classification of use cases. Legal makes that decision; the platform records it.
- Final metric targets. They are confirmed after the baseline (005 G-3).
- The chassis delivery model. ADR-001 settles it.

## Acceptance criteria

- [ ] Each of the four decisions is recorded with an owner, a date, and a short reason (a short ADR or a comment on this issue). ADR-001 is accepted and linked from here.
- [ ] The broker choice is written into 019 H-17, 020 X-8, and 060 H-23.
- [ ] The first cloud is written into 038 X-1a, with the `remote`-lane auth adapter and sandbox runtime it picks.
- [ ] The priority of the public A2A endpoint (061 H-5) is confirmed: P1 if it ships in version 1, P2 if not.
- [ ] A first banned-word list exists and is linked from 027 S-1.
- [ ] Success-metric targets are marked "draft", with a note that 005 G-3 makes them final.
- [ ] Each epic dependency has a named owner.
- [ ] Each reuse pick is accepted or replaced, and the affected issues are updated. The Dapr decision from PoC-4 is recorded: a broker client in the chassis, or Dapr with its localhost API closed to the workload.
- [ ] The choices ADR-001 leaves to planning are recorded: Kyverno or ValidatingAdmissionPolicy; gVisor or Kata; which managed runtime and which non-Python workload the MVP fronts; agent-sandbox or E2B for the code-execution tool; and whether per-key guardrails need LiteLLM Enterprise.
- [ ] The router's first home is decided.

## Dependencies

- Depends on: none
- Blocks: [019 H-17](019-H-17-event-port.md), [020 X-8](020-X-8-event-broker.md), [027 S-1](027-S-1-rewrite-rules.md), [038 X-1a](038-X-1a-terraform-first-cloud.md), [061 H-5](061-H-5-a2a-adapter.md)

## References

- Epic story: [Open decisions](../slm-agent-platform-epic-v3.md#open-decisions)
- Epic context: [Open decisions](../slm-agent-platform-epic-v3.md#open-decisions) · [Success metrics](../slm-agent-platform-epic-v3.md#success-metrics) · [D.1](../slm-agent-platform-epic-v3.md#d1) · [H](../slm-agent-platform-epic-v3.md#app-h)
- Backlog plan: [000-plan.md](000-plan.md)
