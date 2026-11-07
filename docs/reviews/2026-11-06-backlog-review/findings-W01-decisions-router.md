# Findings W01: W0 Decisions, W1 Router and baseline

Session: W01. Issues: 001–006. Waves: W0, W1.
Rubric: [rubric.md](rubric.md).

## Summary

The PoC track built the router and the chassis's own token and cost counting, but only against the fake model: LiteLLM in Compose and on kind, scoped virtual keys (PoC-5, PoC-8), per-route token and cost counters, and one Grafana dashboard per agent (PoC-8). No hosted provider call has a record, H31 (a spoofed agent tag) was never run, and the baseline has no real traffic to count. 002, 003, 004, and the live half of 006 feed phases B and E; 005 G-3 and the savings half of 006 G-6 serve only SLM savings and are parked. The top risk: the product's P1 gap, the route map per task (`ModelSpec.routes`, `judge_route`), has no issue, and 003 G-1b cannot hold it without breaking the order rule.

## Issues

| Issue | Boxes | Phase | Verdicts | Evidence or reason |
| ----- | ----- | ----- | -------- | ------------------ |
| 001 DEC-1 | 2/10 | E | tick, split, parked, drift, moot | Box 8 split into 8a (Dapr, ticked) and 8b (other reuse picks). Box 9 ticked: every ADR-001 leftover was decided by build (ADR-005 items 4 and 6, ADR-006, ADR-008 item 4). Parked: box 5 (banned words, only 027 S-1), box 10 (the router's home, only for the baseline), the GPU and training-data owners in box 7. Box 6 becomes the product's north star, which phase E's done-when needs. Open: broker, first cloud, public A2A, owners, reuse picks. Size S stays |
| 002 G-1 | 2/7 | B | tick, conflict, tick-blocked, moot, cite | Box numbers count all 7. Box 1 ticked already (wrong form). Box 5 (no key refused, no master key in a service) conflict: kind and the PoC-8 stack meet it, the `local` Compose variant gives the chassis the master key. Box 4 (no provider key) tick-blocked: config and repo are covered by offline tests, the logs by none. Box 6 moot: there are no "existing services"; rewrite and tick. Boxes 2 (formats, OpenAI half after the rewrite) and 3 (providers) close with the Mac run. Box 7 open: no router guide. Size M stays (5 open boxes, two need a hosted run) |
| 003 G-1b | 0/6 | B | parked, moot, resize M, tick-blocked | `simplifier-slm` and `teacher-open` are SLM routes: parked. The rename decision is moot: PoC-6c keeps `local-small`. Box 5 partly met: a route outside the key gets 403 `model_route_denied` (`pocs/poc-05-sandboxed/tests/test_poc05_hostile_offline.py::test_h29_a_route_outside_the_key_is_refused_and_an_in_scope_call_is_200`, offline, ran); an unknown route name has no test. 5 open boxes, two need a running LiteLLM: M. Not the home of the route map per task (finding W01-02) |
| 004 G-2 | 1/7 | E | tick, tick-blocked, parked, cite | Box 3 ticked (PoC-1 demo, offline test). Box 7 rewrite: no `user` label exists or should; then tick-blocked on a dated PoC-8 stack run. Box 6 (H31) never run. Box 8 (collection start day) serves only the baseline: parked. Open for phase E: one usage record per call, a totals query, the `unknown` user, provider-usage match (Mac run). Size M stays (6 live open boxes) |
| 005 G-3 | 0/6 | parked | parked | Its only purpose is the big-model savings baseline (−30% spend) for the SLM track. The product's north star is good tasks or agents in production, not token savings. No real traffic exists. Blocks only 031 G-5, which is also SLM |
| 006 G-6 | 0/4 | E | parked, resize S, tick-blocked, cite | Savings half parked: box 4 (big-model and SLM routes apart) and box 5 (baseline and target lines from 005 G-3). Live half: token and cost panels exist in `deploy/dashboards/*.json`, checked offline (`packages/chassis/tests/test_dashboards.py::test_every_panel_names_a_metric_the_chassis_exports`, ran) and on the stack by a `network` test with no dated record. Open: within one minute, filter by agent and route, the stack loads it with no steps. 4 live boxes, 2 close with one dated stack run: S |

## Ticks

- `001 DEC-1`, box 8a "The Dapr decision from PoC-4 is recorded" (after the split in W01-07): `· Delivered in PoC-4: pocs/poc-04-stateless-scalable/notes/2026-10-01-dapr-vs-broker.md; ADR-004 Decision 1, accepted 2026-10-08 (a broker client in the chassis behind EventPort)`
- `001 DEC-1`, box "The choices ADR-001 leaves to planning are recorded": `· Delivered in PoC-2, PoC-5, PoC-6b: ValidatingAdmissionPolicy, no Kyverno (ADR-005 item 4; pocs/poc-05-sandboxed/demo/2026-10-09-demo-sandboxed.md section 5); gVisor (pocs/poc-05-sandboxed/notes/2026-10-09-gvisor-overhead.md); no managed runtime, kagent-adk in plain-A2A mode (pocs/poc-06b-bake-off-remote-lane/notes/2026-10-09-kagent-probe.md; ADR-006, AgentCore and Vertex out of scope on 2026-10-09); echo-typescript in the sidecar lane (pocs/poc-02-two-engines-one-contract/tests/test_lanes.py::test_typescript_echo_gives_the_same_stream_as_echo_python); agent-sandbox, one sandbox per call (ADR-005 item 6; pocs/poc-05-sandboxed/notes/2026-10-09-close-runs.md); guardrails per route in open-source LiteLLM, no Enterprise now (ADR-008 item 4 and S10)`
- `002 G-1`, box "The router starts with `docker compose up`" (already ticked; PR-2 fixes the form): `· Delivered in PoC-1: pocs/poc-01-walking-skeleton/tests/test_compose.py::test_chassis_waits_for_a_healthy_router; pocs/poc-01-walking-skeleton/demo/2026-09-29-demo-fake-variant.md`
- `002 G-1`, box 6 as rewritten in W01-05 "Every service calls the router with its own virtual key, minted at deploy": `· Delivered in PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py::test_litellm_refuses_the_workload_without_the_chassis_key, run in pocs/poc-05-sandboxed/demo/2026-10-09-demo-sandboxed.md; PoC-8: pocs/poc-08-cross-cutting-build/tests/test_poc08_litellm_static.py::test_the_master_key_reaches_litellm_only_and_the_virtual_key_the_chassis_only`
- `004 G-2`, box "A streamed call makes a usage record with real token counts": `· Delivered in PoC-1: pocs/poc-01-walking-skeleton/demo/2026-09-29-demo-fake-variant.md (streamed runs on both routes, 42 and 9 tokens in the router's token_log); packages/chassis/tests/test_metrics.py::test_model_tokens_and_cost_come_from_the_proxy_usage (fake model)`

The offline tests cited in this file were run on 2026-11-06:

```
$ UV_NO_SYNC=1 scripts/check_offline.sh <10 test ids from this file> -q -p no:cacheprovider
.............                                                            [100%]
13 passed in 3.41s
```

The ten ids: `test_metrics.py::test_model_tokens_and_cost_come_from_the_proxy_usage`, `test_model_proxy.py::test_stream_ends_with_usage_and_done`, `test_poc08_litellm_static.py::test_the_master_key_reaches_litellm_only_and_the_virtual_key_the_chassis_only` and `::test_no_key_literal_in_any_file`, `test_poc05_seed_static.py::test_only_the_platform_services_get_the_platform_secrets`, `test_poc05_hostile_offline.py::test_h29_a_route_outside_the_key_is_refused_and_an_in_scope_call_is_200`, `test_poc08_stack_static.py::test_grafana_provisions_every_file_of_deploy_dashboards`, `test_dashboards.py::test_every_panel_names_a_metric_the_chassis_exports`, `test_compose.py::test_no_value_looks_like_a_key`, `test_config_reload.py::test_a_change_takes_effect_on_the_next_request_with_no_restart`. The `network` and kind tests were not run here; their evidence is the dated demo named next to them.

## Status bullets folded

- `001 DEC-1`, "Status after PoC-4": tick (box 8a, the Dapr half). The broker product stays open under DEC-1; ADR-004 item 8 already holds the bar a NATS adapter must pass, so the measurement detail leaves the issue body for a link to the Dapr note.
- `002 G-1`, "Status after PoC-1": tick (box 1). "Only the OpenAI format; `/v1/messages`, Claude, and Gemini untested" goes to open boxes 2 and 3. The `fake` variant's missing master key is a fact for `deploy/compose/SECURITY.md`, not a box: out of scope.
- `002 G-1`, out-of-scope bullet "Debt from PoC-1" (deadline PoC-5): PoC-5 paid it on kind and PoC-8 on its stack, but the `local` Compose variant still carries it. Goes to `proposals.md` (W01-01).
- `003 G-1b`, "Status after PoC-1": the tool-call flag, route versions, and the unknown-route error stay open boxes. The rename question is moot: PoC-6c keeps `local-small` (`pocs/poc-06c-pretrained-slm/notes/backlog-changes.md`). Drop it.
- `003 G-1b`, "Status after PoC-6": out of scope, a fact. The hosted run on `big-default` is the Mac run in [011-what-is-left.md](../../planning/poc/011-what-is-left.md) section 1.
- `004 G-2`, "Status after PoC-1": tick (box 3, streamed counts). The spoofable tag goes to box 6. "Real cost unverified" goes to box 2.
- `004 G-2`, "Status after PoC-5": the uncharged early-closed stream is a fact for `## What` (`_Hold.release`; the abandoned-stream cases in `packages/chassis/tests/test_model_proxy_messages_budget.py`). H31 not run stays open box 6.
- Inputs never applied: the PoC-5 owner item "H31 waits on 004 G-2" (`pocs/poc-05-sandboxed/notes/backlog-changes.md`, and 011 section 3) goes to box 6. The PoC-6c line on 003 G-1b ("the name stays") is folded above. The PoC-7 draft and the PoC-9 W2–W5 report name none of 001–006.

## Findings

### W01-01 [HIGH] [rule 11] docs/planning/issues/002-G-1-litellm-router.md
Problem: Box 5 ("no service config holds the master key") is contradicted by the `local` Compose variant: `deploy/compose/docker-compose.local.yaml` gives the chassis `LITELLM_API_KEY`, which the PoC-1 key-debt note sets equal to the master key, because that variant has no database to mint a virtual key; PoC-6c's Mac command added the same debt again.
Why it matters: the issue's deadline for this debt was PoC-5; kind (`test_poc05_kind_hardreq1.py`) and the PoC-8 stack (`deploy/compose/poc08-litellm/stack.sh`) meet the rule, so the one stack an engineer runs by hand is the one that breaks it.
Fix: keep box 5 open with verdict `conflict`; the proposal is to retire the master-key path of the `local` variant, or point it at the `poc08-litellm` stack that mints a scoped key.
Status: proposal

### W01-02 [HIGH] [rule 14] docs/planning/issues/003-G-1b-named-model-routes.md
Problem: The product plan's P1 gap "No route map per task" (phase B: `ModelSpec.routes`, `judge_route`, enforcement in `model_admission.py`, the schema and `agentctl` refusing a route the key lacks) has no issue, and 003 G-1b cannot hold it: 003 is router-side LiteLLM config, and the map needs the chassis config, the model port (012 H-3), and `agentctl` (059 H-15), all higher numbers (rule 12).
Why it matters: phase B's done-when ("a config map swaps a model per task with no agent change") has no issue to close it, and C needs B's route map. Today `ModelSpec` has only `route` and `fallback_route` (`packages/chassis/src/chassis/server/config.py`).
Fix: a new issue, appended (suggested: 128, `area:harness`, phase B, size M), depending on 003 G-1b, 012 H-3, and 059 H-15, with boxes from `docs/specs/models.md` "The route map per task"; `judge_route` either goes there or to the evaluator issue the W4 and W6 sessions name. 003 keeps the router-side routes and the per-key route list.
Status: open

### W01-03 [MEDIUM] [rule 13] docs/planning/issues/005-G-3-baseline-report.md
Problem: G-3 exists to measure the big-model token spend the SLM track would save (−30%), and the product plan parks the SLM track; no real traffic exists to count.
Why it matters: 001 DEC-1 box 6 ("005 G-3 makes them final") and 006 G-6 box 5 (the baseline line) point at it, and 031 G-5 depends on it.
Fix: `parked` in `ROWS`; 031 G-5 (also SLM) may keep the dependency; rewrite DEC-1 box 6 (W01-06) and park 006 box 5 (W01-04) so no live box waits on it.
Status: open

### W01-04 [MEDIUM] [rule 13] docs/planning/issues/006-G-6-savings-dashboard.md
Problem: Half of G-6 serves only SLM savings (big-model and SLM routes apart; baseline and −30% target lines from 005 G-3), and the other half, live token spend and cost per agent and route, is phase E's container dashboard, mostly built by PoC-8 (`deploy/dashboards/`, seven panels including tokens by direction and cost by route).
Why it matters: parking all of G-6 would also park 096 P-2a's data source and phase E's token panels; keeping it whole keeps two boxes no phase needs.
Fix: move boxes 4 and 5 to `## Out of scope` as "parked with the SLM track"; retitle to "Token and cost dashboard with live spend" (suggested); phase E; resize S, from the 4 live boxes (two close with one dated PoC-8 stack run).
Status: open

### W01-05 [MEDIUM] [rule 8] docs/planning/issues/002-G-1-litellm-router.md
Problem: Box 6 ("every existing service that calls a big model is listed and points at the router") has no subject: the repo has no existing model-calling services, and ADR-001 item 6 made "each service has its own virtual key, minted at deploy" true instead.
Why it matters: the box can never be ticked as written, so the issue can never retire.
Fix: rewrite to "Every service calls the router with its own virtual key, minted at deploy" and tick it with the PoC-5 kind test and the PoC-8 static test (Ticks).
Status: open

### W01-06 [MEDIUM] [rule 14] docs/planning/issues/001-DEC-1-resolve-open-decisions.md
Problem: The product plan's two open user decisions, the north star (good tasks per week, or agents in production) and the primary trace view per environment (LangSmith or Langfuse, and where the score goes), are in no issue; DEC-1 box 6 still asks for the epic's SLM savings targets.
Why it matters: phase E's done-when shows "the north star" on one dashboard, and phase D needs the trace-view choice to place scores.
Fix: rewrite box 6 to "The north star is chosen, with draft targets for its inputs (product plan, open item 1)"; add a box for the trace view (open item 2, ADR-007 open question 4, ADR-009 open question 5); phase E.
Status: open

### W01-07 [MEDIUM] [rule 1] docs/planning/issues/001-DEC-1-resolve-open-decisions.md
Problem: Box 8 joins a decision that is made (Dapr, ADR-004) with picks that are not (lakeFS or DVC, the registry base), so the Dapr half cannot be ticked.
Why it matters: the issue's "Status after PoC-4" says "the box stays unticked until the other reuse picks are accepted", which hides a finished decision.
Fix: split box 8 into 8a (the Dapr decision, ticked) and 8b (each other reuse pick accepted or replaced: suggested, accept Langfuse per ADR-010 item 2, defer lakeFS or DVC to 069 D-7 and the registry base to 076 R-1).
Status: open

### W01-08 [MEDIUM] [rule 5] docs/planning/issues/001-DEC-1-resolve-open-decisions.md
Problem: The `## What` bullet "Choices ADR-001 leaves to planning" lists as open what ADR-005 items 4 and 6, ADR-006, and ADR-008 item 4 decided.
Why it matters: a reader thinks Kyverno, Kata, E2B, and LiteLLM Enterprise are still on the table.
Fix: add "Decided in ADR-005 (items 4, 6), ADR-006, and ADR-008 (item 4, S10)" to the bullet and tick the box (Ticks).
Status: open

### W01-09 [MEDIUM] [rule 5] docs/planning/issues/001-DEC-1-resolve-open-decisions.md
Problem: The first-cloud bullet and box 3 say the cloud "picks the `remote`-lane auth adapter (SigV4, OAuth, Google auth)"; ADR-005 item 1 picks one bearer token per remote, and ADR-006 put AgentCore and Vertex out of scope on 2026-10-09.
Why it matters: the cloud choice looks like it changes the remote lane's auth, which it no longer does.
Fix: rewrite box 3 to "The first cloud is written into 038 X-1a, with the cluster needs PoC-5 proved (gVisor RuntimeClass, a CNI that enforces NetworkPolicy, ValidatingAdmissionPolicy, Kubernetes 1.33 or later)", plus "Decided in ADR-005" for the auth.
Status: open

### W01-10 [MEDIUM] [rule 13] docs/planning/issues/001-DEC-1-resolve-open-decisions.md
Problem: Box 5 (a banned-word list linked from 027 S-1) serves only the simplifier SLM's rewrite rules, and box 10 (the router's first home "so the baseline counts real traffic") serves only 005 G-3.
Why it matters: two open boxes no product phase needs keep DEC-1 from closing.
Fix: move both to `## Out of scope` as "parked with the SLM track"; for the product, the router runs in every deploy stack (`deploy/compose/poc08-litellm/`, `deploy/kind/poc05/platform/`); drop 002 G-1's `suggested: the existing infrastructure` host bullet with it.
Status: open

