# Wave 3: the offline boxes

Status: in progress

Date: 2026-11-16. Base: `main` `9fc0f69`, after PRs 229 to 234. `make progress` there: 255 of 344 boxes (74%), 14 of 44 issues done.

Approved by the user on 2026-11-16, with these choices: run batches 1 to 3; park the four boxes listed under "Parked"; park W3-13 until the user decides P-28; drop W3-14 (137 L31 is parked); write the ADR-005 comparison note (W3-15).

This is the second orchestrator's track. It runs beside the kind orchestrator (waves 1 and 2 on the `poc05` cluster) and never touches kind, Compose, `deploy/helm/**`, `deploy/kind/**`, or `HANDOVER.md`. The wave comes from `HANDOVER.md`, "Wave 3: offline boxes, by phase".

## What the issues say, box by box

"Offline as written" means the box can be ticked with tests that run with no network, no keys, no kind, and no Compose. Line numbers are in the issue file on `9fc0f69`.

| # | Issue, line | Offline as written? | Why |
| - | ----------- | ------------------- | --- |
| 3a | 012 H-3 L48 `latency_ms` | Yes | The field exists (`events.v0.json`), defaults to null. The workloads do not set it. No contract change. |
| 3b | 037 M-2 L42 changed prompt reaches the gate | Yes | The fake model server logs each request body. A test checks a changed `SYSTEM_PROMPT` reaches it. A real gate run is not needed for this box. |
| 3c | 042 A-2 L45 judge route checked against the key | Half | The key's routes are known only to LiteLLM. Offline: a router 403 on the judge route is refused, not shipped unscored. Today it fails open as `evaluator_unavailable` (a real bug). The live router check is wave 4a. |
| 3d-1 | 137 CH-17 L31 30 `fail` cases with reasons | No | Writing the cases is offline. But `evals/gate/baseline.json` must be re-recorded from a real 7-engine run (`eval-kit` rule: `--record` only), or CI fails on the case count. |
| 3d-4 | 137 CH-17 L37 no profile sets `sync` | Yes | `PROFILE_DEFAULTS` and `with_cloud_defaults` set no `sync`. The trap: `HarnessSpec.evaluator_mode` defaults to `sync`. |
| 3e | 058 CH-8 L51 LangGraph default passes the evals | Yes | No test runs `agentctl new` with no `--framework` and then `make eval`. One slow test closes it. It is 058's last box, so 058 retires. |
| 3f | 129 CH-10 L30 exporter behind a profile switch | Yes | An overlay Collector config in `deploy/compose/poc07`, turned on by a `stack.sh` flag. Static tests and `docker compose config` (no daemon) show the default stack has no LangSmith exporter and needs no key. |
| 3g-1 | 130 CH-11 L30 picked by `spec.adapters`, key in chassis | No | The open half is a stack test: the workload and the logs hold no key. Wave 4g. |
| 3g-2 | 130 CH-11 L32 score goes where DEC-1 says | Yes | Both fakes on, the score lands in one only. Trap: `PROFILE_DEFAULTS["cloud"].feedback` is `langfuse`; DEC-1 decision 5 says LangSmith in `cloud`. |
| 3h | 006 G-6 L35 spend by agent, user, route | No | Agent and route are done. A `user` label is unbounded and refused by design (004 G-2). User spend lives in LiteLLM's Postgres, in another stack. Grafana would need a DB password, a new secret holder. |
| 3i | 046 A-5 L36 north star row | Yes | No container dashboard exists. The generator writes per-agent boards only. Rows with no Prometheus source become text panels. |
| 3j | 054 H-16, six boxes | 3 yes, 3 need a decision | L53, L58, L62 are tests and wiring. L52 (`mode: write`) hits the open decision P-28. L57 (malformed output retried) and L59 (`max_tool_calls`) need a contract or schema call. |
| 3k | 117 X-3 L41, 44, 45, 46, 48, 49 | 5 yes, L41 no | L41 says chart defaults: that is `deploy/helm`, the kind orchestrator's. `chassis_build_info` already exists; the issue text is stale. |
| 3l | 120 X-7 L34, 36, 37 | L34, L37 yes; L36 half | L37 (`task` label) adds a label to a family: a contract change, contract v9 J.30. L36 needs "the first week's number", which no production week has given. |

## Pieces, files, and reviewers

Every PR gets `reviewer`. Sizes are `suggested:` (S at most 1 day, M 2 to 3 days, L 4 to 5 days). Each piece also edits its issue file and regenerates `docs/planning/issues/000-plan.md`.

