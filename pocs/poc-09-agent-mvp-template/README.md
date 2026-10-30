# PoC-9: Agent MVP: service template and agent factory CLI

Status: in progress (started 2026-10-29; the template A2A server work waits on PR 22)
Planning doc: [009-PoC-9-agent-mvp-template.md](../../docs/planning/poc/009-PoC-9-agent-mvp-template.md)
Time box: 1 week

## Question

Can a developer take any business logic, pick a framework and a lane, and have a new agent scaffolded, running, and meeting the MVP bar in under a day?

## Scope

- [ ] `agentctl new <name> --kind transformer|evaluator|tool --framework <framework> --connector sidecar|remote`, which scaffolds a new agent from the service template. · built: `packages/agentctl`; `packages/agentctl/tests/test_agentctl_cli.py`, `test_agentctl_render.py`, `test_agentctl_config.py` (the trust rule agrees with the chassis loader). · waits on: criterion 4 and the human trial (criterion 6)
- [ ] The service template: a hardened workload Dockerfile, the template A2A server that wraps `handle`, one event mapping per supported framework, and an example business logic plug-in per supported framework. · built: `packages/agentctl/src/agentctl/template/project/` (Dockerfile, plug-in, mapping, and telemetry per framework, copied from the echo workloads with a drift test); the template A2A server is the `workload-a2a` package, a dependency, not a copy (ADR-002). · waits on: a `make image` build of a rendered agent
- [x] An example config with `spec.engine.connector` and `spec.trust`. · evidence: the template's `config/agent.yaml` and `config/fake.yaml` load in the chassis config model for every framework and lane: `packages/agentctl/tests/test_agentctl_config.py`
- [ ] Docker Compose profiles (`fake`, `local`) that run both containers, the chassis and the workload, with a debugger attached to each. · built: the template's `deploy/compose/docker-compose.yaml`; `tests/test_poc09_template_ops.py::test_each_profile_runs_the_chassis_and_the_workload_as_sidecar`, `::test_a_debugger_is_attached_to_each_container`, `::test_docker_compose_config_accepts_each_profile` ([note](notes/2026-10-29-template-ops.md)). · waits on: a `docker compose up` run of a rendered agent
- [ ] The shared Helm library chart (backlog [024 CH-3](../../docs/planning/issues/024-CH-3-helm-library-chart.md)): it adds the chassis container with a pinned tag, mounts secrets in the chassis container only, and puts the Service on the chassis port.
- [x] The template ships the fakes, the fake model server, the port contract suites, and a test fixture kit for business logic authors, so a new agent is testable offline from its first commit. The kit starts the chassis in the `fake` profile as a separate process (suggested: `uvx chassis serve --profile fake`) and drives the workload over A2A, so the workload's environment never installs the chassis package. · evidence: `packages/agent-testkit` starts the chassis in the `fake` profile as its own uv environment and drives the workload over A2A (Unix sockets); `tests/test_poc09_scaffold.py::test_c1_scaffolded_make_test_passes_offline[python]`, `[pydanticai]`, `[langgraph]`, `[openai-agents]`; `packages/agent-testkit/tests/test_agent_testkit_isolation.py` ([note](notes/2026-10-29-testkit.md))
- [ ] CI in the template: lint (including the rule that keeps frameworks out of the chassis), the lane contract suite over A2A on localhost and in memory, the load smoke test, the hostile and red-team suites, and the offline eval. · built: the template's `.github/workflows/ci.yml` and `.gitlab-ci.yml`; `tests/test_poc09_template_ops.py::test_github_ci_runs_every_stage_through_make`, `::test_actionlint_accepts_the_rendered_workflow`; `packages/agentctl/tests/test_agentctl_render.py::test_every_target_the_ci_calls_is_in_the_makefile`. · waits on: a CI run (criterion 4); load smoke, hostile, red team, and eval are stand-ins that run the platform's suites
- [ ] suggested: image signing with cosign. · built: `make sign` and a keyless, default-branch-only job; `tests/test_poc09_template_ops.py::test_github_signing_is_keyless_and_default_branch_only`. · waits on: a CI run
- [x] A "how to add business logic" guide: the `handle` contract, the event schema and the mappings, the lanes and the trust rule, tools, config, and how to run the evals. · evidence: [docs/guides/how-to-add-business-logic.md](../../docs/guides/how-to-add-business-logic.md) and its copy in the template's `docs/`; `tests/test_poc09_docs.py::test_every_relative_link_in_the_monorepo_guide_resolves`, `::test_the_monorepo_guide_links_every_adr_it_rests_on`
- [ ] Optional: one run on a local Kubernetes cluster (kind or k3d) with the library chart and HPA or KEDA, to show the same images scale there.

## Exit criteria

Each one has a scenario test in `tests/` or a recorded reason it cannot have one yet.

- [x] A scaffolded agent's `make test` passes offline on its first commit. · evidence: `tests/test_poc09_scaffold.py::test_c1_scaffolded_make_test_passes_offline[python]`, `[pydanticai]`, `[langgraph]`, `[openai-agents]` (each: `agentctl new`, a first commit with a `uv.lock` pinned to the platform, then the agent's own `make test` with `UV_OFFLINE=1`: its unit tests and the lane contract over A2A); `::test_c1_c4_scaffold_has_a_first_commit_and_a_complete_tree`
- [ ] Swap drill: one real component (for example LiteLLM for direct vLLM, or Langfuse for another OpenTelemetry backend) is replaced in under a day, with no business logic change.
- [ ] Every item in the Agent MVP done-when list in [000-plan.md](../../docs/planning/poc/000-plan.md#agent-mvp-done-when) is checked.
- [ ] A scaffolded agent passes its own CI with no manual edits. · waits on: the platform packages being published. A rendered agent names them by absolute path (`[tool.uv.sources]`), so a CI runner cannot resolve them ([open items](notes/2026-10-30-open-items.md))
- [ ] The library chart renders a pod with both containers, secrets in the chassis container only, and the Service on the chassis port.
- [ ] A new agent goes from `agentctl new` to a working demo in under a day, tested by someone new to the code.
- [x] [ADR-001](../../docs/planning/adr/001-chassis-delivery-model.md) and the ADRs from PoC-6 and PoC-7 are linked from the template docs. · evidence: the template's `docs/decisions.md` and guide; `tests/test_poc09_docs.py::test_the_template_docs_link_adr_001_and_the_poc6_and_poc7_adrs`, `::test_every_monorepo_url_in_the_template_docs_names_a_file_that_exists`
- [ ] A short report lists what the backlog chassis waves (W2 and W5) should change, based on the PoC.

## How to run

```bash
make test-poc POC=09
```

## Demo

Not recorded yet. The script and its output go to `demo/`.

## Notes and decisions

Dated files in `notes/`. `notes/backlog-changes.md` lists what the backlog should change when this iteration closes.
