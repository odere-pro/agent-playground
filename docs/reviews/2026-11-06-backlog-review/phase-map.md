# Phase map: the backlog against the product plan's phases A to F

Session: C2. Date: 2026-11-06. Revised the same day to match [fix-plan.md](fix-plan.md), including its "User decisions, 2026-11-06" (P-1, P-2, P-6, and 023 C-5 in phase A).
Inputs: [fix-plan.md](fix-plan.md) (C1; the source of truth for the apply PRs), the ten `findings-W*.md` files in this folder, the product plan [2026-11-06-agent-container-product.md](../../plans/2026-11-06-agent-container-product.md) ("The order of work", "Gaps the PoC track did not cover"), `docs/planning/tools/backlog.py` `ROWS`, and [011-what-is-left.md](../../planning/poc/011-what-is-left.md).
Companions: [decision-pack-DEC-1.md](decision-pack-DEC-1.md) and [proposals.md](proposals.md).

## How to read

- One section per product phase. Each lists the issues the phase closes, the plan's gaps it covers, what it reuses, its done-when, and its days.
- Issue IDs, sizes, waves, and phases are the ones `fix-plan.md` sets. Numbers are kept. The six new rows sit after 127: 128 CH-9 to 132 CH-13, and 133 X-5b.
- There is one split: 122 X-5 keeps its ID and phase A, and 133 X-5b takes its pool, registry, and event-burst boxes. Every other issue the findings wanted to split is re-scoped as one issue (`fix-plan.md` section 1, "Splits and re-scopes"). A finding that names a half, such as "059a", means the re-scoped issue.
- `Boxes` is met over total after the review's ticks, from `fix-plan.md` section 3. Open boxes are summarized; the body edits in section 3 have the exact text.
- Days use the `sync.py` sizes: `S` 1, `M` 2.5, `L` 5.
- `P-n` is a proposal in [proposals.md](proposals.md): a user decision. A box a proposal touches stays open until it is decided.
- The product plan sizes its phases in weeks and does not say how many engineers. suggested: one week is 5 engineer-days, so "2 weeks" is 10 days.
- Every live issue with a phase A to F appears in exactly one section. Every `later` issue and every parked issue appears in its table at the end.

## Phase A: close the must-haves

Product plan size: 2 weeks (suggested: 10 days). Issue days: **21.5**, 9 issues. **Flag: about twice the plan's size.** Most of it is 024 CH-3 and 026 CH-4 (5 days each), cluster work on an enforcing CNI. 023 C-5 joined on 2026-11-06 by the user's decision; it has a date, 2026-12-02.

### Issues it closes

| Issue | Boxes | Size | Days | Open boxes after the review |
| ----- | ----- | ---- | ---- | --------------------------- |
| 007 H-1 | 6/10 | S (was L) | 1 | image version against package version, with `chassis serve` started in CI; the wheel build; box 2 ticks after the port renames; box 7 rewritten to "unknown `schema_version` refused"; the core import rule is P-10 |
| 009 CH-1 | 6/8 | S (was M) | 1 | one box left: a JSON-RPC error from the workload surfaces as an `a2a.*` code (W2a-18, P-9). Box 3 moves to 024 CH-3; box 5 is scoped to `sidecar` and `remote` (P-7) |
| 010 H-12 | 8/11 | M | 2.5 | box 11 `ctx` half (P-8) and its no-credential half (kind only); box 4 a bad `risk_class` test; box 5 "credential-shaped value refused"; new boxes: reload ceilings for `spec.limits` and `ttl_s`, `/openapi.json` after a reload |
| 018 H-18 | 7/8 | M | 2.5 | box 5 tick-blocked until a sidecar-lane test sends `Idempotency-Key` and reads it from `ctx`; new box: with auth on, the store key and fingerprint include the principal |
| 023 C-5 | 5/8 | S | 1 | the phase A box is the client guide (box 4), published before the AI Act Art. 50(2) date, 2026-12-02 (user decision, 2026-11-06). `fix-plan.md` section 3 has the body edits for the other boxes (box 6 a contract-suite case, box 8 the body field) |
| 024 CH-3 | 3/9 | L (was M) | 5 | box 2; box 3 tag roll and pin; box 4 "on a CNI that enforces NetworkPolicy"; box 5 render only; boxes 7 and 8 on the chart on kind with the chassis as native sidecar; takes 009 box 3, 011 box 8, and a cluster box from 025 H-10 |
| 026 CH-4 | 1/6 | L (was M) | 5 | box 2 MinIO through the chassis; box 3 per-service Valkey user; box 4 public-address and other-pod probe against the `agent-lib` NetworkPolicy; box 5 revoke test; box 6 names the workflow that runs all four kind suites; new boxes: `agent-lib` carries the admission rules, chart defaults pass every PoC-5 admission rule; 056 CH-7's minimum chassis version box (P-16) |
| 055 CH-6 | 5/7 | S (was L) | 1 | box 2: the T10 probe, with `test_poc05_kind_tool_gateway.py` in `remote-lane.yml`; new box: remote output over `spec.limits` ends the run with a fixed error; ADR-005 replaces the `suggested:` markers. The managed runtime moves to `## Out of scope` (ADR-006). Deps lose 038 X-1a and gain 024 CH-3 |
| 122 X-5 | 0/8 | M | 2.5 | re-titled "Load tests: throughput and sidecar overhead on a Linux host": throughput at 4 pairs on a Linux host, xfails emptied, PoC-4 criterion 4 ticked; sidecar p95 CPU and memory and hop p50 and p95 (tick-blocked); the sidecar's cost line (from 120 X-7); chart requests from p95 and the ADR-001 Revisit trigger; the rare 502 at 4 pairs and its source; box 6, a report per run. Boxes 1 to 5 move to 133 X-5b. No deps |

