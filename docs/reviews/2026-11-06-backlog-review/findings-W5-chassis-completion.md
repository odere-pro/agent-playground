# Findings W5: W5 Chassis completion

Session: W5. Issues: 050–063. Waves: W5.
Rubric: [rubric.md](rubric.md).

## Summary

The PoC track built the remote lane and its trust rule (PoC-5, PoC-6b), the framework mappings (PoC-2, PoC-6a, PoC-6b), the MCP tool gateway and the code runner (PoC-5), the OpenAPI-to-MCP tool and `/manifest` v0 (PoC-3), the chart's read-only roots (PoC-9), and `agentctl new` with LangGraph as its default (PoC-9, PR #71). This session ticks 14 boxes with evidence, and 6 more after a body edit. No issue retires. Service operations, AsyncAPI, release rings, more brokers, and the public A2A endpoint have no code. Phases: 055 CH-6a feeds A, 058 CH-8a and 058 CH-8b feed C, 054 H-16 feeds E, and 057 H-19 and 059 H-15a feed F. Most of the wave is `later`, and 063 S-8 is `parked`. The top risk is the phase F issue 059 H-15. It waits on 038 X-1a (first-cloud Terraform) through 055 CH-6, and on 051 H-13 and 050 CH-5, which no phase needs. And its `--trust` boxes contradict the built rule that trust follows the framework.

Method: four `Explore` subagents, one per 3 or 4 issues. They also read `docs/specs/contract.md` by section and every `notes/backlog-changes.md`. Every test id below was checked with `grep -rn "def <name>"` or `class <name>`. Ten were run offline:

```
UV_NO_SYNC=1 scripts/check_offline.sh <10 test ids from 051, 054, 055, 057, 059> -q -p no:cacheprovider
53 passed in 42.61s
```

`TestPoc05ThreeLanes` is a class bound to `LaneContract`, so it counts as many cases. The PoC-5 kind tests carry `requires_env` (and so `network`). CI runs only the files in `deploy/kind/poc05/run.sh` `REMOTE_TESTS`, through `.github/workflows/remote-lane.yml`. `test_poc05_kind_tool_gateway.py` and `test_poc05_kind_hardreq1.py` are not in that list, so they have only ever run by hand, as recorded in `pocs/poc-05-sandboxed/notes/2026-10-09-close-runs.md` section 6.

## Issues

`Boxes` counts what is met under the current text. Boxes that tick only after a body edit are counted open.

| Issue | Boxes | Phase | Verdicts | Evidence or reason |
| ----- | ----- | ----- | -------- | ------------------ |
| 050 CH-5 | 0/5 | later | keep | No code. `Spec` refuses an `operations` key at load (`extra="forbid"`, `server/config.py`). `docs/specs/contract.md` has no section for it. No product phase declares service operations |
| 051 H-13 | 2/8 | later | tick, tick-blocked, drift, resize M | 2 ticks (MCP tool on the echo, which is the simplifier; input schema equals the OpenAPI schema). Box 1 checks the version string, not a validator (W5-17). Box 4 has no 403 test on `/v1/mcp`. Box 6 ticks once split (no image digests). Boxes 5, 7, 8 wait on governance, 050 CH-5, and event start (021 H-20) |
| 052 H-21 | 0/6 | later | keep, cite | No code: "asyncapi" appears only in the epic and the planning tools. 3.0 versus 3.1 and stale config key names (W5-25) |
| 053 H-22 | 0/7 | later | conflict, drift, deps, resize M | Retry and dead-letter exist only in `EventPortContract` on the in-memory bus and Kafka. Box 4 conflicts: a non-CloudEvent is dropped (W5-09). Topic name, partition key, and the broker-native dead-letter text drift from ADR-004 and contract v3 (W5-16). No consumer (021 H-20), no recorder (040 D-0) |
| 054 H-16 | 3/11 | E | tick, parked, conflict, tick-blocked, deps, resize L | 2 ticks (the gateway refuses an unlisted tool, and the code runs in its sandbox), plus box 6, already ticked. Box 1 parked (guided JSON, W5-07). Box 4 conflicts: there is no `mode: write` config and no chassis allow-list (W5-06). Boxes 2, 5, 8, 11 are `tick-blocked` (W5-11). Boxes 7, 9 open |
| 055 CH-6 | 5/7 | A (055a), later (055b) | tick, split, drift, deps | 5 ticks (the remote lane contract in CI, remote auth, admission, every stage in every lane plus the blind-spots list, gVisor overhead). Box 2 waits on T10 and on the tool check in CI. Box 5 (managed runtime) needs DEC-1 and 038 X-1a: split (W5-03) |
| 056 CH-7 | 0/7 | later | conflict, split, drift, deps, resize L | No rings, no Argo, no minimum-version rule. Two specs claim the rule exists (W5-04). `/manifest` reports the package version, not the image tag (W5-22) |
| 057 H-19 | 1/6 | F | tick, drift, deps, resize M | Box 5 ticked (chart render tests). The PoC-4 detector exists, but the template CI has no stateless step. Box 3 names agents that do not exist (W5-18). Kyverno text (W5-14) |
| 058 CH-8 | 4/5 | C | tick, split, drift, deps | 4 ticks (the engine contract in both transports, TypeScript, the import rule, the scorecard gaps). Open: box 2 (a contract test per mapping) and the guide. Phase C's LangChain items have no boxes: split into 058 CH-8a and 058 CH-8b (W5-05) |
| 059 H-15 | 0/10 | F (059a), later (059b) | conflict, split, deps, tick-blocked | No box met. Trust follows the framework and there is no `--trust` flag (W5-02). There is no chart in the generated repo, and the platform pin is a path. Only 3 kinds exist, and `class`, `kind`, `modules`, and `governance` are not in the config schema. Its deps block phase F (W5-01) |
| 060 H-23 | 0/5 | later | parked, deps | Only `kafka` passes the full suite against a real broker. DEC-1 has not picked the broker (ADR-004 item 5). The Dapr line is parked (W5-19). Add 053 H-22 as a dependency |
| 061 H-5 | 0/6 | later | keep | No public A2A route and no `a2a` interface flag. The agent card is the workload's, unsigned, and only read by the chassis. Whether it ships in version 1 is DEC-1 (the product plan, gap P3) |
| 062 H-11 | 0/4 | later | moot, conflict, deps | Box 4 contradicts ADR-002 and the architecture spec: a workload's tests never import `chassis` (W5-10). Boxes 1 and 2 are shown only in this repo (`echo-typescript`). Box 3 waits on 025 H-10a's release. Depends on 012 H-3, which is half parked (W5-26) |
| 063 S-8 | 0/5 | parked | parked | It trains an SLM. Nothing trains a model, and 033 S-5 is not built. The PoC-6c baseline lines are not applied (W5-20) |

