---
title: "H-19: Stateless check in CI: fail the build on local disk writes or kept data"
labels: ["story", "priority:P1", "phase:0-harness", "area:harness", "size:M", "layer:chassis"]
milestone: "W5 Chassis completion"
index: 57
epic_id: H-19
depends_on: ["025 H-10", "015 H-8"]
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
- Suggested: an admission rule that rejects agent pods in which any container lacks a read-only root file system. It is a ValidatingAdmissionPolicy, in the `agent-trust-rule` set ([ADR-005](../adr/005-remote-lane-auth-and-trust-admission.md) decision 4).
- Data agents write only to their own store, never to local disk, so the disk check applies to every class.
- The failure message names the container, and the file written or the call that differed.
- The kept-data check starts from the PoC-4 scenario test, `pocs/poc-04-stateless-scalable/tests/test_hidden_state.py`. Run A with a marker goes to one replica; run B then goes to the reused workload and to a fresh one. Nothing of A may show in B's answer or in what B sends the model, and no module global of a workload package may change. Its order is fixed (A, then B), not shuffled.
- What PoC-4 found ([hidden-state note](../../../pocs/poc-04-stateless-scalable/notes/2026-10-01-hidden-state.md)):
  - the A2A SDK's `InMemoryTaskStore` keeps every task, so the Python template server prunes a task once it ends, and the TypeScript echo prunes 3 s after the final save (contract v3, Known gaps);
  - LangGraph and PydanticAI keep no state as the echoes use them (no checkpointer or store, no `message_history`). A workload that keeps them in process is rejected unless the state moves behind the chassis into `StatePort`;
  - a read-only root held on both containers for all four engines in Compose, and for `echo-python` and `echo-typescript` on kind.

## Reuse

- **Use:** ValidatingAdmissionPolicy, in the `agent-trust-rule` set (ADR-005 decision 4), next to the trust rule (055 CH-6) and the minimum-version rule (026 CH-4).
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Idempotency (018 H-18).
- The `agent-trust-rule` policy set itself, with the trust rule (055 CH-6) and the minimum-version rule (026 CH-4). This issue adds one rule to it.
- The process-wide counter of failed tool listings in `echo-python` and `echo-langgraph`. It holds no request data.
- The agent factory CLI (059 H-15).
- Load tests (122 X-5).

## Acceptance criteria

- [ ] A test agent that writes a file during a call fails the build, and the message names the container and the path.
- [ ] A test agent that keeps the last request in a module variable, so a second call differs, fails the build.
- [ ] The template's workload for each supported framework passes.
- [ ] Shuffled request order against fresh and reused pods gives the same outputs for the simplifier.
- [x] Both containers of a pod deployed with the Helm chart run with a read-only root file system. · Delivered in PoC-9: `pocs/poc-09-agent-mvp-template/tests/test_poc09_chart.py::test_sidecar_lane_containers_are_hardened`, `pocs/poc-09-agent-mvp-template/tests/test_poc09_chart.py::test_remote_lane_renders_the_chassis_pod_and_a_gvisor_workload_pod` (render tests; `poc09-helm.yml`).
- [ ] The check runs by default in the template CI, with no extra setup per agent.

## Dependencies

- Depends on: [025 H-10](025-H-10-template-repo.md), [015 H-8](015-H-8-testing-kit.md)
- Blocks: [059 H-15](059-H-15-agent-factory-cli.md)

## References

- Epic story: [H-19 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [B.2](../slm-agent-platform-epic-v3.md#b2)
- Backlog plan: [000-plan.md](000-plan.md)