### Gaps it covers

| Gap (product plan) | Owner |
| ------------------ | ----- |
| P1: default-deny egress is not a product rule | 026 CH-4; the chart's NetworkPolicy lives in 024 CH-3 |
| P1: the admission rules are not in the chart | 026 CH-4, two new boxes, plus the minimum-version box from 056 CH-7 (P-16) |
| Phase A item: the sidecar hostile suite in CI (PoC-5 criterion 1) | 026 CH-4 box 6 |
| Phase A item: MinIO through the chassis (PoC-5 criterion 3) | 026 CH-4 box 2 |
| Phase A item: the T10 probe (PoC-5 criterion 8) | 055 CH-6 box 2; the probe workload itself is the user's (011 section 4) |
| Phase A item: throughput at 4 pairs on Linux (PoC-4 criterion 4) | 122 X-5; needs a Linux host (owner list) |
| Not in the product plan: the AI Act Art. 50(2) client guide, due 2026-12-02 | 023 C-5 box 4, moved to phase A by the user on 2026-11-06 |

### What it reuses

- The kind cluster and its suites: `deploy/kind/poc05/`, `pocs/poc-05-sandboxed/tests/test_poc05_kind_sidecar_controls.py`, `test_poc05_kind_hardreq1.py`, `test_poc05_kind_admission.py`, `test_poc05_kind_tool_gateway.py`.
- The admission policy: `deploy/kind/poc05/admission/`.
- The chart: `deploy/helm/agent-lib`, `deploy/helm/example-agent`, `.github/workflows/poc09-helm.yml`, `pocs/poc-09-agent-mvp-template/tests/test_poc09_chart.py`.
- The remote lane: `.github/workflows/remote-lane.yml`, `pocs/poc-05-sandboxed/tests/test_poc05_remote_lane_contract.py`.
- The load kit: `packages/load-tools`, `pocs/poc-04-stateless-scalable/tests/test_load_results.py`, `pocs/poc-04-stateless-scalable/notes/2026-10-01-load-results.md`.
- The AI marking the client guide describes: `packages/chassis/src/chassis/server/marking.py`, `packages/chassis/tests/test_marking.py`, `packages/chassis/tests/test_marking_body.py`.

### Done when

From the product plan: "every PoC-1 to PoC-5 flag has plain evidence; the chart carries the egress and admission rules". The user's decision adds the 023 C-5 client guide, before 2026-12-02.

Evidence that will show it:

- a named workflow that runs the four PoC-5 kind suites on every PR, and a green run of it;
- a test id for the allowed MinIO call through the chassis, next to the existing refusal for the workload;
- a passing T10 runbook run ([poc-05 runbooks](../../guides/poc-05-runbooks.md)) and `test_poc05_kind_tool_gateway.py` green in `remote-lane.yml`;
- a dated `pocs/poc-04-stateless-scalable/notes/` load record at 4 pairs on Linux, and `test_load_results.py` with no xfail;
- `poc09-helm.yml` rendering `agent-lib` with a default-deny NetworkPolicy and admission rules, and the chart's defaults passing every PoC-5 admission rule;
- the client guide, dated before 2026-12-02 and linked from 023 C-5 box 4.

## Phase B: models and the judge

Product plan size: 2 weeks (suggested: 10 days). Issue days: **19.5**, 8 issues. **Flag: about twice the plan's size.** 012 H-3 (5 days) carries the route map. Four issues (002, 003, 028, 042) close only with the Mac run.

### Issues it closes

| Issue | Boxes | Size | Days | Open boxes after the review |
| ----- | ----- | ---- | ---- | --------------------------- |
| 002 G-1 | 2/7 | M | 2.5 | box 2 rewritten (OpenAI and Anthropic formats through the router; closes with the Mac run); box 3 one call per provider (Mac run); box 4 no provider key in router logs (tick-blocked, needs `test_no_provider_key_in_the_router_logs`); box 5 the master key in the `local` Compose variant (P-3); box 7 the router guide |
| 003 G-1b | 0/6 | M (was S) | 2.5 | box 1 `big-default` and `local-small` answer by name (one hosted run); a model swap behind a route; the tool-call flag; route versions; the unknown-route error; the route table. The SLM routes are parked |
| 012 H-3 | 2/10 | L (was M) | 5 | re-titled "Model port to the LLM router, with a route map per task"; three route-map boxes: the map in `ChassisConfig`, admission enforces the resolved route with 403 `model_route_denied`, the map reloads; box 1; box 4 `latency_ms`; box 5 virtual-key spend test; box 6 the agent tag (P-5); box 9 temperature. The vLLM half moves to `## Out of scope` |
| 013 CH-2 | 6/8 | M | 2.5 | box 1 stock Anthropic SDK, offline; box 6 proxy counts against router counts; new box: per-call timeout capped at the run budget, with 017 H-4 |
| 017 H-4 | 6/8 | M (was L) | 2.5 | box 8 scripted model errors; new box: retry on a model error up to `retries`, then `fallback_route`, both lanes, counted; new box: one `timeout_ms` and `max_tokens` across attempts, judge tokens counted; the outage sentence: "An evaluator outage fails open as built (ADR-010 item 4), decided 2026-11-06" (P-1); the budget error code (P-11) |
| 028 S-3 | 0/6 | S (was M) | 1 | re-titled "`facts_kept` and banned-word scoring for the eval gate and the judge"; box 2 a 0–1 `facts_kept` fixture test; box 4 a repeat-run test; box 5 agreement with the 50 hand labels from the Mac run; box 6 judge route and rubric version in the report. Tokens, readability, and cosine parked. No deps |
| 037 M-2 | 3/6 | S (was M) | 1 | re-titled "Eval gate in CI: a change ships only if it passes the golden set against the baseline"; box 3 the baseline floor (tick-blocked by `test_a_run_below_the_floor_fails_with_no_previous_report`); box 5 a prompt change reaches the gate; box 6 the dataset by config. Deps: 028 S-3 only |
| 042 A-2 | 4/7 | M (was S) | 2.5 | box 4 "the gate calls only the configured evaluator adapter"; box 6 the 30-case gate set; box 7 judge cost and tokens in `/metrics` (P-14); new boxes: `judge_route` on the route map, `cloud` profile gate settings with a test each, the judge's latency, cost, and agreement from the Mac run. Box 5 is rewritten to "an evaluator outage fails open" and ticked with `test_an_evaluator_outage_fails_open` (P-1, decided 2026-11-06). Deps: 017 H-4, 028 S-3 |