## Ticks

Ready to paste. Each test id exists today.

- `051 H-13`, box "An MCP client lists the simplifier's tools": `· Delivered in PoC-3: pocs/poc-03-one-interface-every-client/tests/test_exit_criteria.py::test_exit_4_an_mcp_client_lists_the_agents_tool_and_runs_it; packages/chassis/tests/test_mcp_interface.py::test_calling_the_tool_returns_the_native_response_envelope` (the echo workloads are the simplifier, `packages/workloads/README.md`).
- `051 H-13`, box "A contract test fails if an MCP tool's input schema differs": `· Delivered in PoC-3: packages/chassis/tests/test_mcp_interface.py::test_tool_input_schema_is_the_openapi_request_schema_of_run; pocs/poc-03-one-interface-every-client/tests/test_exit_criteria.py::test_exit_5_mcp_tools_and_rest_come_from_one_openapi_spec`.
- `054 H-16`, box "A tool that is not on the key's allow-list is refused by the gateway": `· Delivered in PoC-5: packages/chassis/tests/test_tool_gateway_contract.py::test_the_unlisted_probe_is_refused_and_its_marker_never_seen; pocs/poc-05-sandboxed/tests/test_poc05_kind_tool_gateway.py::test_unlisted_probe_is_refused_with_its_control, pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py::test_mcp_gateway_refuses_the_workload_without_the_chassis_key (kind, by hand); pocs/poc-05-sandboxed/notes/2026-10-09-close-runs.md` section 6. The chassis has no allow-list of its own (W5-06), so "with the chassis check switched off" holds by construction.
- `054 H-16`, box "Code sent to the code-execution tool runs in its sandbox": `· Delivered in PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_kind_code_runner.py::test_code_runs_on_gvisor_with_no_egress, ::test_files_do_not_cross_calls` (kind, `remote-lane.yml`).
- `055 CH-6`, box "A fake workload runs through the `remote` lane in CI on every commit": `· Delivered in PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_remote_lane_contract.py::TestPoc05ThreeLanes` (offline, the same `LaneContract` as `packages/chassis/tests/test_lane_contract.py::TestChassisLanes`); `pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_lane.py::test_remote_gives_the_sidecar_events_for_the_same_request` (kind, `remote-lane.yml`).
- `055 CH-6`, box "A remote pod without its credential is refused by the proxies": `· Delivered in PoC-5: packages/chassis/tests/test_remote_auth.py::test_no_token_and_a_wrong_token_are_401_with_one_body; pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_controls.py::test_h17_listener_answers_only_the_remote_token_inside_a_run, ::test_h30_remote_reaches_no_other_pod` (kind, `remote-lane.yml`; LiteLLM and its MCP gateway share port 4000).
- `055 CH-6`, box "The admission check refuses an `untrusted` workload, and a third-party image": `· Delivered in PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_admission_static.py::test_policy_has_every_rule_and_the_params_check; pocs/poc-05-sandboxed/tests/test_poc05_kind_admission.py::test_rejected_fixture_is_refused_with_its_rule_message` (kind, in `REMOTE_TESTS`); `pocs/poc-05-sandboxed/notes/2026-10-09-close-runs.md` section 6.
- `055 CH-6`, box "Every pipeline stage runs on remote calls": `· Delivered in PoC-5, PoC-8: pocs/poc-08-cross-cutting-build/tests/test_poc08_c7_stages_redteam.py::test_every_stage_runs_in_every_lane; pocs/poc-05-sandboxed/notes/2026-10-02-blind-spots.md` section 1; `docs/contracts/contract-v5.md#b8-what-the-chassis-cannot-see-or-control-with-a-plain-remote`.
- `055 CH-6`, box "gVisor's latency and memory overhead are measured": `· Delivered in PoC-5: pocs/poc-05-sandboxed/notes/2026-10-09-gvisor-overhead.md` "Result".
- `057 H-19`, box "Both containers of a pod deployed with the Helm chart run with a read-only root": `· Delivered in PoC-9: pocs/poc-09-agent-mvp-template/tests/test_poc09_chart.py::test_sidecar_lane_containers_are_hardened, ::test_remote_lane_renders_the_chassis_pod_and_a_gvisor_workload_pod` (render tests; `poc09-helm.yml`).
- `058 CH-8`, box "The workload for each supported framework passes the contract suite over A2A": `· Delivered in PoC-6: pocs/poc-06a-bake-off-sidecar-lane/tests/test_poc06a_engine_contract.py::TestEngineInMemory, ::TestEngineOverA2A; pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_remote_engine_contract.py::TestRemoteEngineOverA2A` ("localhost" is a Unix socket).
- `058 CH-8`, box "The non-Python workload passes the same contract suite": `· Delivered in PoC-6: pocs/poc-06a-bake-off-sidecar-lane/tests/test_poc06a_criteria.py::test_criterion_2_the_typescript_agent_passes_through_the_generic_sidecar_connector; docs/contracts/contract-v5.md#c4-the-evidence-and-the-one-list-of-changes`.
- `058 CH-8`, box "The chassis imports no framework package": `· Delivered in PoC-1: pocs/poc-01-walking-skeleton/tests/test_day0.py::test_import_rules_hold` (the "no agent framework anywhere in the chassis" contract in `pyproject.toml`).
- `058 CH-8`, box "Token deltas and tool-call events stream for every mapped framework, or the gap is listed": `· Delivered in PoC-6: pocs/poc-06a-bake-off-sidecar-lane/notes/2026-10-09-scorecard.md` "Criterion 2" and "Criterion 3" (gaps: smolagents sends one delta; `kagent-adk` shows no `tool_call`).

