# PoC-9: Agent MVP: service template and agent factory CLI

Status: not started
Planning doc: [009-PoC-9-agent-mvp-template.md](../../docs/planning/poc/009-PoC-9-agent-mvp-template.md)
Time box: 1 week

## Question

Can a developer take any business logic, pick a framework and a lane, and have a new agent scaffolded, running, and meeting the MVP bar in under a day?

## Scope

- [ ] `agentctl new <name> --kind transformer|evaluator|tool --framework <framework> --connector sidecar|remote`, which scaffolds a new agent from the service template.
- [ ] The service template: a hardened workload Dockerfile, the template A2A server that wraps `handle`, one event mapping per supported framework, and an example business logic plug-in per supported framework.
- [ ] An example config with `spec.engine.connector` and `spec.trust`.
- [ ] Docker Compose profiles (`fake`, `local`) that run both containers, the chassis and the workload, with a debugger attached to each.
- [ ] The shared Helm library chart (backlog [024 CH-3](../../docs/planning/issues/024-CH-3-helm-library-chart.md)): it adds the chassis container with a pinned tag, mounts secrets in the chassis container only, and puts the Service on the chassis port.
- [ ] The template ships the fakes, the fake model server, the port contract suites, and a test fixture kit for business logic authors, so a new agent is testable offline from its first commit. The kit starts the chassis in the `fake` profile as a separate process (suggested: `uvx chassis serve --profile fake`) and drives the workload over A2A, so the workload's environment never installs the chassis package.
- [ ] CI in the template: lint (including the rule that keeps frameworks out of the chassis), the lane contract suite over A2A on localhost and in memory, the load smoke test, the hostile and red-team suites, and the offline eval.
- [ ] suggested: image signing with cosign.
- [ ] A "how to add business logic" guide: the `handle` contract, the event schema and the mappings, the lanes and the trust rule, tools, config, and how to run the evals.
- [ ] Optional: one run on a local Kubernetes cluster (kind or k3d) with the library chart and HPA or KEDA, to show the same images scale there.

## Exit criteria

Each one has a scenario test in `tests/` or a recorded reason it cannot have one yet.

- [ ] A scaffolded agent's `make test` passes offline on its first commit.
- [ ] Swap drill: one real component (for example LiteLLM for direct vLLM, or Langfuse for another OpenTelemetry backend) is replaced in under a day, with no business logic change.
- [ ] Every item in the Agent MVP done-when list in [000-plan.md](../../docs/planning/poc/000-plan.md#agent-mvp-done-when) is checked.
- [ ] A scaffolded agent passes its own CI with no manual edits.
- [ ] The library chart renders a pod with both containers, secrets in the chassis container only, and the Service on the chassis port.
- [ ] A new agent goes from `agentctl new` to a working demo in under a day, tested by someone new to the code.
- [ ] [ADR-001](../../docs/planning/adr/001-chassis-delivery-model.md) and the ADRs from PoC-6 and PoC-7 are linked from the template docs.
- [ ] A short report lists what the backlog chassis waves (W2 and W5) should change, based on the PoC.

## How to run

```bash
make test-poc POC=09
```

## Demo

Not recorded yet. The script and its output go to `demo/`.

## Notes and decisions

Dated files in `notes/`. `notes/backlog-changes.md` lists what the backlog should change when this iteration closes.
