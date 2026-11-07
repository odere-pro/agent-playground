# Findings W89: W8 Platform MCP server and W9 Orchestrator

Session: W89. Issues: 094–115. Waves: W8, W9.
Rubric: [rubric.md](rubric.md).

## Summary

The PoC track built none of W8 or W9. There is no Temporal, orchestrator, workflow file, registry, memory port, KEDA, audit emitter, or platform MCP server. What exists are per-request pieces: the per-agent MCP tool from PoC-3, the tool endpoint and gateway adapter, per-request budgets, drain and kill drills from PoC-4, the idempotency store, and the CloudEvents port. None satisfies a box as written. All 22 issues are `keep`, phase `later` (the product plan puts orchestration of many agents after the MVP, and 104 O-3 at `later`). Nothing retires, ticks, or parks. The top risks are: a live phase A issue (122 X-5) waits on 104 O-3; the Temporal-worker-in-the-chassis design (ADR-001 follow-up (b)) has no ADR; and eight sizes are too small for the code facts. The longest chain 099 → 100 → 103 → 108 → 112 holds hop by hop (W89-03).

## Issues

| Issue | Boxes | Phase | Verdicts | Evidence or reason |
| ----- | ----- | ----- | -------- | ------------------ |
| 094 P-1 | 0/7 | later | keep, resize M, drift, deps, tick-blocked | No platform server, `registry_*`, or `agent_list`. Per-agent MCP only: `packages/chassis/tests/test_mcp_interface.py::test_the_inner_hop_forwards_only_the_correlation_headers`. Streaming half of box 3 is refused by ADR-003 item 6 (W89-08); `agent_run` needs an outbound agent call nothing builds before it (W89-07) |
| 095 P-4 | 0/6 | later | keep, drift, cite | Only `readOnlyHint` and `agent:run` exist. No `mcp:*` scopes, elicitation, audit event, or tag check. ADR-008 item 2 names other scopes (W89-09); the CI tag check is hidden by a default (W89-10) |
| 096 P-2a | 0/6 | later | keep, cite | Needs 070 D-4, 069 D-7, 068 D-3, 065 D-2: none in code. `metrics_get` "matches the savings dashboard" names the half of 006 G-6 that is parked; point it at the container dashboard (046 A-5) |
| 097 P-3 | 0/6 | later | keep, cite | `/manifest` is served and tested (`test_manifest.py::test_manifest_is_not_an_mcp_tool` says it is not a tool). No resources, prompts, lakeFS, or Loki in the repo (W89-23) |
| 098 O-10 | 0/6 | later | keep, deps, drift | No `OrchestratorConfig` or `Workflow`. Machinery exists for `ChassisConfig`: `test_config_loader.py::test_start_fails_on_an_invalid_document_and_names_the_field_not_the_value`. The loader has no `kind` dispatch (W89-17) |
| 099 O-1 | 0/7 | later | keep, deps, drift | No `RegistryPort`, agent port, or `orchestrator` kind (`agentctl` `KINDS` = transformer, evaluator, tool). `fallback` status exists only for the evaluator gate. Deps and ADR gap: W89-02, W89-18 |
| 100 O-2 | 0/8 | later | keep, resize L, deps | No Temporal in `deploy/` or `pyproject`; `spec.operations` is refused at load. 8 open boxes, three of them new infrastructure (W89-03) |
| 101 O-13 | 0/5 | later | keep, resize M, tick-blocked | `ConfigPort.load(name)` has no version argument, so the pinning helper needs a port change. Request-level behavior ("a run keeps the Context it opened with", contract section 6) has no test (W89-16) |
| 102 O-7 | 0/5 | later | keep, cite, tick-blocked | Agent half exists: `test_idempotency_serve.py::test_complete_replays_on_another_replica`, `packages/fake-mcp-server/tests/test_fake_mcp_server.py::test_note_write_dedups_by_meta_key_and_counts_real_executions`. No step-key function. A run that ends in `error` frees its key, so an errored step re-runs (W89-24) |
| 103 O-11 | 0/6 | later | keep | No interpreter, `fan_out`, `compare`, or `on_failure` logic. Size L holds (6 open boxes, one owner). On the chain; deps hold |
| 104 O-3 | 0/7 | later | keep, resize L, deps, drift | No task-queue adapter, KEDA, or pool metrics. PoC-4 and PoC-9 prove only request-level drain, kill, and CPU HPA 2→4 (W89-01, W89-22). Blocks 122 X-5 and 120 X-7 |
| 105 O-4 | 0/6 | later | keep, drift, cite | No splitter (`split_for_classifier` is guardrail chunking), no `merger`. `agentctl` supports `transformer`. Simplifier and `evaluator-facts` wording (W89-06, W89-12) |
| 106 O-5 | 0/5 | later | keep, drift, cite | No `compare`, `repeat`, or `tie_rule`; no golden import (068 D-3). "SLM route vs big-model route" in box 1 (W89-06, W89-12) |
| 107 O-6 | 0/7 | later | keep, resize M, drift | Per-request only: `test_model_proxy.py::test_an_exhausted_run_is_refused_with_429_complete_and_streamed`, `test_a_spent_budget_refuses_the_next_model_call_with_budget_exhausted`. Envelope `Budget` has no cost field and no step count (W89-20) |
| 108 O-12 | 0/6 | later | keep, drift | No routing, planner, or `OrchestratorConfig`. Guided decoding and `big-planner` have no home (W89-13). Size L holds. On the chain |
| 109 O-8 | 0/6 | later | keep, resize M, drift | Chassis cancels one request (`test_client_disconnect.py::test_a_complete_call_whose_client_leaves_is_cancelled`), not a run. `input-required` is an error (`test_remote_connector_plain.py::test_input_required_is_an_error_and_the_task_is_canceled`). No write scope (W89-14) |
| 110 C-6 | 0/7 | later | keep, deps, drift | `GovernanceSpec` has `risk_class` and `ai_generated_marking` only; `human_oversight` is built nowhere. Reviewer role is an open question in ADR-009 (W89-14) |
| 111 O-14 | 0/5 | later | keep, resize L, drift | `ConfigPort` is read-only; no active pointer, no `config.config.changed.v1` emitter, no run-level "write tools off" (W89-21) |
| 112 O-16 | 0/7 | later | keep, drift, deps | `spec.events.consume` is refused (`test_server.py::test_events_consume_is_refused_until_event_triggered_runs_exist`). Port half exists (partition order, dead-letter). On the chain (W89-19) |
| 113 O-9 | 0/7 | later | keep, split, drift | No `MemoryPort`, no pgvector in code or deploy, no run API. Feedback path conflicts with ADR-009 (W89-11, W89-15) |
| 114 P-2b | 0/6 | later | keep, drift | Wraps 109, 111, 113 APIs; none exists. `feedback_add` overlaps `/v1/feedback` (W89-11). `ConfigPort` has no write (W89-21) |
| 115 O-15 | 0/4 | later | keep | No planner, run store, or `save-workflow`. P2; deps 108, 111, 110 hold |