### Gaps it covers

| Gap (product plan) | Owner |
| ------------------ | ----- |
| P1: no route map per task (`ModelSpec.routes`, `judge_route`) | 012 H-3, re-scoped with three boxes (`fix-plan.md` correction 20); `judge_route` is a 042 A-2 box; `agentctl`'s route check goes to 059 H-15 |
| P1: the judge is off in every profile; the gate is off; no real judge numbers | 042 A-2 (`cloud` profile, outage rule, metrics, Mac numbers); the agreement box is 028 S-3 box 5; the run itself is the user's (011 section 1) |
| P2: the retry stage on a model error is not built | 017 H-4, two new boxes |
| Phase B item: the gate on by default, async at 0.1 on `decision-small` | 042 A-2 `cloud` profile box; the outage rule is decided: fail open everywhere, as built (P-1, 2026-11-06) |

### What it reuses

- The config and profiles: `packages/chassis/src/chassis/server/config.py`, `packages/chassis/src/chassis/profiles.py` (both changed by the product plan).
- The gate and the judge: `packages/chassis/src/chassis/server/evaluator_gate.py`, `packages/chassis/src/chassis/adapters/evaluators.py`, `packages/chassis/tests/test_evaluator_gate.py`, `pocs/poc-08-cross-cutting-build/tests/test_poc08_c6_gate.py`.
- The golden cases and the offline gate: `packages/eval-kit/src/eval_kit/golden/cases-gate.yaml`, `evals/deepeval`, `evals/gate/baseline.json`, `.github/workflows/poc08-evals.yml`.
- The router: `deploy/compose/litellm/config.yaml`, `deploy/compose/poc08-litellm/stack.sh`.
- The route-switch test pattern: `pocs/poc-06c-pretrained-slm/tests/test_poc06c_route_switch.py::test_only_the_route_changes_and_every_task_passes_both_times`. PoC-6c is parked; the test shape is reused, not the SLM.

### Done when

From the product plan: "a config map swaps a model per task with no agent change; the judge's numbers are recorded and the defaults confirmed or changed by ADR-010's rule".

Evidence that will show it:

- a test id that changes only the route map in config and shows a task's model change with `handle` and the workload unchanged;
- the reload test for the route map, and the 403 `model_route_denied` test;
- a dated `notes/` record from the Mac run with the judge's latency, cost per gated call, and agreement on the 50 labels;
- an ADR-010 amendment that confirms or changes the defaults (mode, sample rate); the outage rule is already decided (fail open).

The product plan says phase B cannot close without the Mac run.

## Phase C: LangChain first

Product plan size: 2 weeks (suggested: 10 days). Issue days: **6**, 2 issues. Below the plan's size: step 1 is already done (below).

### Issues it closes

| Issue | Boxes | Size | Days | Open boxes after the review |
| ----- | ----- | ---- | ---- | --------------------------- |
| 058 CH-8 | 4/5 | L (was M) | 5 | box 2 a contract test per mapping; the guide; the MCP adapter swap to `langchain-mcp-adapters` on `mcp` 2, or a test that fails when it is ready; the LangGraph trace-join test; "`agentctl new` defaults to LangGraph and passes the lane contract and the evals" (the evals half); a `langchain` `create_agent` variant. The two ADRs go to 128 CH-9. Deps: 015 H-8 only |
| 128 CH-9 (new) | 0/4 | S | 1 | "LangGraph checkpointer and interrupts: one ADR each". The checkpointer ADR accepted and linked from ADR-006; the interrupt ADR accepted and linked from ADR-006 and the contract spec; a template test for the chosen interrupt mapping, or a clear refusal; ADR-006's two Revisit triggers closed or restated with a date. Wave W5, deps 058 CH-8, owner `chassis-architect` |

### Gaps it covers

| Gap (product plan) | Owner |
| ------------------ | ----- |
| P1: LangGraph's MCP client is a stand-in | 058 CH-8 |
| P1: its instrumentor coverage is a doc claim | 058 CH-8, the trace-join test |
| P3: no checkpointer; interrupts have no place in the `handle` contract | 128 CH-9 |
| Phase C item: LangGraph as the template default | done: PR #71 merged (`cadc99b`); `DEFAULT_FRAMEWORK = "langgraph"` in `packages/agentctl/src/agentctl/rules.py`; recorded in the plan and ADR-006 by #75 (`7e49652`). The product plan at `70244ad` still calls it unmerged |

### What it reuses

- `packages/workloads/echo-langgraph`, `packages/agentctl`, `packages/agent-testkit`, `packages/workload-a2a`.
- The engine contract suites: `pocs/poc-06a-bake-off-sidecar-lane/tests/test_poc06a_engine_contract.py`, `pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_remote_engine_contract.py`.
- The trace-join pattern: `pocs/poc-07-cross-cutting-decisions/tests/test_poc07_obs_trace_join.py`.
- `.github/workflows/poc09-scaffold.yml` for the generated repo.

### Done when

From the product plan: "`agentctl new` defaults to LangGraph and passes the lane contract and the evals; the two ADR-006 Revisit triggers are closed".