### W01-11 [MEDIUM] [rule 13] docs/planning/issues/001-DEC-1-resolve-open-decisions.md
Problem: Box 7 asks owners for five dependencies; two (GPU access, real outputs for training data) serve only the SLM track.
Why it matters: the live owners (cloud account, golden-set reviewer, legal and compliance) are needed by phases E and later and are named nowhere.
Fix: keep box 7 for the three live owners; move the GPU and training-data owners to `## Out of scope`, parked.
Status: open

### W01-12 [MEDIUM] [rule 1] docs/planning/issues/002-G-1-litellm-router.md
Problem: Box 4 ("no provider key in the router config, the repo, or the logs") has offline tests for config and repo (`test_poc08_litellm_static.py::test_no_key_literal_in_any_file`, `test_compose.py::test_no_value_looks_like_a_key`, both ran) and none for the router's logs.
Why it matters: a key in a log line is the leak the box exists to stop.
Fix: `tick-blocked`; the proving test is a `network` test on the PoC-8 stack that makes a call and greps the LiteLLM container log for the key (suggested: `test_no_provider_key_in_the_router_logs`).
Status: open

### W01-13 [MEDIUM] [rule 8] docs/planning/issues/002-G-1-litellm-router.md
Problem: Box 2 wants an Anthropic-format call to reach a big model through LiteLLM's `/v1/messages`, but in the product the chassis serves `/v1/messages` and maps it onto `ModelPort`, whose `LiteLLMModel` posts only to `/chat/completions` (ADR-003, contract v7).
Why it matters: the router's own Anthropic passthrough is on no request path, so the box tests a route nothing uses.
Fix: rewrite box 2 to "An OpenAI-format call through the router reaches a big model, streaming and complete; the Anthropic format reaches the router as OpenAI format through the chassis"; it closes with the Mac run (phase B), as does box 3 (one call per provider the product uses).
Status: open