## Ticks

None. No box is met as written. Four boxes have a per-request or one-hop analogue that is not enough; each is `tick-blocked` and the proving test is named in W89-16. Cited tests that were run on 2026-11-06:

```
$ UV_NO_SYNC=1 scripts/check_offline.sh <10 test ids from this file> -q -p no:cacheprovider
........................                                                 [100%]
24 passed in 8.49s
```

The ten ids (all under `packages/chassis/tests/`): `test_mcp_interface.py::test_the_inner_hop_forwards_only_the_correlation_headers` and `::test_stream_true_in_the_arguments_is_read_as_false`, `test_tool_idempotency.py::test_mcp_lists_the_write_tool_with_read_only_hint_false` and `::test_the_same_write_twice_in_one_run_is_one_effect`, `test_server.py::test_events_consume_is_refused_until_event_triggered_runs_exist`, `test_client_disconnect.py::test_a_complete_call_whose_client_leaves_is_cancelled`, `test_remote_connector_plain.py::test_input_required_is_an_error_and_the_task_is_canceled`, `test_model_proxy.py::test_the_forwarded_max_tokens_is_capped_at_the_remainder` and `::test_an_exhausted_run_is_refused_with_429_complete_and_streamed`, `test_config_loader.py::test_a_reloadable_change_swaps_the_config`. Parametrized, so 10 ids ran 24 cases. Every other test id in this file was checked with `grep -rn "def test_name"` and not run.