### Ticks after a body edit

These tick once the finding's rewrite lands.

- `051 H-13`, box 6 reduced to "the chassis version, the lane, and the trust value" (after W5-17): `packages/chassis/tests/test_manifest.py::test_manifest_matches_the_config_and_the_openapi_spec, ::test_manifest_agent_trust_shows_untrusted_with_the_remote_lane`. The image digests become their own open box.
- `054 H-16`, the first half of box 11, "The MCP adapter and the fake tools pass the same `ToolPort` contract suite, and are picked by `spec.adapters.tools`" (after W5-11): `packages/chassis/tests/test_contracts.py::TestInMemoryTools`, `packages/chassis/tests/test_tool_gateway_contract.py::TestMcpGatewayTools`, `packages/chassis/tests/test_tool_endpoint.py::test_fake_profile_builds_the_fake_tools`.
- `058 CH-8a`, new box "`agentctl new` defaults to LangGraph and scaffolds a `sidecar` agent that passes the lane contract" (after W5-05): `packages/agentctl/tests/test_agentctl_cli.py::test_the_defaults_scaffold_a_langgraph_sidecar_agent_with_a_first_commit, ::test_the_template_default_framework_matches_the_rules`. "and the evals" stays open.
- `059 H-15a`, box 7 rewritten to "an untrusted framework with `--connector sidecar` fails with a clear error", if W5-02 lands as a rewrite: `packages/agentctl/tests/test_agentctl_cli.py::test_an_untrusted_framework_is_refused_in_the_sidecar_lane; packages/agentctl/tests/test_agentctl_config.py::test_agentctl_refuses_what_the_chassis_refuses`.
- `062 H-11`, box 4 rewritten to agent-testkit (after W5-10): `packages/agent-testkit/tests/test_agent_testkit_isolation.py::test_the_kit_imports_with_the_chassis_unimportable`.
- `055 CH-6a`, box 2, once T10 runs (what is left, section 4) and the two hand-run kind files join `REMOTE_TESTS` (W5-03): `test_poc05_kind_remote_controls.py::test_remote_holds_one_secret_its_own_token, ::test_h19_remote_reaches_no_outside_address, ::test_h21_root_is_read_only_and_tmp_is_writable` already pass in `remote-lane.yml` for the secret, egress, and write halves.

## Status bullets folded

