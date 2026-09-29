---
title: "H-5: A2A adapter: JSON-RPC endpoint, task states, signed agent card"
labels: ["story", "priority:P2", "phase:0-harness", "area:harness", "size:S", "layer:agent-profile"]
milestone: "W5 Chassis completion"
index: 61
epic_id: H-5
depends_on: ["001 DEC-1", "011 H-2"]
blocks: []
epic_refs: [G.1, B.5]
---

## Why

A2A lets agents from other systems call platform agents as agents, with task states and a signed card that says who they are. This issue is only the public A2A endpoint for outside callers. A2A from the chassis to its workloads already ships in 009 CH-1, because [ADR-001](../adr/001-chassis-delivery-model.md) makes it the one contract to workloads. 001 DEC-1 decides only whether the public endpoint ships in version 1. It is P2 until then; if it ships, this issue moves to P1. It needs only the inbound adapters (011 H-2) and reuses the `a2a-sdk` already in the chassis, so it can run in parallel with the rest of the wave.

## What

- A public `POST /a2a` JSON-RPC endpoint on the chassis port, built with the `a2a-sdk` the chassis already uses (009 CH-1). It runs the same pipeline and engine connector as every other inbound adapter.
- A2A task states mapped from the chassis event schema, with the same written mapping as 009 CH-1: `start` to working, `end` to completed, `error` to failed.
- Streaming through A2A, mapped from `delta` events.
- `GET /.well-known/agent-card.json`: an agent card generated from the agent config (name, description, task, endpoint, auth, version).
- The card is signed. Suggested: the signing key comes from Vault through External Secrets, mounted into the chassis container only.
- `interface.a2a` in config switches only the public endpoint and its card, for any agent class. The chassis's A2A calls to its workload do not depend on it.
- The same auth, scopes, limits, idempotency, and telemetry as the other adapters.
- Contract tests for the public A2A endpoint in the testing kit (015 H-8).

## Reuse

- **Use:** the official `a2a-sdk` 1.x (A2A spec 1.0, `[fastapi]` extra), the same SDK the connectors (009 CH-1) already use, and LiteLLM's `/a2a` gateway for access control, logging, and spend per agent.
- **Build:** the agent card, generated from config, and its signature.
- **Watch:** PydanticAI's own A2A helpers wrap the framework agent, not `handle`, so they do not emit the chassis event schema. The chassis and the template use `a2a-sdk`.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Deciding whether the public endpoint ships in version 1. That is 001 DEC-1.
- A2A from the chassis to its workloads (009 CH-1) and to `remote` workloads (055 CH-6).
- Finding A2A agents through the registry (076 R-1).
- The orchestrator calling agents over A2A (099 O-1).

## Acceptance criteria

- [ ] With `interface.a2a: true`, an outside A2A client sends a task to the echo agent and to the simplifier through the chassis's public endpoint, and gets the same output as `POST /v1/run`.
- [ ] Task states go from submitted to working to completed on success, and to failed on error.
- [ ] Streaming over A2A returns the same deltas as the native stream.
- [ ] The agent card is built from config and its signature verifies; a changed card fails verification.
- [ ] With `interface.a2a: false`, only the public A2A endpoints return 404. A2A between the chassis and its workload is unaffected, and the agent still answers on its other APIs.
- [ ] Public A2A calls enforce scopes, appear in traces, and pass the contract tests.

## Dependencies

- Depends on: [001 DEC-1](001-DEC-1-resolve-open-decisions.md), [011 H-2](011-H-2-inbound-adapters.md)
- Blocks: none

## References

- Epic story: [H-5 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.1](../slm-agent-platform-epic-v3.md#g1) · [B.5](../slm-agent-platform-epic-v3.md#b5)
- Backlog plan: [000-plan.md](000-plan.md)