### W01-14 [MEDIUM] [rule 13] docs/planning/issues/003-G-1b-named-model-routes.md
Problem: `simplifier-slm` and `teacher-open` are SLM-track routes, and box 1 names `simplifier-slm`; the routes in config are `big-default`, `local-small`, and `fake-chat` (`docs/specs/models.md` at `70244ad`).
Why it matters: box 1 cannot be met without building a parked route.
Fix: rewrite box 1 to "`big-default` and `local-small` answer through the router by name" (`pocs/poc-01-walking-skeleton/demo/2026-09-29-demo-fake-variant.md` shows both on the fake model; tick after one hosted run), drop the two SLM routes from `## What` as parked, and drop the rename question.
Status: open

### W01-15 [MEDIUM] [rule 3] docs/planning/issues/003-G-1b-named-model-routes.md
Problem: Five boxes stay open after the review (model swap behind a route, tool-call flag, route versions, unknown-route error, the route table), and two need a running LiteLLM.
Why it matters: S (one day) undercounts phase B's router work.
Fix: resize to M in `ROWS`. The swap test differs from `pocs/poc-06c-pretrained-slm/tests/test_poc06c_route_switch.py::test_only_the_route_changes_and_every_task_passes_both_times`, which changes the chassis's route, not the model behind it.
Status: open

