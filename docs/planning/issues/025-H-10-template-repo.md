---
title: "H-10: Service template: workload Dockerfile, Helm chart on the chassis library chart, CI (tests, signing, deploy, registration)"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:L", "layer:chassis"]
milestone: "W2 Chassis MVP"
index: 25
epic_id: H-10
depends_on: ["015 H-8", "016 H-9", "024 CH-3"]
blocks: ["026 CH-4", "038 X-1a", "041 A-1", "057 H-19", "058 CH-8", "059 H-15", "083 R-2", "104 O-3"]
epic_refs: [G.4, H]
---

## Why

Every service is built from one template, so a new service gets its workload skeleton, Dockerfile, Helm chart, and CI with no extra work. Under [ADR-001](../adr/001-chassis-delivery-model.md), the template holds only the workload. The chassis comes as the generic chassis image, added by the shared library chart (024 CH-3), and is never copied into the service repo. With this issue, the echo agent runs in Kubernetes in the `sidecar` lane and passes contract tests in CI, as the Phase 0 done-when asks. The registry registration step is a stub until the registry exists (076 R-1) and the controller replaces it (083 R-2).

## What

- A template repo with the workload skeleton:
  - `handle`, and the small A2A server from 009 CH-1 that wraps it on localhost;
  - the echo workload;
  - an example config, with `spec.engine.connector: sidecar`.
- No chassis code is copied into the repo. The chassis comes as the generic chassis image, with its tag set in the library chart.
- A template rule: input text is treated as data. It goes to the model in its own message, never merged into the system prompt. In the `sidecar` lane, the workload builds the prompt, so the chassis cannot enforce this (moved from 022 H-6). A contract test checks it. LiteLLM's prompt-injection guardrails stay the hard control.
- A Dockerfile that builds the workload image only: Python 3.12 and uv, a small image, a non-root user.
- A Helm chart that includes the shared library chart (024 CH-3). The library chart adds the chassis sidecar with a pinned tag. The service's own values set the replicas from `scaling`, the config store location, secret references through External Secrets, and the labels the registry controller will watch.
- The Service points at the chassis port only. Probes run on the chassis's `/health` and `/ready`. Secrets are mounted into the chassis container only.
- CI (suggested: GitHub Actions): lint, unit tests, and the contract suite from 015 H-8, run over both transports: over A2A on localhost and in memory.
- Image signing with cosign, and a Kyverno policy that refuses unsigned images at deploy.
- Deploy with Argo CD from the Helm chart (GitOps) to a dev cluster (suggested: a local test cluster until 038 X-1a builds the cloud one).
- A registry registration step that only prints the manifest it would send.

## Reuse

- **Use:** AWS AgentCore CLI and Google agents-cli as reference layouts (both scaffold agent projects with CI). Helm, Argo CD, cosign, and Kyverno, as planned. The shared Helm library chart from 024 CH-3 adds the chassis container.
- **Build:** the service template: the workload skeleton, the A2A server that wraps `handle`, a Dockerfile for the workload only, and the chart that includes the library chart.
- **Scope change:** the library chart was split out into 024 CH-3, and the credentials and egress rules into 026 CH-4.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Chassis-only credentials for every internal service, and default-deny egress (026 CH-4).
- Cloud infrastructure in Terraform (038 X-1a).
- The CI job that runs a fake workload through the `remote` lane (055 CH-6).
- The Kyverno trust rule (055 CH-6) and minimum-version rule (056 CH-7). They join the signed-image rule in one policy set.
- Framework event mappings and one non-Python workload in the template (058 CH-8).
- The stateless check in CI (057 H-19) and the `agentctl new` CLI (059 H-15).
- Real registry registration (083 R-2).

## Acceptance criteria

- [ ] CI runs unit tests and the contract suite over A2A on localhost and in memory, and a failing test fails the build.
- [ ] CI signs the workload image, and the cluster refuses an unsigned image.
- [ ] Argo CD deploys the echo agent from the Helm chart. Its pods run two containers, the chassis and the workload, and pass the `/health` and `/ready` probes.
- [ ] In the cluster, the echo agent answers through native, OpenAI, and Anthropic APIs, streaming and complete.
- [ ] In the cluster, the echo agent is started by an event, emits result events, and shows in traces.
- [ ] Changing the chassis tag in the library chart restarts the services on the new chassis, with no workload image rebuilt.
- [ ] The workload port cannot be reached from another pod. Only the chassis port is in the Service.
- [ ] The input-as-data contract test fails for a workload that merges the input into the system prompt.
- [ ] The registration step runs as a stub and prints the manifest.
- [ ] A new agent repo made from the template passes CI with only its name changed.

## Dependencies

- Depends on: [015 H-8](015-H-8-testing-kit.md), [016 H-9](016-H-9-local-debug-profile.md), [024 CH-3](024-CH-3-helm-library-chart.md)
- Blocks: [026 CH-4](026-CH-4-chassis-only-credentials-egress.md), [038 X-1a](038-X-1a-terraform-first-cloud.md), [041 A-1](041-A-1-simplifier-core.md), [057 H-19](057-H-19-stateless-check-ci.md), [058 CH-8](058-CH-8-framework-event-mappings.md), [059 H-15](059-H-15-agent-factory-cli.md), [083 R-2](083-R-2-kubernetes-controller.md), [104 O-3](104-O-3-agent-pools-keda.md)

## References

- Epic story: [H-10 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.4](../slm-agent-platform-epic-v3.md#g4) · [H](../slm-agent-platform-epic-v3.md#app-h)
- Backlog plan: [000-plan.md](000-plan.md)
