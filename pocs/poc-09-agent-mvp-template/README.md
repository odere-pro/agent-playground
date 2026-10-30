# PoC-9: Agent MVP: service template and agent factory CLI

Status: in progress: 7 of 8 exit criteria have evidence, 2 of them flagged. Waits on the trial by someone new to the code (criterion 6).
Planning doc: [009-PoC-9-agent-mvp-template.md](../../docs/planning/poc/009-PoC-9-agent-mvp-template.md)
Time box: 1 week

## Question

Can a developer take any business logic, pick a framework and a lane, and have a new agent scaffolded, running, and meeting the MVP bar in under a day?

## Scope

- [x] `agentctl new <name> --kind transformer|evaluator|tool --framework <framework> --connector sidecar|remote`, which scaffolds a new agent from the service template. · built: `packages/agentctl`; `packages/agentctl/tests/test_agentctl_cli.py`, `test_agentctl_render.py`, `test_agentctl_config.py` (the trust rule agrees with the chassis loader). · evidence: `tests/test_poc09_scaffold.py::test_c4_ci_steps_pass_from_a_fresh_clone`, all six frameworks
- [x] The service template: a hardened workload Dockerfile, the template A2A server that wraps `handle`, one event mapping per supported framework, and an example business logic plug-in per supported framework. · built: `packages/agentctl/src/agentctl/template/project/` (Dockerfile, plug-in, mapping, and telemetry per framework, copied from the echo workloads with a drift test); the template A2A server is the `workload-a2a` package, a dependency, not a copy (ADR-002). · evidence: `make image` on a rendered agent, 2026-10-30: uid `10002:10002`
- [x] An example config with `spec.engine.connector` and `spec.trust`. · evidence: the template's `config/agent.yaml` and `config/fake.yaml` load in the chassis config model for every framework and lane: `packages/agentctl/tests/test_agentctl_config.py`
- [x] Docker Compose profiles (`fake`, `local`) that run both containers, the chassis and the workload, with a debugger attached to each. · built: the template's `deploy/compose/docker-compose.yaml`; `tests/test_poc09_template_ops.py::test_each_profile_runs_the_chassis_and_the_workload_as_sidecar`, `::test_a_debugger_is_attached_to_each_container`, `::test_docker_compose_config_accepts_each_profile` ([note](notes/2026-10-29-template-ops.md)). · evidence: `docker compose --profile fake up -d --wait --build` on a rendered agent, every service healthy, 2026-10-30, rerun on `696cee2` after the security changes (`make image` too). · not shown: a debugger attached or its port probed, the `local` profile (needs a LiteLLM)
- [x] The shared Helm library chart (backlog [024 CH-3](../../docs/planning/issues/024-CH-3-helm-library-chart.md)): it adds the chassis container with a pinned tag, mounts secrets in the chassis container only, and puts the Service on the chassis port. · built: `deploy/helm/agent-lib` and `deploy/helm/example-agent`; `tests/test_poc09_chart.py` (default and remote renders, 13 values that must fail). · evidence: `poc09-helm` run [41039762365](https://github.com/odere-pro/agent-playground/actions/runs/41039762365) on `bf9f3d0`: helm v3.19.0, checksum OK, `tests/test_poc09_chart.py` 23 passed (`helm lint`, `helm template`, and 13 values that must fail)
- [x] The template ships the fakes, the fake model server, the port contract suites, and a test fixture kit for business logic authors, so a new agent is testable offline from its first commit. The kit starts the chassis in the `fake` profile as a separate process (suggested: `uvx chassis serve --profile fake`) and drives the workload over A2A, so the workload's environment never installs the chassis package. · evidence: `packages/agent-testkit` starts the chassis in the `fake` profile as its own uv environment and drives the workload over A2A (Unix sockets); `tests/test_poc09_scaffold.py::test_c1_scaffolded_make_test_passes_offline[python]`, `[pydanticai]`, `[langgraph]`, `[openai-agents]`; `packages/agent-testkit/tests/test_agent_testkit_isolation.py` ([note](notes/2026-10-29-testkit.md))
- [x] CI in the template: lint (including the rule that keeps frameworks out of the chassis), the lane contract suite over A2A on localhost and in memory, the load smoke test, the hostile and red-team suites, and the offline eval. · built: the template's `.github/workflows/ci.yml` and `.gitlab-ci.yml`; `tests/test_poc09_template_ops.py::test_github_ci_runs_every_stage_through_make`, `::test_actionlint_accepts_the_rendered_workflow`; `packages/agentctl/tests/test_agentctl_render.py::test_every_target_the_ci_calls_is_in_the_makefile`. · evidence: `tests/test_poc09_scaffold.py::test_c4_ci_steps_pass_from_a_fresh_clone`; the suites are the agent's own (`agent_testkit.suites`). · not shown: a run on a hosted CI runner
- [ ] suggested: image signing with cosign. · built: `make sign` and a keyless, default-branch-only job; `tests/test_poc09_template_ops.py::test_github_signing_is_keyless_and_default_branch_only`. · waits on: a CI run
- [x] A "how to add business logic" guide: the `handle` contract, the event schema and the mappings, the lanes and the trust rule, tools, config, and how to run the evals. · evidence: [docs/guides/how-to-add-business-logic.md](../../docs/guides/how-to-add-business-logic.md) and its copy in the template's `docs/`; `tests/test_poc09_docs.py::test_every_relative_link_in_the_monorepo_guide_resolves`, `::test_the_monorepo_guide_links_every_adr_it_rests_on`
- [ ] Optional: one run on a local Kubernetes cluster (kind or k3d) with the library chart and HPA or KEDA, to show the same images scale there.

## Exit criteria

Each one has a scenario test in `tests/` or a recorded reason it cannot have one yet.

- [x] A scaffolded agent's `make test` passes offline on its first commit. · evidence: `tests/test_poc09_scaffold.py::test_c1_scaffolded_make_test_passes_offline[python]`, `[pydanticai]`, `[langgraph]`, `[openai-agents]` (each: `agentctl new`, a first commit with a `uv.lock` pinned to the platform, then the agent's own `make test` with `UV_OFFLINE=1`: its unit tests and the lane contract over A2A); `::test_c1_c4_scaffold_has_a_first_commit_and_a_complete_tree`
- [x] Swap drill: one real component (for example LiteLLM for direct vLLM, or Langfuse for another OpenTelemetry backend) is replaced in under a day, with no business logic change. · evidence: `tests/test_poc09_swap.py` ([note](notes/2026-10-30-swap-drill.md)); the real backends: PoC-7 `poc07-stack` run 40735435727
- [x] Every item in the Agent MVP done-when list in [000-plan.md](../../docs/planning/poc/000-plan.md#agent-mvp-done-when) is checked. · evidence: [audit](notes/2026-10-30-done-when.md), `tests/test_poc09_records.py::test_c3_the_audit_has_one_row_per_done_when_item`. · **flagged:** 14 of 17 met; rows 6, 7, and 9 are the flags PoC-4 and PoC-5 closed with
- [x] A scaffolded agent passes its own CI with no manual edits. · evidence: `tests/test_poc09_scaffold.py::test_c4_ci_steps_pass_from_a_fresh_clone`: a fresh clone, the platform at another path, no edits: `lint`, `test`, `load-smoke`, `hostile`, `redteam`, `eval`, `uv lock --check`. · **flagged:** run locally, not on a hosted runner (a new repo needs the user's call); `make image` shown by hand; `sign` needs a registry
- [x] The library chart renders a pod with both containers, secrets in the chassis container only, and the Service on the chassis port. · evidence: `poc09-helm` run [41039762365](https://github.com/odere-pro/agent-playground/actions/runs/41039762365) on `bf9f3d0`: helm v3.19.0, checksum OK, `tests/test_poc09_chart.py` 23 passed (`helm lint`, `helm template`, and 13 values that must fail)
- [ ] A new agent goes from `agentctl new` to a working demo in under a day, tested by someone new to the code. · waits on: a person new to the code ([trial steps](notes/2026-10-30-new-user-trial.md))
- [x] [ADR-001](../../docs/planning/adr/001-chassis-delivery-model.md) and the ADRs from PoC-6 and PoC-7 are linked from the template docs. · evidence: the template's `docs/decisions.md` and guide; `tests/test_poc09_docs.py::test_the_template_docs_link_adr_001_and_the_poc6_and_poc7_adrs`, `::test_every_monorepo_url_in_the_template_docs_names_a_file_that_exists`
- [x] A short report lists what the backlog chassis waves (W2 and W5) should change, based on the PoC. · evidence: [report](notes/2026-10-30-w2-w5-report.md), [backlog changes](notes/backlog-changes.md)

## How to run

```bash
make test-poc POC=09
```

## Demo

Offline record: [demo/2026-10-30-demo-agent-mvp-offline.md](demo/2026-10-30-demo-agent-mvp-offline.md). Script: `demo/demo.sh` (`--live` starts the Compose stack and calls it).

## Notes and decisions

Dated files in `notes/`. `notes/backlog-changes.md` lists what the backlog should change when this iteration closes.
