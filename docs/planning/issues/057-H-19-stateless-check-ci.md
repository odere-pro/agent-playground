---
title: "H-19: Stateless check in CI: fail the build on local disk writes or kept data"
labels: ["story", "priority:P1", "phase:0-harness", "area:harness", "size:S", "layer:chassis"]
milestone: "W5 Chassis completion"
index: 57
epic_id: H-19
depends_on: ["025 H-10"]
blocks: ["059 H-15"]
epic_refs: [B.2]
---

## Why

Agents scale by adding pods, so no agent may keep data on disk or in memory between calls. Code review alone does not catch this. A CI check in the template makes the rule hold for every agent, including the ones the factory (059 H-15) will create. Under [ADR-001](../adr/001-chassis-delivery-model.md), each pod runs two containers, the chassis and the workload, so the check covers both.

## What

- A CI step in the template repo (025 H-10) that runs on every agent build.
- Disk check: the contract tests run with a read-only root file system and no writable volumes, for both the chassis and the workload container. Any write fails the build.
- Kept-data check: the same request set runs against fresh pods and against one reused pod, in shuffled order, with the testing kit's fake model adapter. Outputs must match. It runs in the `sidecar` lane, so a reused workload container and a reused chassis container are both covered.
- Suggested: a lint rule that flags module-level mutable state and caches in the workload code.
- The library chart (024 CH-3) sets a read-only root file system on the chassis container. The service chart from the template sets it on the workload container.
- Suggested: a Kyverno policy that rejects agent pods in which any container lacks a read-only root file system.
- Data agents write only to their own store, never to local disk, so the disk check applies to every class.
- The failure message names the container, and the file written or the call that differed.

## Reuse

- **Use:** Kyverno, in the same policy set as the trust and minimum-version rules (055 CH-6, 056 CH-7).
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Idempotency (018 H-18).
- Joining this Kyverno policy into one policy set with the trust rule (055 CH-6) and the minimum-version rule (056 CH-7). The policy set comes with those issues.
- The agent factory CLI (059 H-15).
- Load tests (122 X-5).

## Acceptance criteria

- [ ] A test agent that writes a file during a call fails the build, and the message names the container and the path.
- [ ] A test agent that keeps the last request in a module variable, so a second call differs, fails the build.
- [ ] The echo agent, the simplifier, the `evaluator-facts` agent, and the recorder pass.
- [ ] Shuffled request order against fresh and reused pods gives the same outputs for the simplifier.
- [ ] Both containers of a pod deployed with the Helm chart run with a read-only root file system.
- [ ] The check runs by default in the template CI, with no extra setup per agent.

## Dependencies

- Depends on: [025 H-10](025-H-10-template-repo.md)
- Blocks: [059 H-15](059-H-15-agent-factory-cli.md)

## References

- Epic story: [H-19 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [B.2](../slm-agent-platform-epic-v3.md#b2)
- Backlog plan: [000-plan.md](000-plan.md)