Evidence that will show it:

- a `poc09-scaffold.yml` run where the default scaffold passes the lane contract and the offline evals;
- the LangGraph trace-join test id, with graph steps under the run span;
- the MCP adapter swap test, or the test that fails when `langchain-mcp-adapters` on `mcp` 2 is ready;
- two accepted ADRs, the checkpointer and interrupts (128 CH-9).

## Phase D: LangSmith

Product plan size: 1–2 weeks (suggested: 5 to 10 days). Issue days: **4.5**, 3 new issues. Slightly under the plan's lower bound.

### Issues it closes

| Issue | Boxes | Size | Days | Open boxes |
| ----- | ----- | ---- | ---- | ---------- |
| 129 CH-10 (new) | 0/5 | M | 2.5 | "LangSmith as a trace backend: Collector exporter, fake-langsmith, stack tests". The exporter behind a profile switch, the default stack runs without it; `fake-langsmith` beside `fake-langfuse` records an OTLP trace, read back offline; a stack test: one run is one trace, graph steps under the run span; a stack test: the LangSmith key is in the Collector only; the primary trace view written where DEC-1 records it. Wave W5, deps 014 H-7, owner `observability-expert` |
| 130 CH-11 (new) | 0/4 | S | 1 | "LangSmith feedback adapter behind FeedbackPort". Passes the `FeedbackPort` contract suite against `fake-langsmith`; `POST /v1/feedback` writes one score on the run's trace; picked by `spec.adapters` only, key in the chassis; with both backends on, the score goes where DEC-1 says. Wave W5, deps 129 CH-10, owner `observability-expert` |
| 131 CH-12 (new) | 0/5 | S | 1 | "Golden cases in Langfuse and LangSmith datasets". The 20 cases and one run per engine in a LangSmith dataset through `fake-langsmith`; verdicts match on two engines; a stack test for the Langfuse dataset and its runs; a workflow or Makefile target calls the upload; the dataset name from config. Wave W6, deps 037 M-2 and 129 CH-10, owner `eval-expert` |

### Gaps it covers