## Status bullets folded

No issue in 094–115 has a "Status after PoC-N" bullet. The inputs never applied name none of these issues. Two PoC facts belong here and are folded as new "Status after PoC-N" bullets (W89-22, W89-20):

- `104 O-3`, PoC-4 drain, kill, and rolling-restart results (`pocs/poc-04-stateless-scalable/demo/2026-10-01-demo-scale.md`, `notes/2026-10-01-container-roles.md`) and the PoC-9 HPA drill (`pocs/poc-09-agent-mvp-template/notes/2026-11-05-kind-hpa-drill.md`, 2 to 4 replicas at 203% CPU, scale-down not waited for): facts, as a bullet. They do not tick a box.
- `107 O-6`, the per-request budget (contract section 7, "Budgets per call"; `docs/contracts/contract-v1.md` per-run budget): a fact, as a bullet.
- `011-what-is-left.md` sections 3 and 5: nothing for these issues.

## Findings

### W89-01 [HIGH] [rule 4] docs/planning/issues/104-O-3-agent-pools-keda.md
Problem: 104 O-3 (`later`) blocks 122 X-5 and 120 X-7. 122 X-5 is in the product plan's phase A (throughput at 4 pairs on Linux); 120 X-7 is the container dashboard family in phase E.
Why it matters: a live phase A issue cannot wait on orchestrator pools; PoC-4's Compose and kind scale work is the base for the throughput box.
Fix: drop `O-3` from the `ROWS` dependencies of 122 X-5 and 120 X-7; the "per pool" half of each moves to a box that waits on 104. W1012 decides; also 122 depends on 091 R-11 (registry, W7).
Status: open

### W89-02 [HIGH] [rule 5] docs/planning/issues/099-O-1-orchestrator-agent.md
Problem: 099, 100, 104, and 112 build on "the Temporal worker runs in the chassis" (ADR-001 follow-up (b) in `000-plan.md`). It is a suggestion with no owner sign-off and no ADR. ADR-001 item 1 says the chassis image has no business logic, and hard requirement 1 lists no Temporal credential. `temporalio` is not on the `forbidden_modules` list in `pyproject.toml`, so `make lint` would not stop it.
Why it matters: the critical path starts at 099; building it before the decision risks rework in four issues.
Fix: add an ADR (`chassis-architect`) before 099: where the worker runs, the Temporal credential, the task-queue adapter. Then add "Decided in ADR-0NN" to 099 and 100 and drop the `suggested:` marks.
Status: open