- `051 H-13`, "Status after PoC-3": facts are ticks. FastMCP 4, `/v1/mcp`, and `spec.interfaces.mcp` move into `## What` (W5-17). Service operations, digests, scopes, and governance are open boxes. Auth on MCP calls is now built (contract v6, D.1), but the 403 test is missing (box 4, `tick-blocked`). The `isError` gap and the private `tool._client` reads are open boxes, from the PoC-3 debt note. The LiteLLM gateway grant is 026 CH-4's: out of scope.
- `054 H-16`, "Status after PoC-2": facts are ticks (box 6). Charging tool calls to the run is open box 9.
- `054 H-16`, "Status after PoC-5": facts are ticks (boxes 3, 10). The bare-name and undeclared-key gaps, and the two `suggested:` fixes (`unknown_tool` outside a run; resolve a bare name to the one `<server>-<name>` match), become one new open box (W5-23). B12 closed for the code runner: a fact for `## What`.
- `054 H-16`, "Status after PoC-6": `acronym_expand` and the `<server>-<tool>` naming move into `## What`. Plain-A2A tool calls invisible to the chassis: already in contract v5 B.8, so drop the bullet.
- `054 H-16`, "Status after PoC-8": the two red-team misses stay with box 8 (`tool-04-base64`, `exfil-03-write-tool-gets-secret`, `pocs/poc-08-cross-cutting-build/notes/2026-10-29-redteam.md`).
- `055 CH-6`, "Status after PoC-5": facts are ticks (boxes 1, 3, 4, 6, 7). Bearer token, ValidatingAdmissionPolicy, and agent-sandbox move into `## What` as "Decided in ADR-005" (W5-13). The dispatcher's token and `ipBlock`, the claim-delete denial of service, and the `/dev/shm` cap move to `## Out of scope` as accepted, citing ADR-005 Consequences. T10 is box 2, owned by the user. The managed-runtime adapter goes to 055b.
- `055 CH-6`, "Status after PoC-6": plain-A2A mode and the four gVisor remotes move into `## What`, citing contract v5 part B. "The kind results wait on the first `poc06-kind.yml` run" is stale: what is left section 4 says that job passes its contract and hostile checks. The two unapplied PoC-6b findings become open boxes (W5-15).
- `057 H-19`, "Status after PoC-4": the detector facts move into `## What`. The counter of failed tool listings is out of scope (no request data). The template CI step, the lint rule, and the admission policy stay open boxes 1, 2, and 6.
- `058 CH-8`, "Status after PoC-6": the mapping sizes and "no engine needed an event change" move into `## What`. "Proposed default: PydanticAI" and ADR-006 "proposed" are stale (W5-21). The MCP stand-in goes to 058 CH-8a. The hosted numbers go on the owner list (what is left, section 1).
- `058 CH-8`, "Status after PoC-9": a fact for box 1 and for `## What`. "The TypeScript agent is not in the template" moves to `## Out of scope`, citing ADR-006 D5.
- `059 H-15`, "Status after PoC-9": the built flags move into `## What`. "Trust follows the framework; there is no `--trust` flag" is W5-02. The `orchestrator` and `data` kinds, `spec.modules`, and `governance` go to 059b. The chart in the generated repo goes to 059a, with 025 H-10a.
- `060 H-23`, "Status after PoC-5": a fact. It moves into `## What` as the bar for box 1.
- `063 S-8`, "Status after PoC-6": replaced by a "Parked on 2026-11-06" note that carries the PoC-6c baseline (W5-20).
- Inputs never applied, named for these issues:
  - the PoC-6c "to apply" lines for 063 S-8 (W5-20);
  - the PoC-6b review items for 055 CH-6 and 054 H-16 (W5-15, W5-23);
  - the PoC-4 item for 051 H-13 (OpenAPI limits stale after a reload), which W2a-20 already places in 010 H-12;
  - the PoC-5 item "a CI job for the sidecar kind suite" (`pocs/poc-05-sandboxed/notes/backlog-changes.md` line 55). It is now half stale: the sidecar-controls and admission files are in `REMOTE_TESTS`, while `test_poc05_kind_tool_gateway.py` and `test_poc05_kind_hardreq1.py` are not. That belongs to 026 CH-4 (W2b).

  The PoC-7 draft names 054 H-16 only, for the two red-team misses, which the issue already carries. The PoC-9 w2–w5 report names 058 and 059 only. Its W2 "publish the packages" item feeds 025 H-10a and 059a, and its W5 item says "024 CH-3 and 058 CH-8 hold".

## Findings

### W5-01 [HIGH] [rule 4, 9, 12] docs/planning/tools/backlog.py
Problem: 059 H-15 (phase F) depends on 055 CH-6, whose open box 5 needs 038 X-1a (first-cloud Terraform) and DEC-1. It also depends on 051 H-13, which waits on 050 CH-5, and on 008 H-14, whose class and kind half W2a-08 makes `later`. No phase F box needs service operations, a managed runtime, or the B.5 modules.
Why it matters: phase F's done-when ("`agentctl new` to a demo in under a day") cannot close while its issue waits on cloud infrastructure and on issues no phase schedules.
Fix: split 059 into 059 H-15a and 059 H-15b. 059 H-15a is phase F: boxes 5, 6, 7, 8, and 9, plus "the generated repo pins a published platform release". It depends on 025 H-10a, 024 CH-3, 057 H-19, 058 CH-8a, and 055 CH-6a. 059 H-15b is `later`: boxes 1 to 4 and 10, the five kinds, and modules and governance. It depends on 008 H-14b, 051 H-13, and 059 H-15a. C2 picks one home for "the chart in the generated repo": 025 H-10a (W2b-08) holds the chart template, and 059a renders it.
Status: open

### W5-02 [HIGH] [rule 11] docs/planning/issues/059-H-15-agent-factory-cli.md
Problem: boxes 6 and 7 want `--trust trusted|untrusted`. `agentctl` has no `--trust` flag: trust follows the framework (`rules.trust_of`; the copier `trust` question has `when: false`), and ADR-006 decision 2 fixes each engine's lane. A `remote` agent also gets no deploy files, yet a `copier.yml` comment says it "runs under deploy/helm", which the generated repo does not have.
Why it matters: the two boxes ask for a behavior the code chose against, so they can never tick. Phase F needs a remote agent to deploy from the generated repo.
Fix: goes to `proposals.md`. Either rewrite boxes 6 and 7 to the framework rule ("an untrusted framework renders a `remote` chart"; "an untrusted framework with `--connector sidecar` is refused", which ticks today), or add `--trust` for a trusted framework run as untrusted. Box 6 stays open until the generated repo has a chart.
Status: proposal

