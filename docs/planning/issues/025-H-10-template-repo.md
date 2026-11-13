---
title: "H-10: Service template: workload Dockerfile, Helm chart on the chassis library chart, CI (tests, signing, deploy, registration)"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:M", "layer:chassis"]
milestone: "W2 Chassis MVP"
index: 25
epic_id: H-10
depends_on: ["015 H-8", "016 H-9", "024 CH-3"]
blocks: ["038 X-1a", "041 A-1", "057 H-19", "059 H-15", "062 H-11", "080 R-7", "083 R-2", "104 O-3"]
epic_refs: [G.4, H]
---

## Why

Every service is built from one template, so a new service gets its workload skeleton, Dockerfile, Helm chart, and CI with no extra work. Under [ADR-001](../adr/001-chassis-delivery-model.md), the template holds only the workload. The chassis comes as the generic chassis image, added by the shared library chart (024 CH-3), and is never copied into the service repo. This issue is the template side: a new agent repo that passes CI on its own, signs its image, and carries a chart on the library chart. The cluster deploy is 038 X-1a's. Registration is by label, through the controller (083 R-2).

## What

- A template repo with the workload skeleton:
  - `handle`, and the small A2A server from 009 CH-1 that wraps it on localhost;
  - the echo workload;
  - an example config, with `spec.engine.connector: sidecar`.
- Built in PoC-9 as a Copier template in `packages/agentctl/src/agentctl/template/`. The template depends on `packages/workload-a2a`, the Python template server, instead of copying files ([ADR-002](../adr/002-template-a2a-server-placement.md) item 4, amended 2026-10-01). Its TypeScript port is `packages/workloads/echo-typescript/src/a2a_server.ts`, with an inbound bearer flag (`packages/workloads/echo-typescript/src/auth.ts`).
- No chassis code is copied into the repo. The chassis comes as the generic chassis image, with its tag set in the library chart.
- A template rule: input text is treated as data. It goes to the model in its own message, never merged into the system prompt. In the `sidecar` lane, the workload builds the prompt, so the chassis cannot enforce this (moved from 022 H-6). A contract test checks it. LiteLLM's prompt-injection guardrails stay the hard control.
- A Dockerfile that builds the workload image only: Python 3.12 and uv, a small image, a non-root user. The PoC-2 Dockerfiles are the starting point: uid 10001, the base pinned by digest, and all four ran hardened (`cap_drop: ALL`, `no-new-privileges`, `read_only` with a `tmpfs` on `/tmp`) in [the Compose demo](../../../pocs/poc-02-two-engines-one-contract/demo/2026-10-01-demo-sidecar.md).
- A Helm chart that includes the shared library chart (024 CH-3). The library chart adds the chassis sidecar with a pinned tag. The service's own values set the replicas from `scaling`, the config store location, secret references through External Secrets, and the registration label the controller watches.
- The Service points at the chassis port only. Probes run on the chassis's `/health` and `/ready`. Secrets are mounted into the chassis container only.
- CI on GitHub Actions: lint, unit tests, and the contract suite from 015 H-8, run over both transports: over A2A on localhost and in memory. CI signs the image after the suites (suggested: keyless cosign).
- A pinned platform. Today a rendered agent names the platform through a git-ignored `.platform` link, made from `PLATFORM_PATH`, and CI pins the platform by commit SHA. Publishing the platform packages replaces the link with a release pin.

## Reuse

- **Use:** AWS AgentCore CLI and Google agents-cli as reference layouts (both scaffold agent projects with CI). Helm, Argo CD, cosign, and Kyverno, as planned. The shared Helm library chart from 024 CH-3 adds the chassis container.
- **Build:** the service template: the workload skeleton, the A2A server that wraps `handle`, a Dockerfile for the workload only, and the chart that includes the library chart.
- **Scope change:** the library chart was split out into 024 CH-3, and the credentials and egress rules into 026 CH-4.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Chassis-only credentials for every internal service, and default-deny egress (026 CH-4).
- Cloud infrastructure in Terraform (038 X-1a).
- The cluster deploy (038 X-1a): the cluster refuses an unsigned image, Argo CD deploys the echo agent from the chart, and the echo agent answers on the native, OpenAI, and Anthropic APIs in the cluster.
- The echo agent started by an event in the cluster, with result events and traces (021 H-20 and 019 H-17).
- A chassis tag change that restarts the services, and the workload port closed to other pods (024 CH-3).
- The CI job that runs a fake workload through the `remote` lane (055 CH-6).
- The Kyverno trust rule (055 CH-6) and minimum-version rule (056 CH-7). They join the signed-image rule in one policy set. The hand-kept `trustedRepositories` ConfigMap is 055 CH-6's too.
- Framework event mappings and one non-Python workload in the template (058 CH-8).
- The stateless check in CI (057 H-19) and the `agentctl new` CLI (059 H-15).
- Registration, which is by label, through the controller (083 R-2).

## Acceptance criteria

- [ ] CI runs unit tests and the contract suite over A2A on localhost and in memory, and a failing test fails the build.
- [ ] CI signs the workload image.
- [ ] The input-as-data contract test fails for a workload that merges the input into the system prompt.
- [x] A new agent repo made from the template passes CI with only its name changed. · Delivered in PoC-9: `pocs/poc-09-agent-mvp-template/tests/test_poc09_scaffold.py::test_c4_ci_steps_pass_from_a_fresh_clone`, `pocs/poc-09-agent-mvp-template/notes/2026-10-30-open-items.md` (hosted run 41087571278, all eight jobs)
- [x] The generated repo carries a chart on `agent-lib`. · Delivered by: `packages/agentctl/tests/test_agentctl_chart.py::test_the_chart_depends_on_agent_lib_through_the_platform_link`, `packages/agentctl/tests/test_agentctl_chart.py::test_the_chart_renders_the_chassis_from_the_library` (sidecar and remote lanes, offline `helm template`)
- [ ] The template pins a published platform release, not the `.platform` link.

## Dependencies

- Depends on: [015 H-8](015-H-8-testing-kit.md), [016 H-9](016-H-9-local-debug-profile.md), [024 CH-3](024-CH-3-helm-library-chart.md)
- Blocks: [038 X-1a](038-X-1a-terraform-first-cloud.md), [041 A-1](041-A-1-simplifier-core.md), [057 H-19](057-H-19-stateless-check-ci.md), [059 H-15](059-H-15-agent-factory-cli.md), [062 H-11](062-H-11-adapter-package.md), [080 R-7](080-R-7-trust-levels.md), [083 R-2](083-R-2-kubernetes-controller.md), [104 O-3](104-O-3-agent-pools-keda.md)

## References

- Epic story: [H-10 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.4](../slm-agent-platform-epic-v3.md#g4) · [H](../slm-agent-platform-epic-v3.md#app-h)
- Backlog plan: [000-plan.md](000-plan.md)
