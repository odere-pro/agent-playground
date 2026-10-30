# PoC-6a: Framework bake-off, part A: the `sidecar` lane

This part runs the trusted agent frameworks behind the chassis, in the `sidecar` lane. It exists to pick the default engine and the supported set for new agents. Run its tests offline with `scripts/check_offline.sh pocs/poc-06a-bake-off-sidecar-lane/tests`.

Status: in progress ([planning doc](../../docs/planning/poc/006-PoC-6-framework-bake-off.md)).
Time box: 1 week, after PoC-3.
Builds on: PoC-3, PoC-4, PoC-5.
Hands to: 6b, 6c, PoC-7.

Part B is [PoC-6b](../poc-06b-bake-off-remote-lane/README.md). The SLM runs are [PoC-6c](../poc-06c-pretrained-slm/README.md). This README keeps exit criteria 1, 2 (the TypeScript agent), 3, 4, 5, and 7 as a draft.

## Question

Which trusted frameworks should the platform support, and which one is the default for new agents? Does a full agent in another language pass the same contract?

## Scope

- [x] The trusted frameworks from the shortlist as workloads, each with its own event mapping: OpenAI Agents SDK. Google ADK is skipped (user decision, 2026-10-09). · evidence: `packages/workloads/echo-openai-agents/src/echo_openai_agents/mapping.py`; `packages/workloads/echo-openai-agents/tests/test_oai_tasks.py::test_it_uses_chat_completions_not_the_responses_api`, `::test_lookup_calls_both_tools_in_order_with_valid_arguments`; `packages/workloads/echo-openai-agents/tests/test_oai_errors.py::test_the_sdk_trace_export_is_off`
- [x] The TypeScript echo from PoC-2 grown into a full agent that serves A2A itself, emits the chassis event schema, and runs both benchmark tasks. · evidence: `tests/test_poc06a_criteria.py::test_criterion_2_the_typescript_agent_passes_through_the_generic_sidecar_connector`. `tests/test_poc06a_tasks.py::test_engine_passes_each_task_in_each_lane[echo-typescript-sidecar-*]`. `make ts-check` (84 of 84 pass, [demo record](demo/README.md))
- [x] A draft of the bake-off ADR for the trusted engines. · evidence: [ADR-006](../../docs/planning/adr/006-agent-engines-default-supported-lanes.md), Proposed
- [x] Two benchmark tasks for every engine: text in, text out (the simplifier), and a tool task (a lookup with two read-only tools). · evidence: `tests/test_poc06a_tasks.py::test_engine_passes_each_task_in_each_lane` (smoke, simplifier, lookup on every trusted engine, `inprocess` and `sidecar`). `tests/test_poc06a_spec.py::test_the_lookup_task_calls_both_tools_in_order`
- [ ] Run every task on a hosted big model, through the chassis model proxy and the router. · waits on: the Mac run, `make poc06-mac` (the provider key stays on the Mac). Offline, the same tasks pass on the fake model server. The SLM runs are [PoC-6c](../poc-06c-pretrained-slm/README.md).
- [x] Score each engine against the bake-off criteria in [000-plan.md](../../docs/planning/poc/000-plan.md#bake-off-criteria). · evidence: [notes/2026-10-09-scorecard.md](notes/2026-10-09-scorecard.md) (12 tables, offline numbers); `tests/test_poc06a_scorecard_records.py::test_every_criterion_covers_every_engine_with_no_empty_cell`
- [x] Check router compatibility per engine. · evidence: [notes/2026-10-09-scorecard.md](notes/2026-10-09-scorecard.md), "Router compatibility"; `packages/workloads/echo-openai-agents/tests/test_oai_router_compat.py::test_the_workload_sends_only_keys_the_proxy_keeps`; `tests/test_poc06a_scorecard_records.py::test_router_table_has_every_engine`. Provider-only features needed: none. Hosted and SLM behavior waits on the Mac run.
- [x] Rerun the PoC-3 contract suite, the PoC-4 load test, and the PoC-5 hostile suite on each new engine, in its lane. · evidence: criterion 4. Contract: criterion 1. Load: `poc06-load.yml` run 40691016893, [notes/2026-10-28-sidecar-load-in-ci.md](notes/2026-10-28-sidecar-load-in-ci.md). Hostile: kind, `poc06-kind.yml` run 38010290978.

## Exit criteria

Each one has a scenario test in `tests/` or a recorded reason it cannot have one yet.

- [x] 1. Every new Python workload passes the `EnginePort` contract suite over A2A on localhost and in memory, and runs offline against the fake model server. · evidence: `tests/test_poc06a_engine_contract.py::TestEngineInMemory` and `::TestEngineOverA2A` (every trusted Python engine, `echo-openai-agents` included). `tests/test_poc06a_criteria.py::test_criterion_1_every_python_workload_runs_offline_in_both_lanes`
- [x] 2. The non-Python agent passes the same contract suite, with no change to the chassis core. (The remote solution is [6b](../poc-06b-bake-off-remote-lane/README.md).) · evidence: `tests/test_poc06a_engine_contract.py::TestEngineOverA2A` (`echo-typescript`); `tests/test_poc06a_criteria.py::test_criterion_2_the_typescript_agent_passes_through_the_generic_sidecar_connector`
- [x] 3. Every shortlisted engine is scored on every criterion, with numbers where the criterion is measurable. · evidence: [notes/2026-10-09-scorecard.md](notes/2026-10-09-scorecard.md); `tests/test_poc06a_scorecard_records.py`. The numbers are offline. Real tokens and latency come from the Mac run (criterion 5). The `kagent-adk` cells wait on kind CI (6b).
- [x] 4. Every supported engine passes the contract, load, and hostile suites, in its lane. · contract: done offline (criterion 1). Load: the PoC-4 matrix (64 users at 1, 2, and 4 pairs, and 10 RPS on 1 pair) on `echo-openai-agents` and the full `echo-typescript`, on a GitHub runner with Docker. The run is `.github/workflows/poc06-load.yml` run 40691016893 on `ce9274d`, with 1 failed call in 22,420 ([notes/2026-10-28-sidecar-load-in-ci.md](notes/2026-10-28-sidecar-load-in-ci.md)). The other sidecar engines are PoC-4's quiet pass. `tests/test_poc06a_criteria.py::test_criterion_4_every_sidecar_engine_passed_the_load_matrix`, `::test_criterion_4_the_new_engines_come_from_the_ci_run`. Getting there fixed a bug: the TypeScript agent refused the scale stack's `--drain-timeout-s` flag and exited 2 (`ce9274d`). Hostile: done on kind for the new sidecar engines, `echo-openai-agents` and `echo-typescript`. The test is `pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_kind_sidecar_controls.py`, in `poc06-kind.yml` run 38010290978 on `0248655`, 74 passed ([run table](../poc-06b-bake-off-remote-lane/notes/2026-10-09-lanes-b-kind.md)). The static half passes offline in `test_poc06b_kind_static.py`. `echo-python` has the PoC-5 sidecar suite.
- [ ] 5. The token overhead against plain Python is known per engine, on a hosted big model. · waits on: the Mac run, `make poc06-mac`. Offline proxy: prompt bytes per call, within 9 bytes of plain Python for the five Chat Completions engines ([scorecard](notes/2026-10-09-scorecard.md), criterion 5).
- [ ] 7. (draft) An ADR names the default engine, the supported engines with their lane, and the rejected engines with reasons. · [ADR-006](../../docs/planning/adr/006-agent-engines-default-supported-lanes.md) is written and Proposed. Ticked in 6b when the user accepts it at the PR.

## How to validate

Offline, on localhost, with the fake model server:

```bash
make ts-check                                       # build and test the TypeScript agent once
make bakeoff ARGS=smoke                             # the super simple task, one PASS/FAIL/SKIP line per framework and lane
make bakeoff ARGS="run --tasks smoke,simplifier,lookup"   # the three tasks, with latency, tokens, and prompt bytes
```

`smoke` exits 1 on any FAIL. `kagent-adk` is always SKIP here (it runs on kind). Add `--engine` or `--lane` to narrow. Details: [packages/bakeoff/README.md](../../packages/bakeoff/README.md).

## How to run

```bash
make test-poc POC=06a
```

## Demo

Offline demo: `demo/demo.sh`. The record is linked from [demo/README.md](demo/README.md). The hosted-model demo comes from the Mac run.

## Notes and decisions

Dated files in `notes/`: the [scorecard](notes/2026-10-09-scorecard.md), the [sidecar load in CI](notes/2026-10-28-sidecar-load-in-ci.md). [notes/backlog-changes.md](notes/backlog-changes.md) lists what the backlog should change.