### W5-03 [HIGH] [rule 9, 4, 14] docs/planning/issues/055-CH-6-remote-lane-trust-rule.md
Problem: five of seven boxes are met on kind and in CI. The two left have different owners and phases. Box 2 waits on the T10 probe (owned by the user, phase A) and on the hand-run gateway file. Box 5, the managed runtime, waits on the first cloud from DEC-1 and on 038 X-1a.
Why it matters: phase A lists the T10 probe, but its issue cannot close while it carries cloud work. Every issue that depends on 055 (056, 059, 077, 116) inherits the 038 X-1a wait.
Fix: split. 055 CH-6a is phase A. It keeps boxes 1 to 4, 6, and 7, and adds "`test_poc05_kind_tool_gateway.py` runs in `remote-lane.yml`" to box 2. It depends on 009 CH-1, 010 H-12, 026 CH-4, and 024 CH-3 (the chart renders the remote lane). Resize S. 055 CH-6b is `later`, size M. It holds box 5, the "one relaxation" bullet, and the managed-runtime blind-spot rows, and depends on 001 DEC-1 and 038 X-1a. Point 059a at 055a, and 116 X-1b at 055b.
Status: open

### W5-04 [HIGH] [rule 11, 14] docs/planning/issues/056-CH-7-chassis-release-rings.md
Problem: `docs/specs/architecture.md` (line 93) and `docs/specs/security.md` (the admission row) say a minimum chassis version rule lives in `deploy/kind/poc05/admission/`. No file there has a version rule. 056 box 4 is the only box that asks for one.
Why it matters: phase A's done-when is "the chart carries the egress and admission rules", and the specs make it look built. The rest of 056 (rings, Argo) is `later`, so the rule would wait with it.
Fix: move box 4 out of 056 into the phase A admission box that W2b-02 adds to 026 CH-4 ("the admission rules, including the minimum chassis version, ship in the chart"), and leave 056 `later`. The spec sentences go to `proposals.md`: say "not built" until the rule exists.
Status: proposal

### W5-05 [HIGH] [rule 14, 9] docs/planning/issues/058-CH-8-framework-event-mappings.md
Problem: phase C ("LangChain first") lists work with no box anywhere: the `langchain-mcp-adapters` swap on `mcp` 2 (or a test that fails when it is ready), a trace-join test that shows LangGraph's graph steps under the run span (ADR-006 decision 1), a `langchain` `create_agent` variant in the template, and the checkpointer and interrupt ADRs (gap P3). Together with box 2 and the guide, that is more than 5 days.
Why it matters: phase C has no backlog home besides this issue. The ADR-006 Revisit triggers stay open with no owner.
Fix: split. 058 CH-8a is phase C, size M. It keeps box 2 and the guide, and adds three boxes: the MCP adapter swap or its failing-when-ready test; the LangGraph trace-join test; and "`agentctl new` defaults to LangGraph and passes the lane contract and the evals", whose first half ticks today. 058 CH-8b is phase C, size M. It adds the `create_agent` variant and the two ADRs (`chassis-architect`).
Status: open

### W5-06 [HIGH] [rule 11] docs/planning/issues/054-H-16-tool-port.md
Problem: `## What` and box 4 want a chassis config allow-list (`tools` with `name` and `mode`), with write tools refused unless `mode: write`. The gateway adapter "adds no allow-list of its own", and no such config field exists. Write mode comes from the tool's `readOnlyHint` (`packages/chassis/tests/test_tool_gateway_contract.py::test_a_tool_with_no_read_only_hint_is_a_write_tool`), and a write tool is not refused: it only needs an idempotency key.
Why it matters: the box asks for a second check the design dropped. The gateway's per-key allow-list is the only check today, which the security spec accepts as the boundary.
Fix: goes to `proposals.md`. Either rewrite `## What` and box 4 to the built rule (the gateway allow-list is the only allow-list; a tool with no `readOnlyHint` is a write tool and needs an `idempotency_key`), or build the config allow-list. Box 2's "logged" half stays open either way (W5-11).
Status: proposal

### W5-07 [HIGH] [rule 13, 4] docs/planning/issues/054-H-16-tool-port.md
Problem: box 1, the fourth `## What` bullet (native calls or JSON forced by guided decoding), and the Reuse line "vLLM tool parsers and guided decoding" are the SLM path. `ChatCompletionRequest` drops `response_format`, and nothing passes guided-decoding parameters. 054 also depends on 012 H-3, whose vLLM half is parked (W2a-01), and on 013 CH-2, needed only for the guided path.
Why it matters: a live issue may not depend on a parked one, and box 1 can never close on the router-only path.
Fix: move box 1 and the guided-decoding text to `## Out of scope` as "Parked on 2026-11-06 (product plan)". In `ROWS`, drop `H-3` and `CH-2` from 054's deps. Add `H-4` (box 7, the fallback route) and `H-6` (box 8, the `tool_output` guard).
Status: open

