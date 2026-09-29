---
title: "O-1: Orchestrator agent built from the harness, using registry search"
labels: ["story", "priority:P1", "phase:7-orchestrator", "area:orchestrator", "size:M", "layer:platform"]
milestone: "W9 Orchestrator"
index: 99
epic_id: O-1
depends_on: ["008 H-14", "092 R-12", "098 O-10"]
blocks: ["100 O-2"]
epic_refs: [Fig.3, F.6]
---

## Why

The orchestrator is the second agent class, and it must use the same interface and harness as every other agent. Building it from the harness template proves that one template serves all classes. It comes right after the schemas and registry search, because it reads its config from the config store and finds agents only through search.

## What

- Scaffolded with `agentctl new orchestrator --kind orchestrator`: class `orchestrator`, with the same endpoints, envelope, and harness (auth, config loader, budgets, observability) as every agent.
- Loads `agents/orchestrator.yaml` (`kind: OrchestratorConfig`) through the config loader.
- The outbound ports from Fig. 3: model port, registry port, agent port, memory port, and event port. The memory and event ports start as fakes. In the `sidecar` lane, each port is a chassis proxy or connector.
- Registry port: calls search with `registry.top_k` and `registry.min_trust`, and gets the main pick plus ranked fallbacks with difference metadata (R-12). Only these top matches are passed on, which keeps the planner prompt small.
- Agent port: calls any agent through the native envelope (`POST /v1/run`), passing `trace_id`, `idempotency_key`, and `budget`. Only the chassis holds the service credentials and the mTLS identity.
- A first single-step flow: take a task, find the main pick by `task`, call it, and on failure call the next ranked fallback.
- Temporal: suggested: the Temporal worker runs in the chassis and calls the workload once per step through the connector. Framework Temporal integrations are not used in the `sidecar` lane. This is gap (b) in [the gaps in the backlog plan](000-plan.md#adr-001-follow-ups).
- The process keeps no state. It registers its manifest and `governance` block in the registry on deploy, like any agent.

## Reuse

- **Use:** Temporal, with the worker in the chassis. suggested: each step calls the workload through the connector. The framework integrations (OpenAI Agents SDK, PydanticAI `TemporalDurability`, the LangGraph plugin) run in the workload and need a Temporal credential, which ADR-001 hard requirement 1 rules out in the `sidecar` lane; see the gaps in 000-plan.md.
- **Build:** the registry search step and the orchestrator config.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Temporal run state and checkpoints (100 O-2).
- Workflow files (103 O-11), routing rules and the planner (108 O-12).
- Agent pools (104 O-3), run events (112 O-16), and memory (113 O-9).

## Acceptance criteria

- [ ] `POST /v1/run` with `task: simplify_text` finds the simplifier through registry search and returns its output in the native envelope, streaming and complete.
- [ ] Entries below `min_trust` are never called, and the registry port never passes on more than `top_k` entries.
- [ ] When the main pick fails, the first ranked fallback is called, and the response `status` is `fallback`.
- [ ] One `trace_id` spans the orchestrator call and the agent call in the traces.
- [ ] The orchestrator passes the harness contract tests (H-8) and the stateless check in CI (H-19).
- [ ] On deploy, the orchestrator shows in the registry with its manifest and `governance` block.
- [ ] Orchestrator tests run offline through the `inprocess` connector, with the in-memory `RegistryPort`.

## Dependencies

- Depends on: [008 H-14](008-H-14-one-agent-interface.md), [092 R-12](092-R-12-main-pick-fallbacks.md), [098 O-10](098-O-10-orchestrator-schemas.md)
- Blocks: [100 O-2](100-O-2-temporal-checkpoints.md)

## References

- Epic story: [O-1 in Phase 7](../slm-agent-platform-epic-v3.md#phase-7-orchestrator)
- Epic context: [Fig. 3](../slm-agent-platform-epic-v3.md#fig3) · [F.6](../slm-agent-platform-epic-v3.md#f6)
- Backlog plan: [000-plan.md](000-plan.md)
