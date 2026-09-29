---
title: "PoC-9: Agent MVP: service template and agent factory CLI"
labels: ["poc", "priority:P0", "area:harness"]
milestone: "Agent MVP"
index: 9
iteration: PoC-9
timebox: "1 week"
depends_on: ["PoC-8"]
backlog_refs: ["025 H-10", "059 H-15", "024 CH-3", "058 CH-8"]
---

## Question

Can a developer take any business logic, pick a framework and a lane, and have a new agent scaffolded, running, and meeting the MVP bar in under a day?

## Why

This turns the PoC into the MVP. Everything the earlier iterations proved is packed into one service template and one command. That way the next agent, and the rest of the epic, starts from a working chassis, not from a blank repo.

## Scope

- [ ] `agentctl new <name> --kind transformer|evaluator|tool --framework <framework> --connector sidecar|remote`, which scaffolds a new agent from the service template.
- [ ] The service template: a hardened workload Dockerfile, the template A2A server that wraps `handle`, one event mapping per supported framework, and an example business logic plug-in per supported framework.
- [ ] An example config with `spec.engine.connector` and `spec.trust`.
- [ ] Docker Compose profiles (`fake`, `local`) that run both containers, the chassis and the workload, with a debugger attached to each.
- [ ] The shared Helm library chart (backlog [024 CH-3](../issues/024-CH-3-helm-library-chart.md)): it adds the chassis container with a pinned tag, mounts secrets in the chassis container only, and puts the Service on the chassis port.
- [ ] The template ships the fakes, the fake model server, the port contract suites, and a test fixture kit for business logic authors, so a new agent is testable offline from its first commit. The kit starts the chassis in the `fake` profile as a separate process (suggested: `uvx chassis serve --profile fake`) and drives the workload over A2A, so the workload's environment never installs the chassis package.
- [ ] CI in the template: lint (including the rule that keeps frameworks out of the chassis), the lane contract suite over A2A on localhost and in memory, the load smoke test, the hostile and red-team suites, and the offline eval.
- [ ] suggested: image signing with cosign.
- [ ] A "how to add business logic" guide: the `handle` contract, the event schema and the mappings, the lanes and the trust rule, tools, config, and how to run the evals.
- [ ] Optional: one run on a local Kubernetes cluster (kind or k3d) with the library chart and HPA or KEDA, to show the same images scale there.

## Reuse

- **Use:** suggested: Copier for the template, so existing agents can pull template updates. AWS AgentCore CLI and Google agents-cli as reference layouts. cosign, Helm, kind, and KEDA.
- **Build:** `agentctl`, the service template content, the library chart, and the how-to guide.
- Details: [reuse analysis](010-reuse-analysis.md)

## Out of scope

- Registry registration (backlog W7).
- Argo CD, Terraform, and cloud deploys (backlog [038 X-1a](../issues/038-X-1a-terraform-first-cloud.md) and W10).
- Chassis release rings and the minimum-version rule (backlog [056 CH-7](../issues/056-CH-7-chassis-release-rings.md)).
- New engines beyond the ones the bake-off ADR supports.

## Demo

Someone who did not build the chassis scaffolds a new agent with their own business logic on the default framework, in the `sidecar` lane. They run it with `docker compose up`, with a debugger on each container. They call it from the OpenAI SDK and an MCP client, see its trace and dashboard, and get a failing eval when they break it. Then they run its tests offline: the kit starts the chassis in the `fake` profile next to the workload and runs the contract suite over A2A, with nothing from the chassis installed in the workload's environment.

## Exit criteria

- [ ] A scaffolded agent's `make test` passes offline on its first commit.
- [ ] Swap drill: one real component (for example LiteLLM for direct vLLM, or Langfuse for another OpenTelemetry backend) is replaced in under a day, with no business logic change.
- [ ] Every item in the Agent MVP done-when list in [000-plan.md](000-plan.md#agent-mvp-done-when) is checked.
- [ ] A scaffolded agent passes its own CI with no manual edits.
- [ ] The library chart renders a pod with both containers, secrets in the chassis container only, and the Service on the chassis port.
- [ ] A new agent goes from `agentctl new` to a working demo in under a day, tested by someone new to the code.
- [ ] [ADR-001](../adr/001-chassis-delivery-model.md) and the ADRs from PoC-6 and PoC-7 are linked from the template docs.
- [ ] A short report lists what the backlog chassis waves (W2 and W5) should change, based on the PoC.

## Links

- Plan: [000-plan.md](000-plan.md) · Previous: [PoC-8](008-PoC-8-build-cross-cutting.md)
- Decision: [ADR-001](../adr/001-chassis-delivery-model.md)
- Backlog issues this previews: [025 H-10](../issues/025-H-10-template-repo.md), [059 H-15](../issues/059-H-15-agent-factory-cli.md), [024 CH-3](../issues/024-CH-3-helm-library-chart.md), [058 CH-8](../issues/058-CH-8-framework-event-mappings.md) (event mappings in the template)
- Epic: [B.5](../slm-agent-platform-epic-v3.md#b5), [G.4](../slm-agent-platform-epic-v3.md#g4), [H](../slm-agent-platform-epic-v3.md#app-h)