| Gap or phase item | Owner |
| ----------------- | ----- |
| P1: LangSmith is in no code or config: the `otlphttp/langsmith` exporter, `fake-langsmith`, the two stack tests | 129 CH-10 |
| P1: Langfuse is the only `FeedbackPort` backend (ADR-009 item 3a) | 130 CH-11 |
| Phase D item: LangSmith datasets against the 20 golden cases (ADR-010's trial rule), and the Langfuse dataset stack test | 131 CH-12 |
| Phase D decision: the primary trace view per environment, and where the score goes when both run | 001 DEC-1, new box; decision 2 in the decision pack |

### What it reuses

- The Collector and its stack: `deploy/compose/poc07/otel-collector.yaml` (gains the exporter), `deploy/compose/poc07/stack.sh`, `.github/workflows/poc07-stack.yml`.
- The fake pattern: `packages/fake-langfuse` for `fake-langsmith`.
- The feedback adapter pattern: `packages/chassis/src/chassis/adapters/langfuse`.
- The dataset uploader and its stub test: `packages/eval-kit/src/eval_kit/langfuse_datasets.py`, `packages/eval-kit/tests/test_eval_kit_langfuse_datasets.py`, and the cases in `packages/eval-kit/src/eval_kit/golden/`.

### Done when

From the product plan: "one trace, one score, and one eval run visible in LangSmith, with the key only in the Collector and the chassis".

Evidence that will show it:

- the 129 CH-10 and 131 CH-12 stack tests against `fake-langsmith`: one trace, one score, one dataset run;
- the 129 CH-10 key test, and `LANGSMITH_TRACING` unset in the workload;
- one dated record of the same run against the real LangSmith (a user run: the key stays on the user's side).

## Phase E: measure and show

Product plan size: 2 weeks (suggested: 10 days). Issue days: **21.5**, 10 issues. **Flag: about twice the plan's size.**

### Issues it closes

| Issue | Boxes | Size | Days | Open boxes after the review |
| ----- | ----- | ---- | ---- | --------------------------- |
| 001 DEC-1 | 2/10 | S | 1 | box 1 closes when 2, 3, 4, and 6 close; box 2 broker; box 3 first cloud (rewritten); box 4 public A2A priority; box 6 rewritten to the north star with draft targets; new box: primary trace view; box 7 owners; box 8b other reuse picks. Boxes 5 and 10 parked. See the decision pack |
| 004 G-2 | 1/7 | M | 2.5 | one usage record per call; the totals query; the `unknown` user; provider-usage match (Mac run); box 6 H31, needs `test_spoofed_agent_tag_counts_under_key_agent` on kind; box 7 rewritten (labels by agent and route, `user` in the usage record only), tick-blocked on a dated PoC-8 note. Box 8 parked |
| 006 G-6 | 0/4 | S (was M) | 1 | re-titled "Token and cost dashboard with live spend"; panels within one minute (`test_every_panel_query_has_data`); filter by agent and route; box 6 "the PoC-8 stack loads every dashboard with no manual steps", tick-blocked on one dated record. Savings boxes parked |
| 014 H-7 | 6/8 | M (was S) | 2.5 | box 6 Langfuse fields; three phase E boxes: sampling as a Collector policy, the guard stage's own span under the run span, the OTel SDK's CPU and latency cost measured on the PoC-8 stack in a dated note; box 2 ticks after the rewrite to "the Collector's trace backend". Deps: 007 H-1, 009 CH-1 |
| 022 H-6 | 5/11 | M | 2.5 | box 1 `/metrics` with no token (P-4), split into an OIDC box and a caller API key box; box 3 two chassis on one Valkey; box 6 names never masked (Q6); box 7 memory with redaction; box 8 Prompt Guard 2 (Mac run); new boxes: a cap on new idempotency keys per caller, the workload `guardrails` key never reaches LiteLLM. No fail-closed box: a rate-limit store outage lets calls through unlimited, as built (P-2, decided 2026-11-06; one sentence in `## What`) |
| 046 A-5 | 1/6 | M (was S) | 2.5 | re-titled "Container dashboard and per-agent gate panels": every panel in Compose and on Kubernetes; first-try pass rate; retry rate; fallback rate with the 10% line; links to the trace view and Loki; rows for the north star, inputs, and counters, with an up-to-date test. Deps: 001 DEC-1, 014 H-7, 042 A-2 |
| 054 H-16 | 3/11 | L (was M) | 5 | box 2 the `tool_denied` log line; box 4 one tool allow-list or two (P-17); box 5 retry with one `Idempotency-Key`; box 7 retry then fallback on a malformed tool call; box 8 tool-call count and allow-list unchanged, plus 2 red-team misses; box 9 tool calls charged to the run; box 11 through the proxy in each lane; new box: a bare tool name resolves or is refused. Box 1 parked. Deps: 018 H-18, 017 H-4, 022 H-6 |
| 117 X-3 | 0/9 | M | 2.5 | 6 boxes today, 9 after the edits: SLO rules from an `slo` block; a burn-rate error alert with a fault test on Compose; a p95 latency fault test; the fallback-above-10% alert; a version label on `chassis_build_info`; SLO target and error budget on the dashboards; box 7, the weekly note; two alert boxes from the fail-open decisions: `gate_failed_open / gate_runs` above suggested: 1% over 15 minutes (P-1), and the rate-limit store unavailable (P-2). Size unchanged. Alerts for parts not built move to `## Out of scope`. Deps: 014 H-7 only |
| 120 X-7 | 0/6 | S | 1 | cost per agent and per good task for day, week, and month (box 1 tick-blocked: only a per-hour panel exists); API cost matches the proxy; rates as config, not 031 G-5; the baseline next to the first week. Per-pool cost moves to 104 O-3; the sidecar's cost line moves to 122 X-5. Deps: 004 G-2 only |
| 132 CH-13 (new) | 0/4 | S | 1 | "OWASP agentic threat list (ASI01 to ASI10) mapped to the controls": a table of ASI01 to ASI10 with control and evidence, or "gap" with its owner; the tool-description poisoning row names Snyk Agent Scan and 081 R-8; every gap has an open box; a recorded `platform-security` review. Wave W11, deps 026 CH-4 and 022 H-6 |

### Gaps it covers

| Gap (product plan) | Owner |
| ------------------ | ----- |
| P2: no SLOs, no alerts | 117 X-3, including the two fail-open alerts |
| P2: no container dashboard | 046 A-5, re-scoped (`fix-plan.md` correction 21); 006 G-6 is its token and cost input; no new X-9 |
| P2: no weekly record | 117 X-3 box 7, the weekly note |
| P2: no guard-stage span; sampling deferred; the OTel SDK's cost unmeasured | 014 H-7, three boxes |
| P2: `/metrics` open by default | 022 H-6 box 1, after P-4 |
| P2: the OWASP agentic list (ASI01–ASI10) and the vault's threat model not mapped to the controls | 132 CH-13; inputs 054 H-16 (ASI02, tool misuse), `deploy/agent-scan/`, and 081 R-8 |
| Product plan open item 1: the north star | 001 DEC-1 box 6; decision 1 in the decision pack |

### What it reuses

- The dashboard generator: `packages/chassis/src/chassis/dashboards` (gains `deploy/dashboards/container.json`), `deploy/dashboards`, `packages/chassis/tests/test_dashboards.py`.
- The metric families: `packages/chassis/src/chassis/metrics.py`, `packages/chassis/tests/test_metrics.py`, and the formulas in [metrics](../../specs/metrics.md).
- The stacks: `deploy/compose/poc07/` (Collector, Langfuse, Jaeger) and the PoC-8 Prometheus and Grafana stack (`.github/workflows/poc08-stack.yml`, `pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_static.py`).
- The tool gateway: `packages/chassis/tests/test_tool_gateway_contract.py`, `pocs/poc-05-sandboxed/tests/test_poc05_kind_tool_gateway.py`.
- The guardrail server and the agent scan: `packages/guardrail-server`, `deploy/agent-scan/`, `.github/workflows/poc07-agent-scan.yml`.

### Done when

From the product plan: "the north star, inputs, and counters are on one dashboard with one week of real numbers".

Evidence that will show it:

- `deploy/dashboards/container.json` written by `python -m chassis.dashboards`, with an up-to-date test in `test_dashboards.py`;
- the alert fault tests on Compose (burn rate, p95, fallback rate);
- 117 X-3's weekly note with seven days of real numbers.

Risk: "one week of real numbers" needs real traffic for a week. Phases A to F run on kind and Compose, and the first cloud is `later`. Decision 8 in the decision pack asks where that week runs.

## Phase F: template GA

Product plan size: 1–2 weeks (suggested: 5 to 10 days). Issue days: **13.5**, 5 issues. **Flag: above the plan's upper bound.** 059 H-15 (5 days) holds the template's phase F boxes and the trial.

### Issues it closes

| Issue | Boxes | Size | Days | Open boxes after the review |
| ----- | ----- | ---- | ---- | --------------------------- |
| 015 H-8 | 4/10 | M (was L) | 2.5 | box 2 a mutant without `status`, both transports; box 3 the event-mapping suite over a fixture; box 6 scripted errors for the in-memory config, telemetry, and engine fakes; box 10 `make test-integration` in a CI workflow; the record-host guard; new box: the fake model scripted per case; boxes 7 and 8 reworded |
| 016 H-9 | 6/9 | S | 1 | box 1 rewritten to the template stack (Postgres moves to the first issue that needs it); box 4 hot reload. Box 8 parked. Deps: 011 H-2, 009 CH-1 |
| 025 H-10 | 1/10 | M (was L) | 2.5 | the phase F boxes stay (1, 7, 8, 9, 10); new boxes: the generated repo carries a chart on `agent-lib`, and the template pins a published platform release, not the `.platform` link. The cluster boxes go to 038 X-1a, 024 CH-3, 021 H-20, and 083 R-2. Registration is by label, through the controller (083 R-2) |
| 057 H-19 | 1/6 | M (was S) | 2.5 | box 1 a stateless step in the template CI; box 2 the lint rule; box 3 "the template's workload for each supported framework passes"; box 4 a fixed order; box 6 the admission policy. Deps: 025 H-10, 015 H-8 |
| 059 H-15 | 0/10 | L (was M) | 5 | box 5 the Anthropic route (a `demo.sh --live` rerun on LangGraph calling `/v1/messages`, as a dated note); box 6 the remote chart in the generated repo and box 7 `--trust` (both P-15); boxes 8 and 9; the generated repo pins the published release; `agentctl`'s route check (from 012 H-3); the trial box. Box 1 and the five kinds move to 008 H-14. The chart and the pin are 025 H-10's; 059 renders them. Deps: 025 H-10, 057 H-19, 058 CH-8, 055 CH-6, 024 CH-3 |

### Gaps it covers

| Gap (product plan) | Owner |
| ------------------ | ----- |
| P2: the template's fake model cannot be scripted per case | 015 H-8, one new box |
| P2: the platform pin is a local symlink | 025 H-10 holds the published pin; 059 H-15 renders it (`fix-plan.md` correction 22) |
| Phase F item: the chart in the generated repo | 025 H-10 holds the chart; 059 H-15 renders it |
| P2: no trial by a person new to the code | 059 H-15, one new box; the steps are in `pocs/poc-09-agent-mvp-template/notes/2026-10-30-new-user-trial.md`; the user names the person |

### What it reuses

- `packages/agentctl`, `packages/agent-testkit`, `packages/workload-a2a`, `packages/fake-model-server`.
- `deploy/helm/agent-lib`, `deploy/helm/example-agent`.
- `.github/workflows/poc09-scaffold.yml`, `.github/workflows/poc09-helm.yml`, `pocs/poc-09-agent-mvp-template/tests/test_poc09_scaffold.py`, `test_poc09_template_ops.py`.
- The checks already run on the Mac: `pocs/poc-09-agent-mvp-template/notes/2026-11-05-machine-checks.md`.

### Done when

From the product plan: "a person goes from `agentctl new` to a demo in under a day, recorded".

Evidence that will show it:

- a `poc09-scaffold.yml` run where the generated repo uses the published pin and carries its chart;
- a dated trial record, timed, by a person new to the code, from `agentctl new` to a demo.

## Cross-phase dependencies

These `ROWS` edges, after `fix-plan.md`'s dep changes, point from an earlier phase to a later one. `check.py` allows them: each points to a lower number. Each may need only the delivered part of its target. The apply PRs leave them; a phase lead checks them before the phase starts.

| From | Depends on | Phase order |
| ---- | ---------- | ----------- |
| 018 H-18 (A), 024 CH-3 (A) | 016 H-9 (F) | A waits on F |
| 026 CH-4 (A) | 002 G-1 (B) | A waits on B; the plan runs A and B in parallel |
| 012 H-3 (B) | 004 G-2 (E) | B waits on E |
| 058 CH-8 (C) | 015 H-8 (F) | C waits on F |
| 129 CH-10 (D) | 014 H-7 (E) | D waits on E |
| 009 CH-1 (A) | 008 H-14 (`later`) | A waits on a `later` issue; 009 needs only 008's lane and trust boxes, which are ticked |
| 023 C-5 (A) | 019 H-17 (`later`) | A waits on a `later` issue; the client guide needs the built marking, not the event port |

Nine live issues depend on 011 H-2, which retires (015, 016, 021, 022, 023, 050, 051, 061, 062). A done row counts as met (`fix-plan.md` section 4).

## Later issues by wave

72 issues, 190.5 days. `later` means no product phase needs the issue. Sizes are the ones `fix-plan.md` sets.

| Wave | Issues | Days | Issues (size) |
| ---- | ------ | ---- | ------------- |
| W2 | 4 | 15 | 008 H-14 (L), 019 H-17 (M), 020 X-8 (L), 021 H-20 (M) |
| W3 | 1 | 5 | 038 X-1a (L) |
| W4 | 1 | 5 | 040 D-0 (L) |
| W5 | 8 | 16.5 | 050 CH-5 (M), 051 H-13 (M), 052 H-21 (S), 053 H-22 (M), 056 CH-7 (L), 060 H-23 (S), 061 H-5 (S), 062 H-11 (S) |
| W6 | 9 | 19 | 064 D-1 (M), 065 D-2 (M), 066 D-6 (S), 067 D-5 (S), 068 D-3 (S), 069 D-7 (M), 070 D-4 (S), 071 D-8 (M), 072 C-3 (L) |
| W7 | 18 | 39.5 | 076 R-1 (L), 077 C-1 (M), 078 R-16 (S), 079 C-2 (S), 080 R-7 (M), 081 R-8 (M), 082 R-9 (S), 083 R-2 (M), 084 R-3 (S), 085 R-6 (M), 086 R-10 (M), 087 R-4 (S), 088 R-5 (M), 089 R-15 (S), 090 R-13 (M), 091 R-11 (L), 092 R-12 (M), 093 R-14 (S) |
| W8 | 4 | 8.5 | 094 P-1 (M), 095 P-4 (M), 096 P-2a (M), 097 P-3 (S) |
| W9 | 18 | 58.5 | 098 O-10 (M), 099 O-1 (M), 100 O-2 (L), 101 O-13 (M), 102 O-7 (M), 103 O-11 (L), 104 O-3 (L), 105 O-4 (M), 106 O-5 (M), 107 O-6 (M), 108 O-12 (L), 109 O-8 (M), 110 C-6 (M), 111 O-14 (L), 112 O-16 (M), 113 O-9 (L), 114 P-2b (M), 115 O-15 (S) |
| W10 | 5 | 15 | 116 X-1b (L), 118 C-7 (M), 119 X-6 (M), 121 X-4 (M), 133 X-5b (M, new) |
| W11 | 2 | 3.5 | 123 C-4 (M), 124 C-9 (S) |
| W12 | 2 | 5 | 125 L-1 (M), 126 L-2 (M) |

Notes:

- 008 H-14 is one live issue, size L (was M): lane and trust are built and ticked; class, kind, modules, the B.5 table, and 059 H-15's five `agentctl` kinds are open. 040, 072, 076, and 099 depend on it.
- 019 H-17 takes the two live result-event boxes from 043 A-3, which is parked.
- 113 O-9 is long-term memory only, size L; run status moved to 100 O-2.
- 133 X-5b (new): pool scale to and from zero, pool p95, registry p95 at 5,000 entries, registry top 3 under load, an event burst with no loss, a report per run. Deps 091 R-11, 104 O-3, 117 X-3.
- 077 C-1 holds the E.4 governance fields (`fix-plan.md` correction 18). It stays `later`.

## Parked: the SLM track (2026-11-06)

23 issues, 52.5 days. Each keeps its number and size and moves to wave `WP`.

| Issue | Old wave | Size | Days |
| ----- | -------- | ---- | ---- |
| 005 G-3 | W1 | M | 2.5 |
| 027 S-1 | W3 | S | 1 |
| 029 S-2 | W3 | M | 2.5 |
| 030 S-4 | W3 | M | 2.5 |
| 031 G-5 | W3 | S | 1 |
| 032 M-1 | W3 | S | 1 |
| 033 S-5 | W3 | M | 2.5 |
| 034 C-8 | W3 | S | 1 |
| 035 S-6 | W3 | L | 5 |
| 036 S-7 | W3 | S | 1 |
| 039 X-2 | W3 | M | 2.5 |
| 041 A-1 | W4 | M | 2.5 |
| 043 A-3 | W4 | S | 1 |
| 044 G-4 | W4 | M | 2.5 |
| 045 A-4 | W4 | S | 1 |
| 047 M-3 | W4 | M | 2.5 |
| 048 M-4 | W4 | S | 1 |
| 049 M-5 | W4 | M | 2.5 |
| 063 S-8 | W5 | L | 5 |
| 073 E-1 | W6 | L | 5 |
| 074 E-2 | W6 | S | 1 |
| 075 E-3 | W6 | S | 1 |
| 127 L-3 | W12 | L | 5 |

Parked boxes inside live issues (not counted here): 001 boxes 5 and 10, 003's SLM routes, 004 box 8, 006's savings boxes, 012's vLLM half, 016 box 8, 028's token and readability metrics, 054 box 1, and the MLflow bullets in 069 and 071. 116 X-1b stays live; only its GPU bullets are parked.

## Done

| Issue | Days | Note |
| ----- | ---- | ---- |
| 011 H-2 | 5 | Retires to wave `WD`. P-6 was decided on 2026-11-06: box 7 becomes "`/ready` goes 503 first; in-flight runs finish; the workload stops after the chassis has drained" and is ticked with the drain tests. Box 5 is rewritten to the fixed `/ready` list and ticked; box 8 moves to 024 CH-3 |

008 H-14 does not retire. `fix-plan.md` keeps it whole: phase `later`, size L (see the later table).

## Totals

Recounted with a read-only snippet over `backlog.build()`, run from `docs/planning/tools` with `python3 -B`. It holds `fix-plan.md` section 4's change set (retired, parked, sizes, new rows, phases) and writes nothing.

```python
"""Read-only: phase totals from the fix-plan.md change set (C2 recount). Run from docs/planning/tools."""

import sys
from collections import defaultdict

sys.path.insert(0, ".")
import backlog  # noqa: E402

DAYS = {"S": 1.0, "M": 2.5, "L": 5.0}
WD = {"H-2"}  # 011 H-2; P-6 decided 2026-11-06
WP = {
    "G-3",
    "S-1",
    "S-2",
    "S-4",
    "G-5",
    "M-1",
    "S-5",
    "C-8",
    "S-6",
    "S-7",
    "X-2",
    "A-1",
    "A-3",
    "G-4",
    "A-4",
    "M-3",
    "M-4",
    "M-5",
    "S-8",
    "E-1",
    "E-2",
    "E-3",
    "L-3",
}
SIZE = {
    "G-1b": "M",
    "G-6": "S",
    "H-1": "S",
    "H-14": "L",
    "CH-1": "S",
    "H-3": "L",
    "H-7": "M",
    "H-8": "M",
    "H-4": "M",
    "X-8": "L",
    "H-20": "M",
    "CH-3": "L",
    "H-10": "M",
    "CH-4": "L",
    "S-3": "S",
    "M-2": "S",
    "D-0": "L",
    "A-2": "M",
    "A-5": "M",
    "H-13": "M",
    "H-22": "M",
    "H-16": "L",
    "CH-6": "S",
    "CH-7": "L",
    "H-19": "M",
    "CH-8": "L",
    "H-15": "L",
    "C-1": "M",
    "R-10": "M",
    "P-1": "M",
    "O-2": "L",
    "O-13": "M",
    "O-3": "L",
    "O-6": "M",
    "O-8": "M",
    "O-14": "L",
    "L-1": "M",
    "L-2": "M",
}
# New rows after 127: (id, wave, size)
NEW = [
    ("CH-9", "W5", "S"),
    ("CH-10", "W5", "M"),
    ("CH-11", "W5", "S"),
    ("CH-12", "W6", "S"),
    ("CH-13", "W11", "S"),
    ("X-5b", "W10", "M"),
]
PHASE = {
    p: ids.split()
    for p, ids in {
        "A": "H-1 CH-1 H-12 C-5 H-18 CH-3 CH-4 CH-6 X-5",
        "B": "G-1 G-1b H-3 CH-2 H-4 S-3 M-2 A-2",
        "C": "CH-8 CH-9",
        "D": "CH-10 CH-11 CH-12",
        "E": "DEC-1 G-2 G-6 H-7 H-6 A-5 H-16 X-3 X-7 CH-13",
        "F": "H-8 H-9 H-10 H-19 H-15",
    }.items()
}
PHASE_OF = {eid: p for p, ids in PHASE.items() for eid in ids}

rows = [
    (i, it["epic_id"], it["wave"], SIZE.get(it["epic_id"], it["size"]))
    for i, it in backlog.build().items()
]
rows += [(127 + n, eid, wave, size) for n, (eid, wave, size) in enumerate(NEW, 1)]


def bucket(eid):
    if eid in WD:
        return "done"
    if eid in WP:
        return "parked"
    return PHASE_OF.get(eid, "later")


tot, cnt, later_wave = defaultdict(float), defaultdict(int), defaultdict(list)
for idx, eid, wave, size in rows:
    b = bucket(eid)
    tot[b] += DAYS[size]
    cnt[b] += 1
    if b == "later":
        later_wave[wave].append(f"{idx:03d} {eid} ({size})")

print(f"rows {len(rows)}")
for b in ["A", "B", "C", "D", "E", "F", "later", "parked", "done"]:
    print(f"{b:7} {cnt[b]:3} issues {tot[b]:6.1f} days")
open_af = sum(tot[p] for p in "ABCDEF")
print(f"open A-F {open_af:g} | all open {open_af + tot['later']:g}")
for p in "ABCDEF":
    print(p, [f"{i:03d} {e} {s}" for i, e, _, s in rows if bucket(e) == p])
for w in list(backlog.WAVES):
    if later_wave[w]:
        days = sum(DAYS[x[-2]] for x in later_wave[w])
        print(f"later {w}: {len(later_wave[w])} issues {days:g} days")
```

Output:

```
rows 133
A         9 issues   21.5 days
B         8 issues   19.5 days
C         2 issues    6.0 days
D         3 issues    4.5 days
E        10 issues   21.5 days
F         5 issues   13.5 days
later    72 issues  190.5 days
parked   23 issues   52.5 days
done      1 issues    5.0 days
open A-F 86.5 | all open 277
A ['007 H-1 S', '009 CH-1 S', '010 H-12 M', '018 H-18 M', '023 C-5 S', '024 CH-3 L', '026 CH-4 L', '055 CH-6 S', '122 X-5 M']
B ['002 G-1 M', '003 G-1b M', '012 H-3 L', '013 CH-2 M', '017 H-4 M', '028 S-3 S', '037 M-2 S', '042 A-2 M']
C ['058 CH-8 L', '128 CH-9 S']
D ['129 CH-10 M', '130 CH-11 S', '131 CH-12 S']
E ['001 DEC-1 S', '004 G-2 M', '006 G-6 S', '014 H-7 M', '022 H-6 M', '046 A-5 M', '054 H-16 L', '117 X-3 M', '120 X-7 S', '132 CH-13 S']
F ['015 H-8 M', '016 H-9 S', '025 H-10 M', '057 H-19 M', '059 H-15 L']
later W2: 4 issues 15 days
later W3: 1 issues 5 days
later W4: 1 issues 5 days
later W5: 8 issues 16.5 days
later W6: 9 issues 19 days
later W7: 18 issues 39.5 days
later W8: 4 issues 8.5 days
later W9: 18 issues 58.5 days
later W10: 5 issues 15 days
later W11: 2 issues 3.5 days
later W12: 2 issues 5 days
```

| Bucket | Issues | Days | Product plan |
| ------ | ------ | ---- | ------------ |
| A | 9 | 21.5 | 2 weeks (suggested: 10) |
| B | 8 | 19.5 | 2 weeks (suggested: 10) |
| C | 2 | 6 | 2 weeks (suggested: 10) |
| D | 3 | 4.5 | 1–2 weeks (suggested: 5–10) |
| E | 10 | 21.5 | 2 weeks (suggested: 10) |
| F | 5 | 13.5 | 1–2 weeks (suggested: 5–10) |
| A to F | 37 | 86.5 | 10–12 weeks (suggested: 50–60) |
| later | 72 | 190.5 | — |
| parked | 23 | 52.5 | — |
| done | 1 | 5 | — |

All open work is 277 days (A to F plus `later`). The first estimate was 298.5 days with W12, or 283.5 for W0 to W11 as `baseline.md` counts it. Phases A to F hold 86.5 days against the product plan's 50 to 60. The phases run partly in parallel (A with B, E after A), so two engineers could still fit A and B into the plan's two weeks.

## Differences from fix-plan.md

None. The recount above gives `fix-plan.md` section 4 "after" with its "User decisions, 2026-11-06" applied: 023 C-5 moves from `later` to phase A (A 9 issues, 21.5 days; `later` 72 issues, 190.5 days), and 011 H-2 retires without a condition. Open A to F is 86.5 days; all open stays 277. The first version of this file split nine issues into halves; this version follows `fix-plan.md`'s one split (133 X-5b) and its re-scopes.