| Piece | Boxes | Files (main ones) | Size `suggested:` | Extra reviewers |
| ----- | ----- | ----------------- | ----------------- | --------------- |
| W3-1 058 eval box | 058 L51 | `packages/agentctl/tests/test_agentctl_eval.py`; retire 058 in `backlog.py` | S | `eval-expert` |
| W3-2 037 prompt test | 037 L42 | new test in `pocs/poc-08-cross-cutting-build/tests/` | S | `eval-expert` |
| W3-3 137 no-sync test | 137 L37 | new `packages/chassis/tests/test_evaluator_mode_profiles.py` | S | `eval-expert`, `chassis-architect` |
| W3-4 012 latency | 012 L48 | `packages/workloads/echo-python/handle.py`, the workload `mapping.py` files that lack it, their tests | S | — |
| W3-5 046 container board | 046 L36 | `chassis/dashboards/__init__.py`, `metrics.py` `DECLARED`, `deploy/dashboards/container.json` (generated), `test_dashboards.py`, `docs/specs/contract.md` families table | M | `observability-expert`, `chassis-architect` |
| W3-6 129 profile switch | 129 L30 | new `deploy/compose/poc07/otel-collector.langsmith.yaml`, `stack.sh`, compose override, `test_poc07_stack_static.py` | M | `platform-security`, `observability-expert` |
| W3-7 130 score routing | 130 L32 | `chassis/profiles.py` (`cloud` feedback default), `test_langsmith_feedback.py` | S | `chassis-architect`, `observability-expert` |
| W3-8 042 judge refusal | 042 L45 | `adapters/evaluators.py`, `server/evaluator_gate.py`, `test_evaluators.py`, `test_evaluator_gate.py` | M | `chassis-architect`, `eval-expert` |
| W3-9 117 alert rules | 117 L44, 45, 48, 49 | `deploy/prometheus/rules/chassis.yaml`, `test_alert_rules.py`, `server/observability.py` (zero-init `rate_limit_unavailable`), `metrics.py` `DECLARED`, runbooks guide | M | `observability-expert` |
| W3-10 054 tools part 1 | 054 L53, 58, 62 | new tool tests, `chassis_contracts/redteam/__init__.py`, `test_redteam_controls.py`, a new `/mcp` lane binding test | M | `chassis-architect`, `platform-security` |
| W3-11 cost and SLO panels | 120 L34; 117 L46; 120 L36 panel only | `chassis/dashboards/__init__.py`, `deploy/dashboards/*.json`, `test_dashboards.py` | M | `observability-expert` |
| W3-12 120 task label | 120 L37 | `server/correlation.py`, `server/observability.py`, `metrics.py`, `server/model_admission.py`, `server/evaluator_gate.py`, contract v9 J.30, `test_metrics.py` | M | `chassis-architect`, `observability-expert` |
| W3-13 054 tools part 2 | 054 L52, 57, 59 | `server/config.py`, `chassis-config.v0.json`, `server/correlation.py`, `adapters/mcp/server.py`, `server/evaluator_gate.py`, the fakes | M | `chassis-architect`, `platform-security` |
| W3-14 137 fail cases, draft | 137 L31 (prep) | `golden/cases-gate.yaml`, `golden/__init__.py` (a `fail` case needs a `note`) | M | `eval-expert` |

## Files two pieces share, and the order that keeps them apart

| Shared file | Pieces, in order |
| ----------- | ---------------- |
| `chassis/metrics.py` `DECLARED`, contract families table | W3-5 → W3-9 → W3-12 |
| `chassis/dashboards/__init__.py`, `deploy/dashboards/*` | W3-5 → W3-11 |
| `server/observability.py` | W3-9 → W3-12 |
| `server/evaluator_gate.py` | W3-8 → W3-12 → W3-13 |
| `server/correlation.py` | W3-12 → W3-13 |
| `docs/planning/issues/000-plan.md` | every piece; on a conflict take either side and rerun `make planning-sync` |

## Batches

At most 5 agents at once, targeted tests only. I run `make check` per PR under the gate lock.

1. **Batch 1, no shared files:** W3-1, W3-2, W3-3, W3-4, W3-5.
2. **Batch 2, after W3-5's PR is open and reviewed:** W3-6, W3-7, W3-8, W3-9, W3-10. W3-11 starts when W3-5 merges.
3. **Batch 3:** W3-12, after W3-8 and W3-9 merge.
4. **Parked:** W3-13 waits for the user's P-28 decision. W3-14 is dropped while 137 L31 is parked.
5. **W3-15, beside batch 1:** the ADR-005 comparison note (see the last section).

Expected: 18 boxes ticked from W3-1 to W3-12, and 058 CH-8 retired. W3-13 adds 3 more after P-28.

## Parked, with the reason

- **006 G-6 L35, the user filter.** Not offline. Proposed: park to wave 4c. The box needs a decision: reword to "user spend from LiteLLM's spend logs", or give Grafana a Postgres datasource (`platform-security`, a new secret holder).
- **117 X-3 L41, SLO rules from chart defaults.** Needs `deploy/helm`. Park until the kind orchestrator's wave 2 is done, or reword to a `spec.slo` block plus an objective gauge (a contract change).
- **137 CH-17 L31.** Merge waits on a real gate run that re-records `baseline.json` (wave 5a). Also, 042 A-2 L41 says "the 30-case gate set", which the new cases make false. That line needs rewording in the same PR.
- **130 CH-11 L30.** The stack test is wave 4g.
- **120 X-7 L36.** The panel ships in W3-11. The box stays open until the first production week gives a number.

## Notes for the reviewers

- **Key conflict:** 129 L33 wants the LangSmith key in the Collector only; 130 L30 keeps a LangSmith key in the chassis for feedback. That needs two variables. `chassis-architect` and `platform-security` should agree on the names in W3-6 and W3-7.
- **042 A-2 L45:** tick on the offline refusal test only if `chassis-architect` reads the box as the chassis side (the router owns the key's routes, ADR-013). Either way the live check is added to wave 4a.
- **Contract v9:** W3-12 takes item J.30. If W3-8 adds an error code, it takes the next item.

## W3-15: the ADR-005 comparison note

Approved by the user on 2026-11-16. A comparison note for the ADR-005 amendment: Kyverno `verifyImages` only, against sigstore `policy-controller`, checked against `platform-security`'s four asks in `HANDOVER.md`. It is a note for the user's decision in `docs/reviews/`, not a decision. Writer: `platform-security`.
