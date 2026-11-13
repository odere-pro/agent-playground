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
  - a Helm chart that includes the shared library chart (024 CH-3), from the chart template and the platform pin in 025 H-10 (`deploy/helm/` in the template);
  - the CI that runs the contract suite over both transports.
- The inbound adapters, the pipeline, and the ports live in the chassis image, not in the generated repo.
- `--connector sidecar|remote`, default `sidecar`. The generated tests run the chassis in the `fake` profile as a separate process and drive the workload over A2A; `inprocess` is for the chassis's own tests and is never deployed. A `remote` agent gets no Compose file, because Compose is the sidecar shape.
- `--trust trusted|untrusted` (P-15, decided 2026-11-13: a new flag; suggested: the values). The default follows the framework (`rules.trust_of`, ADR-006 decision 2), so a run with no flag is unchanged. `--trust untrusted` runs a trusted framework as untrusted and, with no `--connector`, picks `remote`. `--trust trusted` on an untrusted framework is refused. An untrusted agent with `--connector sidecar` is refused before anything is written.
- `--framework` picks one of the supported frameworks from 058 CH-8, and its event mapping.
- It writes a starter `AgentConfig` with `spec.engine.connector`, `spec.trust`, interface flags, and a `governance` block to fill in.
- It checks each generated model route against the routes the service's key lists (the route map, 012 H-3). The key is minted at deploy, so agentctl has no offline source for it: `--key-route` (repeated) states the key's list, and with no flag the list is the route the template names (suggested: `big-default`). A route outside the list is refused, never rewritten.
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
- [x] `--trust untrusted` produces a `remote` chart: the workload runs in its own pod with the gVisor RuntimeClass and default-deny egress, and the chassis pod stays outside the sandbox. · Delivered by: `packages/agentctl/tests/test_agentctl_chart.py::test_trust_untrusted_renders_the_workload_in_its_own_gvisor_pod` (`--trust untrusted` on `langgraph`, local `helm template` on `agent-lib`: the workload alone in Deployment `my-agent-workload` in `my-agent-remote`, `runtimeClassName: gvisor`, one default-deny policy there; the chassis Deployment alone, no RuntimeClass) and `::test_the_chart_renders_the_chassis_from_the_library[langgraph-remote-untrusted]`; `packages/agentctl/tests/test_agentctl_cli.py::test_trust_untrusted_alone_picks_the_remote_lane`; a render, not a cluster run; run on 2026-11-13
- [x] `--trust untrusted --connector sidecar` fails with a clear error. · Delivered by: `packages/agentctl/tests/test_agentctl_cli.py::test_trust_untrusted_with_connector_sidecar_is_refused_and_writes_nothing` (exit 2, the message names `--connector remote`, no output folder; `langgraph` and both untrusted frameworks); `packages/agentctl/tests/test_agentctl_config.py::test_trust_untrusted_matches_the_chassis_on_a_trusted_framework`; run on 2026-11-13
- [x] Each `--framework` choice gives a repo whose event mapping passes its contract test. · Delivered by: `packages/agentctl/tests/test_agentctl_rendered_checks.py::test_rendered_contract_test_passes_offline` (every framework in `rules.FRAMEWORKS`, `remote` for untrusted ones; runs the rendered `tests/test_contract.py`, `agent_testkit.contract.WorkloadContract` with the chassis in the `fake` profile as its own process, sockets disabled but Unix); run on 2026-11-13
- [x] The generated repo holds no chassis code. The chassis image and tag come from the library chart. · Delivered by: `packages/agentctl/tests/test_agentctl_render.py::test_the_rendered_tree_holds_no_chassis_code` and `::test_pyproject_names_the_kit_and_never_the_chassis`; `packages/agentctl/tests/test_agentctl_chart.py::test_the_chart_depends_on_agent_lib_through_the_platform_link` (no `chassis.image` in values) and `::test_the_chart_renders_the_chassis_from_the_library` (local helm, `file://` dependency; the chassis container image is the `agent-lib` default); run on 2026-11-13
- [x] The generated `governance` block holds the fields the chassis schema has (`risk_class`, `ai_generated_marking`), with a placeholder for each; 077 C-1 adds the rest of E.4. · Delivered by: `packages/agentctl/src/agentctl/template/project/config/agent.yaml.jinja` (`spec.governance`, a `fill in:` note per field); `packages/agentctl/tests/test_agentctl_config.py::test_the_starter_config_names_each_governance_field_with_a_placeholder` (both fields set, equal to `GovernanceSpec.model_fields`, loaded by `load_config`); run on 2026-11-10
- [x] `agentctl` refuses a generated route that the service's key does not list. · Delivered by: `packages/agentctl/tests/test_agentctl_cli.py::test_a_generated_route_the_key_does_not_list_is_refused` (exit 2, the route named, nothing written; the key's list from `--key-route`, as agentctl has no offline source for the key) and `::test_a_key_that_lists_the_generated_route_passes`; `packages/agentctl/tests/test_agentctl_render.py::test_the_generated_routes_are_the_ones_agentctl_checks` (the checked routes are every route the keyed configs name); run on 2026-11-13
- [ ] A person new to the code goes from `agentctl new` to a demo in under a day, recorded in a dated note.
- [ ] `agentctl new` renders `evals/cases.yaml` in the `eval_kit` schema with at least one judged case, and `make eval` in the generated repo runs the judge on the per-case fake model (gap review, 2026-11-07).

## Dependencies

- Depends on: [025 H-10](025-H-10-template-repo.md), [057 H-19](057-H-19-stateless-check-ci.md), [058 CH-8](058-CH-8-framework-event-mappings.md), [055 CH-6](055-CH-6-remote-lane-trust-rule.md), [024 CH-3](024-CH-3-helm-library-chart.md)
- Blocks: [076 R-1](076-R-1-registry-data-model-api.md), [099 O-1](099-O-1-orchestrator-agent.md)

## References

- Epic story: [H-15 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [B.5](../slm-agent-platform-epic-v3.md#b5)
- Backlog plan: [000-plan.md](000-plan.md)