### W01-16 [MEDIUM] [rule 1] docs/planning/issues/004-G-2-token-cost-counting.md
Problem: Box 7 wants Prometheus metrics labeled by agent, user, and route; `chassis/metrics.py` labels by `agent` and `route` only (`chassis_model_tokens_total`, `chassis_model_cost_microusd_total`), and a `user` label is unbounded.
Why it matters: the box asks for a label the platform should not add, and its scrape evidence (`pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_metrics.py::test_prometheus_scrapes_every_chassis_at_metrics`, `network`) has no dated record.
Fix: rewrite to "labeled by agent and route; `user` is in the usage record only"; `tick-blocked` until a dated `pocs/poc-08-cross-cutting-build/notes/<date>-stack-metrics.md` records the run.
Status: open

### W01-17 [MEDIUM] [rule 10] docs/planning/issues/004-G-2-token-cost-counting.md
Problem: Box 6 (a spoofed agent tag still counts under the key's agent) is H31, which PoC-5 never ran (`pocs/poc-05-sandboxed/notes/2026-10-02-blind-spots.md`); the tag now sits in the key's `metadata.tags` (`deploy/kind/poc05/platform/seed.sh`).
Why it matters: per-agent cost on the phase E dashboard is only as true as this check.
Fix: keep open; name the proving test, a kind test `test_spoofed_agent_tag_counts_under_key_agent`; take H31 off the PoC-5 owner list in 011 section 3 with a link here.
Status: open

### W01-18 [MEDIUM] [rule 13] docs/planning/issues/004-G-2-token-cost-counting.md
Problem: Box 8 (the day collection starts, for the baseline window) and the `## What` bullet on SLM route prices and GPU cost (031 G-5) serve only the parked baseline.
Why it matters: they keep a phase E issue tied to the SLM track.
Fix: move box 8 and the SLM-price bullet to `## Out of scope`, parked with 005 G-3.
Status: open

### W01-19 [MEDIUM] [rule 1] docs/planning/issues/006-G-6-savings-dashboard.md
Problem: Box 6 says `docker compose up` loads the dashboard; the PoC-8 stack (`deploy/compose/poc08/`) provisions it through its own script, with an offline check (`pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_static.py::test_grafana_provisions_every_file_of_deploy_dashboards`, ran) and a `network` check (`test_poc08_stack_metrics.py::test_grafana_loaded_every_dashboard_file`) with no dated record.
Why it matters: the box names a command the product does not use, and its live check has no evidence.
Fix: rewrite to "The PoC-8 stack loads every dashboard with no manual steps"; `tick-blocked` until one dated stack record, which also covers box 1 (`test_every_panel_query_has_data`).
Status: open

### W01-20 [LOW] [rule 6] docs/planning/issues/006-G-6-savings-dashboard.md
Problem: `suggested: Grafana provisioning files` is settled: PoC-8 stores the dashboards as code (`python -m chassis.dashboards` writes `deploy/dashboards/`; `packages/chassis/tests/test_dashboards.py::test_the_generated_files_are_up_to_date`).
Why it matters: a settled value still reads as open.
Fix: drop the marker and cite PoC-8.
Status: open

### W01-21 [LOW] [rule 6] docs/planning/issues/004-G-2-token-cost-counting.md
Problem: `suggested: user still comes from request metadata` is still open and ties to ADR-008's open question on `user.id` hashing (a P3 user decision in the product plan).
Why it matters: it does not block W1 to W4, so it does not belong in the DEC-1 pack.
Fix: keep the marker; add "see ADR-008 open questions" so the owner list carries it.
Status: open

### W01-22 [LOW] [rule 14] docs/planning/issues/006-G-6-savings-dashboard.md
Problem: The product plan's P2 gap "no container dashboard" (phase E) has no single home: 006 holds the token and cost panels, 117 X-3 the SLOs and alerts, 120 X-7 cost per pool.
Why it matters: phase E's done-when (north star, inputs, and counters on one dashboard) could fall between three issues.
Fix: C2 names one owner for the container dashboard in `phase-map.md` (suggested: 117 X-3, with 006 as its token and cost input); 006 links to it.
Status: open

### W01-23 [LOW] [rule 1] docs/planning/issues/002-G-1-litellm-router.md
Problem: The ticked box 1 uses "Delivered in PoC-1:" after a period, not the `· Delivered in PoC-1:` form.
Why it matters: the retire tooling in PR-2 parses the form.
Fix: PR-2 rewrites the line as in Ticks.
Status: open

### W01-24 [LOW] [rule 4] docs/planning/issues/002-G-1-litellm-router.md
Problem: 026 CH-4 depends on 002 G-1, but CH-4 needs from G-1 only the key refusal, which kind already proves (`test_poc05_kind_hardreq1.py::test_litellm_refuses_the_workload_without_the_chassis_key`, run in the 2026-10-09 PoC-5 demo).
Why it matters: phase A's CH-4 looks blocked by phase B's G-1.
Fix: keep the dependency (it points to a lower number) and note in CH-4 that the G-1 part it needs is met; the W2b session decides whether to drop it.
Status: open

## DEC-1 evidence

One subsection per open box of `001 DEC-1`, after the split of box 8.

### Box 1: the four decisions recorded, ADR-001 linked
- Question: are the broker, the first cloud, the public A2A endpoint, and the banned words and targets each recorded with an owner, a date, and a reason?
- Options: a short ADR each, or a dated comment on the issue.
- What the PoCs learned: ADR-001 is accepted and linked. The other three are below. ADRs name the epic owner as decider; the issue names no owner or date.
- `suggested:` close this box when boxes 2, 3, 4, and the rewritten 6 close; it adds no work of its own.
- Phase: E (through the north star). The rest is `later`.

### Box 2: the broker choice in 019 H-17, 020 X-8, 060 H-23
- Question: NATS JetStream (the epic's pick) or the company's Kafka?
- Options: Kafka, already built and bound; NATS, which must first pass all 8 `EventPortContract` cases with no xfail (ADR-004 item 8).
- What the PoCs learned: the Kafka adapter passes the contract; Kafka on kind with SASL/SCRAM (`deploy/kind/poc05/platform/kafka.yaml`); NATS was never run (`pocs/poc-04-stateless-scalable/notes/2026-10-01-dapr-vs-broker.md`, ADR-004 item 6).
- `suggested:` Kafka, written into 019 H-17, 020 X-8, and 060 H-23; NATS stays a swap behind the port.
- Phase: `later`. No phase A to F done-when publishes events to a real broker.

### Box 3: the first cloud in 038 X-1a
- Question: AWS or GCP for staging and prod?
- Options: AWS, GCP. The remote-lane auth is no longer part of it (ADR-005 item 1, a bearer token per remote); managed runtimes are out of scope (ADR-006, 2026-10-09).
- What the PoCs learned: no AWS or GCP evidence. The cluster shape is proven on kind: gVisor RuntimeClass, a NetworkPolicy CNI, ValidatingAdmissionPolicy, native sidecars on 1.33 or later (`pocs/poc-05-sandboxed/notes/2026-10-09-gvisor-overhead.md`, `pocs/poc-05-sandboxed/demo/2026-10-09-demo-sandboxed.md`).
- `suggested:` the cloud the company's services already run in, if it gives those four.
- Phase: `later`. The product plan lists it as a P3 open decision; phases A to F run on kind and Compose.

### Box 4: the priority of the public A2A endpoint (061 H-5)
- Question: does a public A2A JSON-RPC endpoint and agent card ship in version 1?
- Options: P1 (ships), P2 (does not).
- What the PoCs learned: the public port serves `/v1/run`, the OpenAI, Anthropic, and MCP interfaces, `/manifest`, `/v1/feedback`, and `/metrics` (contract v7). A2A is served only to workloads (inbound in the `inprocess` lane, outbound in `remote` and plain-A2A; PoC-3, PoC-6, contract v5).
- `suggested:` P2 unless a named external consumer exists; 061 H-5 unchanged.
- Phase: `later` (product plan P3).

### Box 5: a first banned-word list, linked from 027 S-1
- Question: which words does the simplifier refuse?
- Options: draft from the eval cases, or wait for the SLM rewrite rules.
- What the PoCs learned: no list exists; per-case `banned:` words sit in `packages/eval-kit/src/eval_kit/golden/cases.yaml`; PoC-7 checked banned words in an eval (`pocs/poc-07-cross-cutting-decisions/notes/2026-10-28-eval-tool-trial.md`).
- `suggested:` none now.
- Phase: `parked`. Only 027 S-1, the SLM rewrite rules, needs it (W01-10).

### Box 6: success-metric targets, rewritten as the north star
- Question: the product plan's north star, "good tasks per week" or "agents in production on the chassis", and draft targets for its inputs.
- Options: the two in the product plan (open item 1). The epic's targets (−30% big-model spend, `facts_kept` ≥ 0.95, fallback < 10%, registry top-3 ≥ 90%, run recovery 100%) are SLM and later-wave targets.
- What the PoCs learned: PoC-6c measured a 0% fallback share (`pocs/poc-06c-pretrained-slm/notes/backlog-changes.md`); the gate baseline is in `evals/gate/baseline.json` (PoC-8). No real traffic exists.
- `suggested:` the user picks; the epic's savings targets stay drafts, parked with 005 G-3.
- Phase: E. Its done-when shows the north star on one dashboard (W01-06).

### New box: the primary trace view per environment
- Question: LangSmith or Langfuse as the primary trace view per environment, and where the score goes when both run.
- Options: LangSmith first (the product plan's LangChain-first decision), Langfuse first (the only `FeedbackPort` backend built), or both with one score owner.
- What the PoCs learned: Langfuse v4 runs in the PoC-7 stack (`deploy/compose/poc07/stack.sh`; ADR-007, ADR-009); LangSmith is in no code or config.
- `suggested:` none; the user's call (product plan open item 2; ADR-007 open question 4, ADR-009 open question 5).
- Phase: D.

### Box 7: owners for the epic's dependencies
- Question: who owns the cloud account, the golden-set reviewer role, and legal and compliance?
- Options: named people; GPU access and real training outputs are parked with the SLM track (W01-11).
- What the PoCs learned: none is named anywhere; the epic lists them in its dependencies section.
- `suggested:` the user fills the table.
- Phase: E (the golden-set reviewer feeds the judge agreement test of phase B and the weekly record of E); the cloud account and legal owner are `later`.

### Box 8b: the other reuse picks
- Question: accept or replace Langfuse as the golden-set store, lakeFS or DVC, and our own registry on Postgres or ToolHive.
- Options: as listed in the reuse analysis (`docs/planning/poc/010-reuse-analysis.md`).
- What the PoCs learned: Langfuse datasets hold the golden cases (ADR-010 item 2); the product plan adds LangSmith datasets as a candidate store (ADR-010 note, phase D). lakeFS, DVC, and ToolHive were never tried.
- `suggested:` accept Langfuse, with LangSmith datasets on trial in phase D; defer lakeFS or DVC to 069 D-7 and the registry base to 076 R-1.
- Phase: D for the golden-set store; the rest `later`.

### Box 10: the router's first home
- Question: where the router runs before a cloud exists, so the baseline counts real traffic.
- Options: the company's existing hosts, or a stand-alone Compose host.
- What the PoCs learned: the router runs inside each deploy stack: PoC-1 Compose (`deploy/compose/litellm/`, no database), the PoC-8 stack that mints a scoped key (`deploy/compose/poc08-litellm/stack.sh`), and kind (`deploy/kind/poc05/platform/`).
- `suggested:` none; for the product the router's home is the deploy stack, so the question has no subject outside the baseline.
- Phase: `parked`, with 005 G-3 (W01-10).

Counts: retire 0, tick 5, parked 1 (issue 005; plus parked boxes in 001, 003, 004, 006), findings 24.
