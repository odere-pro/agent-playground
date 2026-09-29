---
title: "H-15: Agent factory CLI: scaffold an agent by class and kind, modules by config"
labels: ["story", "priority:P1", "phase:0-harness", "area:harness", "size:M", "layer:chassis"]
milestone: "W5 Chassis completion"
index: 59
epic_id: H-15
depends_on: ["008 H-14", "025 H-10", "051 H-13", "057 H-19", "058 CH-8", "055 CH-6"]
blocks: []
epic_refs: [B.5]
---

## Why

Every new agent should start from the same template, with only the parts its class needs. A CLI makes that one command, so the registry, the MCP server, and the orchestrator are built quickly and the same way. Under [ADR-001](../adr/001-chassis-delivery-model.md), the generated repo holds only the workload: the chassis comes as the generic image. It needs the one agent interface (008 H-14), the template repo (025 H-10), and the framework event mappings (058 CH-8).

## What

- `agentctl new <name> --kind transformer|evaluator|tool|orchestrator|data`.
- The class follows from the kind: transformer, evaluator, and tool agents are `stateless`; `orchestrator` and `data` are their own classes.
- It creates the workload repo from the template:
  - the workload skeleton, with `handle`;
  - the template A2A server that wraps `handle` (009 CH-1);
  - the event mapping for the chosen framework (058 CH-8);
  - a Dockerfile for the workload image;
  - a Helm chart that includes the shared library chart (024 CH-3);
  - the CI that runs the contract suite over both transports.
- The inbound adapters, the pipeline, and the ports live in the chassis image, not in the generated repo.
- `--connector sidecar|remote`, default `sidecar`. The generated tests run the chassis in the `fake` profile as a separate process and drive the workload over A2A; `inprocess` is for the chassis's own tests and is never deployed.
- `--trust trusted|untrusted`, default `trusted`. `--trust untrusted` produces a `remote` chart (055 CH-6): the workload in its own pod, with gVisor and default-deny egress. Asking for `--trust untrusted` with `--connector sidecar` is refused.
- `--framework` picks one of the supported frameworks from 058 CH-8, and its event mapping.
- It writes a starter `AgentConfig` with `class`, `kind`, `modules`, `spec.engine.connector`, `spec.trust`, interface flags, and a `governance` block to fill in.
- Modules follow the B.5 table and are switched on by `spec.modules`. For example, a tool agent gets the tool port (054 H-16); a data agent gets its own store and no tool port.
- The CLI refuses a module that the B.5 table does not allow for the kind.
- `--kind orchestrator` creates the skeleton with placeholders for the routing, planner, and workflow modules.
- Installed with uv; `--help` documents every option.

## Reuse

- **Use:** suggested: Copier for the template, so existing agents can pull template updates. Chassis updates do not come this way: they come from the library chart's tag. The AgentCore CLI is a reference for the command shape.
- **Build:** `agentctl new` on top of the template.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- The orchestrator modules themselves (099 O-1 and later).
- The stateless check (057 H-19), which the generated repo runs.
- Registry registration on deploy (083 R-2).

## Acceptance criteria

- [ ] `agentctl new demo --kind <kind>` works for all five kinds, and each result builds and passes the template CI, including the stateless check, with no hand edits.
- [ ] Each generated config has the right `class` and `kind` and passes the agent config JSON Schema.
- [ ] The generated modules match the B.5 table for each kind.
- [ ] Asking for a module the kind does not allow fails with a clear error.
- [ ] A generated transformer runs in Docker Compose in the `sidecar` lane, as two containers, and answers through the native, OpenAI, Anthropic, and MCP APIs.
- [ ] `--trust untrusted` produces a `remote` chart: the workload runs in its own pod with the gVisor RuntimeClass and default-deny egress, and the chassis pod stays outside the sandbox.
- [ ] `--trust untrusted --connector sidecar` fails with a clear error.
- [ ] Each `--framework` choice gives a repo whose event mapping passes its contract test.
- [ ] The generated repo holds no chassis code. The chassis image and tag come from the library chart.
- [ ] The generated `governance` block lists every field from E.4, with a placeholder to fill in.

## Dependencies

- Depends on: [008 H-14](008-H-14-one-agent-interface.md), [025 H-10](025-H-10-template-repo.md), [051 H-13](051-H-13-openapi-mcp-tools.md), [057 H-19](057-H-19-stateless-check-ci.md), [058 CH-8](058-CH-8-framework-event-mappings.md), [055 CH-6](055-CH-6-remote-lane-trust-rule.md)
- Blocks: none

## References

- Epic story: [H-15 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [B.5](../slm-agent-platform-epic-v3.md#b5)
- Backlog plan: [000-plan.md](000-plan.md)