### W5-08 [HIGH] [rule 4, 12] docs/planning/tools/backlog.py
Problem: 058 CH-8 (phase C) depends on 025 H-10, whose live half is phase F (W2b-08). The mappings already ship in the template (`packages/agentctl/tests/test_agentctl_render.py::test_the_example_logic_is_the_echo_workload`), so no open 058 box needs 025.
Why it matters: phase C would wait on the last phase, F.
Fix: in `ROWS`, drop `H-10` from CH-8's deps and keep `H-8`. 058 CH-8a and 058 CH-8b depend on 015 H-8 only.
Status: open

### W5-09 [HIGH] [rule 11] docs/planning/issues/053-H-22-event-reliability.md
Problem: box 4 wants a schema-invalid event in the dead-letter topic with no retries. Both the Kafka and Dapr adapters drop a body that is not a CloudEvent (`packages/chassis/tests/test_dapr_inbound.py::test_a_body_that_is_not_a_cloudevent_is_drop`; the Kafka `_consume`).
Why it matters: a bad event vanishes with no record, which the issue's own "must not get lost" rules out.
Fix: goes to `proposals.md` (dead-letter it, or drop and count it). Box 4 stays open.
Status: proposal

### W5-10 [HIGH] [rule 8, 11] docs/planning/issues/062-H-11-adapter-package.md
Problem: box 4 and the fourth `## What` bullet publish the `chassis` package so that a service's tests run `handle` in `inprocess`. ADR-002 says a workload never depends on `chassis`. The architecture spec says a workload's own tests never use `inprocess`. `agent-testkit` runs the chassis as its own process.
Why it matters: the box asks for what an accepted ADR removed, so it can never tick.
Fix: rewrite box 4 to "a service's tests run the stock chassis in the `fake` profile through `agent-testkit`, offline, with no keys, and never import `chassis`", citing ADR-002, then tick it. Rewrite box 3 to "the chassis image and the platform packages carry one version from one CI release", depending on 025 H-10a.
Status: open

### W5-11 [MEDIUM] [rule 1] docs/planning/issues/054-H-16-tool-port.md
Problem: four boxes are partly met, with no test for the missing half:
- Box 2: refused, but never shown logged (`test_tool_idempotency.py::test_the_allow_list_refuses_before_the_tool_runs`).
- Box 5: keys derive per run, but no request-retry test runs end to end.
- Box 8: blocked by the guard, but "adds no tool calls" and "the allow-list is unchanged" are never asserted (`test_guard_stages.py::test_tool_output_injection_is_an_mcp_error_the_workload_never_reads`).
- Box 11: the suite runs on the ports, never through the proxy in each lane.
Why it matters: these read as done in the status bullets, and they are not.
Fix: `tick-blocked`. Name the tests that would prove each half:
- Box 2: a log or metric line carrying `tool_denied`.
- Box 5: a client retry with one `Idempotency-Key` that produces one side effect on the fake write tool.
- Box 8: a red-team case that counts tool calls before and after the injection.
- Box 11: the `ToolPortContract` bound through `/mcp` in `inprocess` and `sidecar`.

Split box 11, so its first half ticks as listed.
Status: open

### W5-12 [MEDIUM] [rule 3, 14] docs/planning/issues/054-H-16-tool-port.md
Problem: after the ticks and the parked box, seven boxes are open or partly met (2, 4, 5, 7, 8, 9, 11), plus W5-23's new box. The issue names no phase.
Why it matters: size M (2.5 days) under-counts the work. Boxes 8 and 9 are the tool guard and the budget guard that phase E maps to the OWASP agentic list (ASI02).
Fix: resize M to L, and set phase E. Box 7 (retry, then the fallback route on a malformed tool call) can join phase B's retry stage with 017 H-4, if C2 prefers.
Status: open

### W5-13 [MEDIUM] [rule 5, 6] docs/planning/issues/055-CH-6-remote-lane-trust-rule.md
Problem: `## What` and Reuse still offer "suggested: mTLS through cert-manager, or a per-remote token", "Kyverno or ValidatingAdmissionPolicy", and "agent-sandbox ... or E2B" (054). ADR-005 settled each one: the token (decision 1), ValidatingAdmissionPolicy with no Kyverno (decision 4), and agent-sandbox with one sandbox per call (decision 6).
Why it matters: an engineer would reopen settled choices.
Fix: replace each with "Decided in ADR-005" and drop the `suggested:` markers. Keep mTLS only as ADR-005's Revisit trigger. Apply the same to 054's code-execution bullet.
Status: open

### W5-14 [MEDIUM] [rule 5] docs/planning/issues/056-CH-7-chassis-release-rings.md
Problem: 056 (`## What`, Reuse) and 057 (`## What`, Reuse, `## Out of scope`) plan the version and read-only-root rules in Kyverno. ADR-005 decision 4 chose ValidatingAdmissionPolicy, "no Kyverno", and PoC-5's admission set is built on it.
Why it matters: two issues would add a second policy engine that the ADR ruled out.
Fix: in both issues, replace "Kyverno" with "ValidatingAdmissionPolicy, in the `agent-trust-rule` set (ADR-005 decision 4)". Tell W3, the 038 X-1a owner, that 038 still installs Kyverno.
Status: open

