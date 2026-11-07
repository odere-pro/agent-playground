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

- **Event broker:** NATS JetStream (the epic's recommendation) or the company's existing Kafka. Note retention needs and who runs the broker. Dapr is decided: [ADR-004](../adr/004-events-through-a-broker-client.md), accepted 2026-10-08, puts a broker client in the chassis behind `EventPort` (see [the Dapr note](../../../pocs/poc-04-stateless-scalable/notes/2026-10-01-dapr-vs-broker.md)). The broker product stays open: ADR-004 does not pick between NATS JetStream and Kafka. A NATS adapter must pass the bar in ADR-004 item 8.
- **First cloud:** AWS or GCP, for staging and prod. It must give the cluster needs PoC-5 proved: a gVisor RuntimeClass, a CNI that enforces NetworkPolicy, ValidatingAdmissionPolicy, and Kubernetes 1.33 or later. The `remote`-lane auth no longer depends on the cloud. Decided in [ADR-005](../adr/005-remote-lane-auth-and-trust-admission.md): one bearer token per remote.
- **A2A:** A2A to workloads is decided by ADR-001: A2A is the one contract between the chassis and every workload. Still open: whether the public A2A endpoint ([061 H-5](061-H-5-a2a-adapter.md)) ships in version 1.
- **The north star:** "good tasks per week" or "agents in production on the chassis", with draft targets for its inputs ([product plan](../../plans/2026-11-06-agent-container-product.md), open item 1).
- **The primary trace view per environment:** LangSmith or Langfuse, and where the score goes when both run (product plan open item 2; ADR-007 open question 4; ADR-009 open question 5).
- **Reuse stack:** accept or replace the picks in the [reuse analysis](../poc/010-reuse-analysis.md). The main calls: Langfuse as the golden set workspace, lakeFS under its new license or DVC, and our own registry on Postgres or ToolHive as the base. suggested: accept Langfuse (ADR-010 item 2), defer lakeFS or DVC to 069 D-7, and defer the registry base to 076 R-1. See gap (c) in the [backlog plan](000-plan.md#adr-001-follow-ups).
- **Choices ADR-001 leaves to planning:** Kyverno or ValidatingAdmissionPolicy for the admission rules; gVisor or Kata for `remote` pods; which managed runtime and which non-Python workload the MVP fronts; agent-sandbox or E2B for the code-execution tool; and whether per-key guardrails need LiteLLM Enterprise (gap (d) in the backlog plan). Decided in ADR-005 (items 4, 6), ADR-006, and ADR-008 (item 4, S10).
- **Owners for the epic's live dependencies:** cloud account, golden set reviewer, legal and compliance owner.

## Out of scope

- Building anything. This issue only records decisions.
- Legal classification of use cases. Legal makes that decision; the platform records it.
- The epic's savings targets (big-model token spend −30%, `facts_kept` ≥ 0.95, fallback rate < 10%, registry top-3 ≥ 90%, run recovery 100%). They stay drafts, parked with the SLM track and 005 G-3.
- The first banned-word list for 027 S-1. Parked with the SLM track.
- Where the router runs before the cloud exists, so the baseline counts real traffic. Parked with the SLM track. For the product, the router runs in every deploy stack (`deploy/compose/poc08-litellm/`, `deploy/kind/poc05/platform/`).
- Owners for GPU access and for real outputs for training data. Parked with the SLM track.
- The chassis delivery model. ADR-001 settles it.

## Acceptance criteria

- [ ] Each of the four decisions is recorded with an owner, a date, and a short reason (a short ADR or a comment on this issue). ADR-001 is accepted and linked from here.
- [ ] The broker choice is written into 019 H-17, 020 X-8, and 060 H-23.
- [ ] The first cloud is written into 038 X-1a, with the cluster needs PoC-5 proved (gVisor RuntimeClass, a CNI that enforces NetworkPolicy, ValidatingAdmissionPolicy, Kubernetes 1.33 or later).
- [ ] The priority of the public A2A endpoint (061 H-5) is confirmed: P1 if it ships in version 1, P2 if not.
- [ ] The north star is chosen, with draft targets for its inputs (product plan, open item 1).
- [ ] The primary trace view per environment is chosen, LangSmith or Langfuse, with where the score goes when both run (product plan open item 2, ADR-007 open question 4, ADR-009 open question 5).
- [ ] Each live epic dependency has a named owner: cloud account, golden set reviewer, legal and compliance.
- [x] The Dapr decision from PoC-4 is recorded: a broker client in the chassis, or Dapr with its localhost API closed to the workload. · Delivered in PoC-4: `pocs/poc-04-stateless-scalable/notes/2026-10-01-dapr-vs-broker.md`; [ADR-004](../adr/004-events-through-a-broker-client.md) Decision 1, accepted 2026-10-08 (a broker client in the chassis behind `EventPort`).
- [ ] Each other reuse pick is accepted or replaced, and the affected issues are updated: Langfuse as the golden set store, lakeFS or DVC, and the registry base.
- [x] The choices ADR-001 leaves to planning are recorded: Kyverno or ValidatingAdmissionPolicy; gVisor or Kata; which managed runtime and which non-Python workload the MVP fronts; agent-sandbox or E2B for the code-execution tool; and whether per-key guardrails need LiteLLM Enterprise. · Delivered in PoC-2, PoC-5, PoC-6b: ValidatingAdmissionPolicy, no Kyverno (ADR-005 item 4; `pocs/poc-05-sandboxed/demo/2026-10-09-demo-sandboxed.md` section 5); gVisor (`pocs/poc-05-sandboxed/notes/2026-10-09-gvisor-overhead.md`); no managed runtime, kagent-adk in plain-A2A mode (`pocs/poc-06b-bake-off-remote-lane/notes/2026-10-09-kagent-probe.md`; ADR-006, AgentCore and Vertex out of scope on 2026-10-09); echo-typescript in the sidecar lane (`pocs/poc-02-two-engines-one-contract/tests/test_lanes.py::test_typescript_echo_gives_the_same_stream_as_echo_python`); agent-sandbox, one sandbox per call (ADR-005 item 6; `pocs/poc-05-sandboxed/notes/2026-10-09-close-runs.md`); guardrails per route in open-source LiteLLM, no Enterprise now (ADR-008 item 4 and S10).

## Dependencies

- Depends on: none
- Blocks: [019 H-17](019-H-17-event-port.md), [020 X-8](020-X-8-event-broker.md), [027 S-1](027-S-1-rewrite-rules.md), [038 X-1a](038-X-1a-terraform-first-cloud.md), [061 H-5](061-H-5-a2a-adapter.md)

## References

- Epic story: [Open decisions](../slm-agent-platform-epic-v3.md#open-decisions)
- Epic context: [Open decisions](../slm-agent-platform-epic-v3.md#open-decisions) · [Success metrics](../slm-agent-platform-epic-v3.md#success-metrics) · [D.1](../slm-agent-platform-epic-v3.md#d1) · [H](../slm-agent-platform-epic-v3.md#app-h)
- Backlog plan: [000-plan.md](000-plan.md)
