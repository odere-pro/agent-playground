---
title: "H-15: Agent factory CLI: scaffold an agent by class and kind, modules by config"
labels: ["story", "priority:P1", "phase:0-harness", "area:harness", "size:L", "layer:chassis"]
milestone: "W5 Chassis completion"
index: 59
epic_id: H-15
depends_on: ["025 H-10", "057 H-19", "058 CH-8", "055 CH-6", "024 CH-3"]
blocks: ["076 R-1", "099 O-1"]
epic_refs: [B.5]
---

## Why

Every new agent should start from the same template, with only the parts its class needs. A CLI makes that one command, so the registry, the MCP server, and the orchestrator are built quickly and the same way. Under [ADR-001](../adr/001-chassis-delivery-model.md), the generated repo holds only the workload: the chassis comes as the generic image. It needs the one agent interface (008 H-14), the template repo (025 H-10), and the framework event mappings (058 CH-8).

## What

- `agentctl new <name> --kind transformer|evaluator|tool`, built in PoC-9 in `packages/agentctl` ([scaffold test](../../../pocs/poc-09-agent-mvp-template/tests/test_poc09_scaffold.py)), with `--framework`, `--connector`, `--dest`, `--platform-path`, and `--no-lock`. It pins the agent to the platform's `uv.lock` and commits a `uv.lock`. `kind` is recorded and does not change the generated code yet.
- The `orchestrator` and `data` kinds, the class each kind implies, and the modules per kind (the B.5 table, `spec.modules`) are 008 H-14's, re-scoped on 2026-11-06. This issue renders what 008 H-14 defines.
- It creates the workload repo from the template:
  - the workload skeleton, with `handle`;
  - the template A2A server that wraps `handle` (009 CH-1);
  - the event mapping for the chosen framework (058 CH-8);
  - a Dockerfile for the workload image;
  - a Helm chart that includes the shared library chart (024 CH-3), from the chart template and the platform pin in 025 H-10. Not built yet: the generated repo has no chart;
  - the CI that runs the contract suite over both transports.
- The inbound adapters, the pipeline, and the ports live in the chassis image, not in the generated repo.
- `--connector sidecar|remote`, default `sidecar`. The generated tests run the chassis in the `fake` profile as a separate process and drive the workload over A2A; `inprocess` is for the chassis's own tests and is never deployed. A `remote` agent gets no Compose file, because Compose is the sidecar shape.
- Trust follows the framework (`rules.trust_of`, ADR-006 decision 2); there is no `--trust` flag. An untrusted framework with `--connector sidecar` is refused. Boxes 6 and 7 still ask for `--trust trusted|untrusted`; proposal P-15 decides whether they follow the framework rule or a new flag.
- `--framework` picks one of the supported frameworks from 058 CH-8, and its event mapping.
- It writes a starter `AgentConfig` with `spec.engine.connector`, `spec.trust`, interface flags, and a `governance` block to fill in.
- It checks each generated model route against the routes the service's key lists (the route map, 012 H-3).
- Installed with uv; `--help` documents every option.

## Reuse

- **Use:** suggested: Copier for the template, so existing agents can pull template updates. Chassis updates do not come this way: they come from the library chart's tag. The AgentCore CLI is a reference for the command shape.
- **Build:** `agentctl new` on top of the template.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- The orchestrator modules themselves (099 O-1 and later).
- The five kinds, the `class` and `kind` in each generated config, and the module rules per kind: 008 H-14, re-scoped on 2026-11-06.
- The chart in the generated repo and the published platform pin: 025 H-10.
- The stateless check (057 H-19), which the generated repo runs.
- Registry registration on deploy (083 R-2).
- The rest of the E.4 governance fields: 077 C-1.

## Acceptance criteria

- [ ] A generated transformer runs in Docker Compose in the `sidecar` lane, as two containers, and answers through the native, OpenAI, Anthropic, and MCP APIs. The proof is a `demo.sh --live` rerun on the LangGraph default that calls `/v1/messages`, recorded as a dated note. The PoC-9 run showed native, OpenAI, and MCP only, with `pydanticai` (`pocs/poc-09-agent-mvp-template/notes/2026-11-05-machine-checks.md`, section 1).
- [ ] `--trust untrusted` produces a `remote` chart: the workload runs in its own pod with the gVisor RuntimeClass and default-deny egress, and the chassis pod stays outside the sandbox.
- [ ] `--trust untrusted --connector sidecar` fails with a clear error.
- [ ] Each `--framework` choice gives a repo whose event mapping passes its contract test.
- [ ] The generated repo holds no chassis code. The chassis image and tag come from the library chart.
- [ ] The generated `governance` block holds the fields the chassis schema has (`risk_class`, `ai_generated_marking`), with a placeholder for each; 077 C-1 adds the rest of E.4.
- [ ] `agentctl` refuses a generated route that the service's key does not list.
- [ ] A person new to the code goes from `agentctl new` to a demo in under a day, recorded in a dated note.
- [ ] `agentctl new` renders `evals/cases.yaml` in the `eval_kit` schema with at least one judged case, and `make eval` in the generated repo runs the judge on the per-case fake model (gap review, 2026-11-07).

## Dependencies

- Depends on: [025 H-10](025-H-10-template-repo.md), [057 H-19](057-H-19-stateless-check-ci.md), [058 CH-8](058-CH-8-framework-event-mappings.md), [055 CH-6](055-CH-6-remote-lane-trust-rule.md), [024 CH-3](024-CH-3-helm-library-chart.md)
- Blocks: [076 R-1](076-R-1-registry-data-model-api.md), [099 O-1](099-O-1-orchestrator-agent.md)

## References

- Epic story: [H-15 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [B.5](../slm-agent-platform-epic-v3.md#b5)
- Backlog plan: [000-plan.md](000-plan.md)