### W5-15 [MEDIUM] [rule 10] docs/planning/issues/055-CH-6-remote-lane-trust-rule.md
Problem: two PoC-6b review items for 055 were never applied: no cap on a remote's output (MEDIUM), and an `except ValueError` that is too wide (LOW), from `pocs/poc-06b-bake-off-remote-lane/notes/backlog-changes.md` lines 20 and 21. The PoC-6 status line about `poc06-kind.yml` is stale.
Why it matters: an unbounded remote output is a budget and denial-of-service hole in the lane meant for untrusted code.
Fix: add an open box to 055 CH-6a: "a remote's output over `spec.limits` ends the run with a fixed error". Send the `except` item to `proposals.md`. Drop the stale clause.
Status: open

### W5-16 [MEDIUM] [rule 5, 6, 3, 4] docs/planning/issues/053-H-22-event-reliability.md
Problem: four parts of 053 have drifted from the code:
- `## What` suggests the topic `agents.<agent>.deadletter.v1` and the keys `events.retry.*`. Contract v3 decided `<topic>.dlq` (`ports/events.py` `DLQ_SUFFIX`), `max_attempts=3`, and a backoff of 0.2 s, then 1 s.
- Box 6 orders by `run_id`, but result events key on the sha256 of the idempotency key (W2b-11).
- Reuse says "the broker's own dead-letter feature where it has one". ADR-004 item 8 refuses that: Dapr's own dead-lettering fails the suite with two strict xfails.
- No box covers a failed dead-letter publish, which ends the consumer (W2b-13).
Why it matters: size S (1 day) for seven open boxes, a replay command, and a consumer-side duplicate drop under-counts the work. Boxes 1, 5, and 7 need a consumer (021 H-20) and the recorder (040 D-0).
Fix: cite contract v3 for the name and the backoff, and drop the `suggested:` markers. Rewrite box 6 to "one partition key". Strike the broker-native line, citing ADR-004 item 8. Add W2b-13's box. Resize S to M. In `ROWS`, add `D-0` to the deps. The phase is `later`.
Status: open

### W5-17 [MEDIUM] [rule 1, 3, 5, 6] docs/planning/issues/051-H-13-openapi-mcp-tools.md
Problem: five parts of 051 are stale or only partly met:
- Box 1 is met only by a version-string check and Schemathesis on the echo (`pocs/poc-03-one-interface-every-client/tests/test_openapi_props.py::test_the_spec_is_openapi_3_1_with_every_operation_named_and_labeled`), with no validator and no CI artifact.
- Box 4 has no 403 test on `/v1/mcp` (`test_run_scope_is_needed_on_every_run_interface` covers the other three routes).
- Box 6 mixes built fields with image digests nothing produces.
- `## What` says "built with the MCP Python SDK", but it is FastMCP 4 (ADR-003 item 6).
- `GET /openapi.json` and the scopes (`agent:read` for `/manifest`; `agent:run` for `/v1/mcp`, contract v6 D.1) are settled, but still marked `suggested:`.
Why it matters: six boxes stay open, which is more than size S.
Fix: `tick-blocked` boxes 1 and 4: add an `openapi-spec-validator` test with the spec saved in CI, and a `/v1/mcp` 403 case. Split box 6, as in "Ticks after a body edit". Cite FastMCP 4 and contract v6, and drop the markers. Resize S to M. The phase is `later` (the registry readers 076 and 083 are `later`).
Status: open