### W89-03 [HIGH] [rule 3] docs/planning/issues/100-O-2-temporal-checkpoints.md
Problem: 100 O-2 is size M with 8 open boxes: Temporal on Compose and kind, a pod-kill drill, a resume API, a replay test in CI, a run API on `spec.operations` (refused at load today), a recovery metric, and time-skipping tests.
Why it matters: it is on the longest chain, so its size sets the chain length. The deps hold hop by hop: 098 → 099 (config), 099 → 100 (the worker lives in the orchestrator's chassis), 100 → 103 (workflows run on Temporal), 103 → 108 (routing picks a workflow), 108 → 112 (events start a run through routing). Chain days: 2.5 + 2.5 + 5 + 5 + 5 + 2.5 = 22.5 with the resize (was 20).
Fix: resize L in `ROWS`. Keep the dependencies. The 050 CH-5 dependency is real (no `operations` key exists); W5 owns its verdict.
Status: open

### W89-04 [MEDIUM] [rule 3] docs/planning/tools/backlog.py (P-1, O-13, O-3, O-6, O-8, O-14 rows)
Problem: Six sizes do not fit their open boxes. 094 S→M (outbound agent call, token exchange: W89-07). 101 S→M (`ConfigPort` read-by-version plus contract suite: W89-16). 104 M→L. 107 S→M (a cost field and step count in the envelope: W89-20). 109 S→M (6 boxes, a write scope, pause surviving a restart). 111 M→L (a config writer: W89-21). 113 splits (W89-15).
Why it matters: W8 goes from 7 to 8.5 days and W9 from 46.5 to 61; the totals feed the plan. All are `later`, so the MVP total does not move.
Fix: apply the resizes in `ROWS`. Sizes stay `suggested:`.
Status: open

### W89-05 [MEDIUM] [rule 14] docs/planning/issues/100-O-2-temporal-checkpoints.md
Problem: The product plan's phase C needs a LangGraph checkpointer decision and an interrupt decision (an ADR each); gap P3 says an agent keeps no state and interrupts have no place in `handle`. No orchestrator issue covers them. 100's "checkpoint" is Temporal's event history for orchestrator runs; 113's short-term memory is the same. A2A `input-required` is an error today (`docs/specs/contract.md` line 197), and 109's approval is a Temporal signal, so it does not touch agent interrupts.
Why it matters: the gap has no issue, and 109 and 110 will need the interrupt ADR's outcome to say whether an agent can ever ask for approval.
Fix: add one phase C issue (size S, two ADRs, owner `chassis-architect`); 100 and 109 gain a one-line "Decided in ADR-0NN" once it lands. Do not stretch 100.
Status: open

### W89-06 [MEDIUM] [rule 13] docs/planning/issues/099-O-1-orchestrator-agent.md
Problem: Acceptance boxes in 094 (2, 3), 099 (1), 105, 106, 108, 111, 112 (1), and 113 name the simplifier, `simplify_text`, `simplify-long-doc`, or "the SLM route". The simplifier (041 A-1) is parked in W4, and the SLM track is parked.
Why it matters: a live issue's proof cannot need a parked agent.
Fix: use the echo agent (`packages/workloads/echo-*`) and a fake evaluator as the reference agents; rename the example workflows to `echo-long-doc` and `compare-routes`; 106 box 1 compares two routes from the route map (`docs/specs/models.md`), not "SLM vs big model". The epic's example files stay in the epic.
Status: open

### W89-07 [MEDIUM] [rule 12] docs/planning/issues/094-P-1-platform-mcp-server.md
Problem: `agent_run` "goes out through the chassis's outbound agent call", but no code has one and the only issue that builds the agent port is 099 O-1, a higher number. 094 depends on 051 H-13 and 093 R-14 only.
Why it matters: box 3 to 6 cannot pass without it, and the dependency points the wrong way.
Fix: 094 builds the outbound agent call (a chassis feature, with a fake and a contract case) and 099's agent port reuses it; resize M. The caller-identity "token exchange" bullet needs an ADR-008 note: ADR-008 says the token is never forwarded to the workload; this is chassis to agent.
Status: open

### W89-08 [MEDIUM] [rule 5] docs/planning/issues/094-P-1-platform-mcp-server.md
Problem: Box 3 wants partial output before the end, with `delta` as MCP progress notifications. ADR-003 item 6 makes MCP complete only, generated from the OpenAPI spec (`test_mcp_interface.py::test_stream_true_in_the_arguments_is_read_as_false`); the demo `pocs/poc-03-one-interface-every-client/demo/2026-10-01-demo-interfaces.md` says progress notifications are not used.
Why it matters: the streaming half is unreachable without a hand-written tool, which ADR-003 refuses.
Fix: add "Decided in ADR-003 item 6" and cut the streaming half of box 3 (and the "Suggested: streaming" sentence) until FastMCP can stream an OpenAPI tool.
Status: open

### W89-09 [MEDIUM] [rule 5] docs/planning/issues/095-P-4-mcp-scopes-audit.md
Problem: 095 names scopes `mcp:read` and `mcp:write`; ADR-008 item 2 defines `agent:run`, `agent:feedback`, and `agent:read`, and puts `/v1/mcp` under `agent:run`. Per-key tool allow-lists at the gateway (ADR-008, `deploy/kind/poc05/platform/seed.sh`) are the hard limit; a per-caller tool filter is a second layer. "A2A `input-required` maps to MCP elicitation" needs a contract change (it is `a2a.unsupported_state` today).
Why it matters: an engineer builds a scope model the ADR did not choose.
Fix: amend ADR-008 or rewrite the scope names; say which layer filters tools; drop the `input-required` suggestion until W89-05's interrupt ADR exists. Size M stays.
Status: open

### W89-10 [MEDIUM] [rule 11] packages/chassis/src/chassis/ports/tool.py
Problem: `ToolDefinition.read_only` defaults to `True`, so an untagged tool lists as read-only; the gateway adapter treats a missing hint as a write tool. 095's box 6 ("CI fails when a new tool has no tag") cannot work while the default hides the missing tag. Only `readOnlyHint` is set; there is no `destructiveHint`.
Why it matters: the box would pass with a wrong tag.
Fix: proposal for `proposals.md`: make the tag required (no default) or add a lint test; box 6 stays open.
Status: proposal

### W89-11 [MEDIUM] [rule 5] docs/planning/issues/113-O-9-orchestrator-memory.md
Problem: 113 stores "feedback from users and evaluators per run" behind `feedback_add` and sends it to the golden set through 068 D-3; 114 wraps `feedback_add`. ADR-009 sends feedback through `POST /v1/feedback` to Langfuse or LangSmith scores, with the golden set as a consumer of `agents.feedback.received.v1` (064 D-1). Evaluator results are spans and counters (ADR-010 item 5), not stored feedback.
Why it matters: two feedback stores.
Fix: remove feedback from the memory store; `feedback_add` wraps `/v1/feedback`; the golden set path follows ADR-009 item 6.
Status: open

### W89-12 [MEDIUM] [rule 5] docs/planning/issues/105-O-4-fan-out-fan-in.md
Problem: 105 and 106 score with an `evaluator-facts` agent. ADR-010 G7 says "not now"; the product plan runs a judge behind `EvaluatorPort` (`JudgeEvaluator`, rubric `judge-facts-v1`, `packages/chassis/src/chassis/adapters/evaluators.py`). The gate threshold defaults to 0.7, not 0.95.
Why it matters: the issues build an agent the plan chose not to.
Fix: replace "`evaluator-facts` agent" with "the `EvaluatorPort` judge"; the 0.95 stays a workflow field.
Status: open

### W89-13 [MEDIUM] [rule 5] docs/planning/issues/108-O-12-routing-planner-modes.md
Problem: The planner returns "JSON with guided decoding" on `big-planner`. `ModelPort` has no structured-output field; the proxy ignores such keys and ADR-003 item 2 refuses them; guided decoding comes from the parked vLLM work. `big-planner` is not in the route map (`docs/specs/models.md`).
Why it matters: box 2 is unbuildable as worded.
Fix: the planner validates the model's JSON against `workflow.schema.json` and retries once; the route is `planner` in the route map. 108 does not depend on a parked issue; it only blocks the parked 127 L-3. Size L holds.
Status: open

### W89-14 [MEDIUM] [rule 4] docs/planning/issues/110-C-6-human-oversight.md
Problem: 110 enforces `governance.human_oversight`, built nowhere (`GovernanceSpec` has two fields, `extra="forbid"`); 077 C-1 owns it (`contract-v6.md` line 426) and is not a dependency. It also needs a reviewer role: ADR-009 lists it as an open question. 109 needs a "write scope" ADR-008 does not define.
Why it matters: both issues rest on an undecided role.
Fix: add `C-1` (077) to 110's `ROWS`; put the reviewer role and write scope into DEC-1 and the owner list.
Status: open

### W89-15 [MEDIUM] [rule 9] docs/planning/issues/113-O-9-orchestrator-memory.md
Problem: 113 holds two scopes with no code: run state (Temporal query, status API, the Phase 7 end-to-end test) and long-term memory (a new `MemoryPort` with fake and contract suite, a pgvector adapter, settings, gotchas, vector search). pgvector appears in no code or deploy file.
Why it matters: the memory half alone is over the L bound.
Fix: split `113a` run state and the end-to-end test (M), `113b` long-term memory (L). 125 L-1 depends on 113b; 114 on both.
Status: open

### W89-16 [LOW] [rule 1] docs/planning/issues/101-O-13-version-pinning.md
Problem: Four boxes have a near analogue but no proving test: 094 box 5 (no MCP case in `test_idempotency_serve.py`, `INTERFACES` lists native, openai, anthropic); 101 boxes 1 and 3 (tests show only that the next request sees the new config); 107 boxes 2 and 4 (per-request).
Why it matters: the next engineer may tick them from the status line.
Fix: leave open. The proving tests are `test_an_mcp_call_replays_on_the_same_idempotency_key`, `test_a_request_keeps_the_config_it_opened_with_across_a_reload`, and for 107 a per-run test that needs step accounting.
Status: open

### W89-17 [MEDIUM] [rule 4] docs/planning/issues/098-O-10-orchestrator-schemas.md
Problem: 098 assumes `apiVersion`, `kind`, `metadata.name`, and a `schemas/` prefix in the store. The loader reads one `ChassisConfig` (`profile`, `agent`, `spec`, `version`) by agent name; `schemas/` lives in `packages/chassis/schemas/` in git; reload is limited to `RELOADABLE` paths.
Why it matters: box 5 needs a `kind` dispatch that 010 H-12 never built.
Fix: add a box to 098 for the dispatch and the reloadable set (W2a owns 010's verdict); reword to the real field names or keep `kind` as new. Size M holds.
Status: open

### W89-18 [MEDIUM] [rule 4] docs/planning/tools/backlog.py (O-1 row)
Problem: 099 depends on 008 H-14, which W2a splits (008a done, 008b `later`: class and kind). `agentctl new orchestrator --kind orchestrator` needs the kind, which 059 H-15 owns (`KINDS` has no `orchestrator`); 059 is not a dependency.
Why it matters: the scaffold box cannot pass.
Fix: depend on 008b and 059 H-15 (both lower numbers); if 059 splits, use the part that adds kinds.
Status: open

### W89-19 [MEDIUM] [rule 5] docs/planning/issues/112-O-16-event-triggers.md
Problem: 112 depends on 021 H-20 (`later`, 0/8, `spec.events.consume` refused). Box 5 wants a "correlation ID" and `run_id` as the partition key; `CloudEvent` is `extra="forbid"` (PoC-8 Q3 is open), and `partitionkey` is the hashed run key today. The AsyncAPI box needs 052 H-21, not a dependency.
Why it matters: a new extension breaks older consumers until Q3 is decided.
Fix: use `subject` for the request id; state the `partitionkey` rule; add `H-21` or drop the AsyncAPI box; Q3 goes to the owner list. The dep on 021 stays.
Status: open

### W89-20 [MEDIUM] [rule 10] docs/planning/issues/107-O-6-run-budgets.md
Problem: The per-request budget exists (model proxy caps `max_tokens`, `budget.timeout_ms`, 429 `budget_exhausted`). The envelope `Budget` has only `max_tokens` and `timeout_ms`; there is no cost cap, step count, `timeout_s`, or `stopped_budget` status. Judge cost is not charged to the run.
Why it matters: the issue reads as if it builds budgets from nothing.
Fix: add a "Status after PoC-4" bullet; box 3 and 7 need a contract change (`chassis-architect`); resize M.
Status: open

### W89-21 [MEDIUM] [rule 5] docs/planning/issues/111-O-14-workflow-validation-rollback.md
Problem: `ConfigPort` has `load` and `subscribe` only. Activation, rollback, and `config.config.changed.v1` need a writer, an active pointer, and an emitter; none is in a contract. "Dry run with write tools off" has no run-level switch: writes are gated per call by idempotency key and the gateway's per-key allow-list (ADR-001 item 6). 114's `config_set` and `config_rollback` need the same writer.
Why it matters: the port needs a fake, a minio and s3 adapter, and a contract suite first.
Fix: resize L; add the port change to `## What`; a dry run uses a key whose allow-list holds no write tools.
Status: open

### W89-22 [MEDIUM] [rule 10] docs/planning/issues/104-O-3-agent-pools-keda.md
Problem: Text drifts from what is built: it asks for a preStop order the chart does not use (the chassis is the native sidecar, `deploy/helm/agent-lib/templates/_deployment.tpl`), scales a `remote` pod on its own (the product plan: one replica, no HPA), and adds a Temporal credential. Box 4 and 5 have request-level evidence only (`pocs/poc-04-stateless-scalable/tests/test_killed_replica.py::test_a_retry_after_a_dead_owner_runs_once_after_the_lease`, `test_graceful_shutdown.py::test_the_chassis_drains_in_flight_runs_and_ready_goes_false_first`). Scale from 0 and lag scaling are unproven; the HPA drill went 2 to 4.
Why it matters: a reader would think boxes 4 and 5 are done.
Fix: add the "Status after PoC-4 and PoC-9" bullet from the folded list; mark the `remote` and KEDA parts `later`; 025 H-10 can go from deps because the chart values live in `agent-lib` (W2b confirms). Size L.
Status: open

### W89-23 [LOW] [rule 5] docs/planning/issues/097-P-3-mcp-resources-prompts.md
Problem: Resource 3 reads call logs "from Loki (014 H-7)"; Loki is in no code, deploy file, or ADR-007 (the stack is Collector, Jaeger, Langfuse).
Why it matters: the box names a backend nothing runs.
Fix: say "from the log backend chosen in 014 H-7"; the dataset resource waits on lakeFS (069 D-7).
Status: open

### W89-24 [LOW] [rule 5] docs/planning/issues/102-O-7-idempotent-steps.md
Problem: The step key resolves to the chassis rule that a run ending in `error` frees its key (contract section 6), so a retried errored step runs again, and same key with other input is 422. "The testing kit's fake write tool" is `fake_mcp_server.FakeMcpState.executions` or `chassis.fakes.tool.WriteModeTools`. The cache TTL is `spec.idempotency.ttl_s` (default 86400).
Why it matters: box 1 must name the fake it counts with.
Fix: add the three facts to `## What`; no new dependency.
Status: open

### W89-25 [LOW] [rule 6] docs/planning/issues/095-P-4-mcp-scopes-audit.md
Problem: The `suggested:` values in 094–115 (scope names, URL paths, event names, the UUIDv5 key, a $2 approval line, the tie rule) are all open; none was settled by a PoC or ADR.
Why it matters: none blocks W1 to W4.
Fix: list none for the DEC-1 pack; W89-02, W89-09, and W89-14 are the three that need an ADR or an owner answer first.
Status: deferred (all `later`)

### W89-26 [LOW] [rule 13] docs/planning/issues/096-P-2a-mcp-golden-set-metrics-tools.md
Problem: 096 box 5 compares `metrics_get` to "the savings dashboard"; W4's 006 G-6 verdict parks the savings half (tokens saved against an SLM baseline).
Why it matters: the box would wait on a parked panel.
Fix: reword to "the container dashboard's token and cost panels for the same agent and period" (046 A-5, phase E); keep the dependency.
Status: open