### W5-18 [MEDIUM] [rule 3, 4, 12] docs/planning/issues/057-H-19-stateless-check-ci.md
Problem: box 3 names "the simplifier, the `evaluator-facts` agent, and the recorder". The echo workloads are the simplifier. `evaluator-facts` (042 A-2) is an open question in ADR-010, and the product plan puts the judge behind `EvaluatorPort`. The recorder is 040 D-0, which is not built. 057 depends on all of 025 H-10, though only its phase F half (025 H-10a, W2b-08) carries the template CI. The template CI has no stateless step (`ci.yml.jinja`), and `agent-testkit` has no stateless suite. Box 4's order is fixed (A, then B), not shuffled.
Why it matters: box 3 can never close as written, and five boxes of new work exceed size S.
Fix: rewrite box 3 to "the template's workload for each supported framework passes". Resize S to M. Point the dependency at 025 H-10a, and add 015 H-8 (the kit's suite). Set phase F.
Status: open

### W5-19 [MEDIUM] [rule 13, 4] docs/planning/issues/060-H-23-broker-adapters.md
Problem: Reuse keeps "the `dapr` adapter stays as a tested alternative". The product plan parks Dapr ("What is reused"). Box 1 wants duplicate drop, retry, and dead-letter on every new broker. Those are 053 H-22's behaviors, and 053 is not among 060's deps. The duplicate-drop case is not in `EventPortContract` yet.
Why it matters: an adapter would bind a suite that lacks the case its box asks for.
Fix: replace the Dapr line with "Dapr: parked on 2026-11-06 (product plan)". In `ROWS`, add `H-22` to H-23's deps. The broker pick goes to the DEC-1 pack (ADR-004 item 5 keeps `events: none` in every profile until then).
Status: open

### W5-20 [MEDIUM] [rule 13, 10] docs/planning/issues/063-S-8-tool-call-finetune.md
Problem: the issue fine-tunes a Qwen3 SLM. The product plan parks the SLM track, and 033 S-5 is not built. The PoC-6c "to apply" lines were never folded in: "Qwen3-1.7B, not fine-tuned, passed smoke, simplifier, and lookup 5 of 5 on every trusted engine and lane (135 of 135 runs)", CPU only, on a 4-CPU runner. The issue still says "No number exists yet".
Why it matters: the baseline already passes the bake-off tasks, so the case for the fine-tune is weaker than the issue says.
Fix: phase `parked`. Replace the PoC-6 bullet with "Parked on 2026-11-06 (product plan). Baseline from PoC-6c: 135 of 135 on CPU (`pocs/poc-06c-pretrained-slm/notes/backlog-changes.md`)". No live issue depends on 063.
Status: open

### W5-21 [MEDIUM] [rule 5, 10] docs/planning/issues/058-CH-8-framework-event-mappings.md
Problem: `## What` still says "suggested: PydanticAI, LangGraph, and OpenAI Agents SDK, pending the PoC-6 decision". The PoC-6 bullet says "Proposed default: PydanticAI" and ADR-006 "proposed". ADR-006 is amended (2026-11-06): LangGraph is the default, and decision 2 fixes the supported set (plain Python, PydanticAI, LangGraph, and the OpenAI Agents SDK in `sidecar`; smolagents and Claude in `remote`). Commit `ed1e834` made the code change. ADR-006 decision 1 still says it is "not done".
Why it matters: an engineer would build against the old default.
Fix: replace both with "Decided in ADR-006 (amended 2026-11-06)", citing decision 2's set. Drop the `suggested:` marker. Send the stale ADR-006 sentence to the `adr` skill owner as a note.
Status: open

### W5-22 [MEDIUM] [rule 3, 4] docs/planning/issues/056-CH-7-chassis-release-rings.md
Problem: none of the 7 boxes is met:
- Box 5's `/manifest` reports the Python package version (`CHASSIS_VERSION = __version__`), not the image tag the rings roll out (`poc09` in the chart).
- Box 6 needs a second event-schema major, which does not exist (W2a-15).
- Box 1 needs Argo CD, which only 038 X-1a would install.
Why it matters: size M under-counts seven open boxes of cluster work, and the deps miss two real needs.
Fix: resize M to L. In `ROWS`, add `H-13` (`/manifest`) and `X-1a`. Add an open box: "the chassis version in `/manifest` equals the image tag". The phase is `later`.
Status: open

### W5-23 [MEDIUM] [rule 10] docs/planning/issues/054-H-16-tool-port.md
Problem: two items from earlier PoCs have no open box:
- The PoC-5 status names a gap: a write tool called by its bare name, or one that does not declare the key, never gets the idempotency key. It also names two `suggested:` fixes.
- The PoC-6b review found `follow_redirects=True` on a tool call (LOW, `pocs/poc-06b-bake-off-remote-lane/notes/backlog-changes.md` line 22). It was never applied.
Why it matters: box 5 ("performs the side effect once") can be bypassed by a name.
Fix: add an open box: "a bare tool name resolves to its one `<server>-<name>` match, or is refused; outside a run, an unknown name answers `unknown_tool`". Send `follow_redirects` to `proposals.md`.
Status: open

### W5-24 [MEDIUM] [rule 14] docs/planning/issues/059-H-15-agent-factory-cli.md
Problem: phase F's done-when needs a generated transformer that answers on all four APIs. Box 5 is shown for native, OpenAI, and MCP only (`pocs/poc-09-agent-mvp-template/notes/2026-11-05-machine-checks.md` section 1, run with `pydanticai` before the default moved). The Anthropic route was never called.
Why it matters: phase F would close on a run of the old default, with one interface unshown.
Fix: keep box 5 open in 059a. The proof is a `demo.sh --live` rerun on the LangGraph default that calls `/v1/messages`, recorded as a dated note.
Status: open

### W5-25 [LOW] [rule 7] docs/planning/issues/052-H-21-asyncapi-spec.md
Problem: the issue says AsyncAPI 3.0 in its title and boxes, but 3.1 in Reuse. It reads `events.consumes` and `events.produces`, while the config has `spec.events.result_events` and `spec.events.consume` (`server/config.py`). The `configversion` and `modelroute` extensions follow contract v3, not the issue's list.
Why it matters: an engineer would generate from keys that do not exist.
Fix: pick one AsyncAPI version (3.0 matches the epic), and name the real config keys, citing contract v3 for the extensions. The phase is `later`. The broker bindings wait on DEC-1.
Status: open

### W5-26 [LOW] [rule 13, 4] docs/planning/tools/backlog.py
Problem: 062 H-11 depends on 012 H-3, whose vLLM half is parked (W2a-01), and on 058 CH-8, which W5-05 splits.
Why it matters: a live issue may not depend on a parked half.
Fix: in `ROWS`, change H-11's deps to `H-2`, `CH-2`, and `CH-8` (the 058a half), and add `H-10` for the release (025 H-10a).
Status: open

### W5-27 [LOW] [rule 7] docs/planning/issues/061-H-5-a2a-adapter.md
Problem: the issue names the switch `interface.a2a`. The config uses `spec.interfaces.<name>` (`InterfacesSpec` has `openai`, `anthropic`, `mcp`).
Why it matters: wording only, but the new key should follow the existing shape.
Fix: write `spec.interfaces.a2a`. The phase stays `later` until DEC-1 decides the public endpoint (the product plan, gap P3).
Status: open
