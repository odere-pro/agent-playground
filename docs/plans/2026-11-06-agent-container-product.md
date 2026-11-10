# The agent container: the plan

Status: in progress
Date: 2026-11-06

## What this is

The one plan to ship the agent container. It shows what is in progress and what is left, in order. What the container *is* lives in [the specs](../specs/README.md). Why each rule exists lives in the ADRs. What each PoC proved lives in the PoC READMEs and in [what is left](../planning/poc/011-what-is-left.md). Done work is in git history and `HANDOVER.md`, not here.

## Decisions

From the user, 2026-11-06:

- PoC-1 to PoC-5 are must-haves. The architecture stays as built: [architecture](../specs/architecture.md).
- The SLM track is parked (PoC-6c, 127 L-3, and the vLLM half of [012 H-3](../planning/issues/012-H-3-model-port.md)). The model stays swappable from a route map per task: [models](../specs/models.md).
- A cheap judge model is on by default, behind `EvaluatorPort`: [models](../specs/models.md), "The judge model".
- Security and observability are must-haves, and the container measures and shows them: [security](../specs/security.md), [observability](../specs/observability.md), [metrics](../specs/metrics.md).
- LangChain, LangGraph, and LangSmith come first: [ADR-006](../planning/adr/006-agent-engines-default-supported-lanes.md), [ADR-007](../planning/adr/007-observability-otel-collector-langfuse.md) item 10, [ADR-009](../planning/adr/009-feedback-langfuse-scores-event.md) item 3a, and a note in [ADR-010](../planning/adr/010-evals-offline-tool-online-gate.md).
- P-1 and P-2: an evaluator outage and a rate-limit store outage fail open, as built. P-26 (2026-11-08) makes `cloud` fail closed on a store outage.
- P-6: the drain follows the contract. P-29 picked the container order in [ADR-012](../planning/adr/012-drain-order-on-rollout.md).

The sources are the decisions in the [backlog review proposals](../reviews/2026-11-06-backlog-review/proposals.md).

Open, for the user:

1. **The north star.** "Good tasks per week", or "agents in production on the chassis" ([metrics](../specs/metrics.md)).
2. **The primary trace view per environment**, LangSmith or Langfuse, and where the score goes when both run (ADR-007 open question 4, ADR-009 open question 5).

## Progress

Measured on `main` with `make progress`, 2026-11-10: 144 of 344 boxes ticked (41%), 4 of 44 issues done. 200 boxes are open.

Estimated, not measured: the open and queued PRs below add about 30 to 35 boxes. After they merge, that is about 175 to 180 boxes, near 52%, with about 165 left. Run `make progress` again after the merges for the real numbers.

## In progress

Open PRs on GitHub, waiting on the user's merge:

| PR | Issue | What |
| -- | ----- | ---- |
| 131 | (cross-cutting) | Counters start at 0; a `chassis_build_info` metric |
| 132 | 007 H-1 | The last boxes; retires the issue |
| 133 | 002 G-1, 023 C-5 | The router guide and the disclosure client guide |
| 134 | 015 H-8 | Testing kit boxes |
| 135 | 057 H-19 | Stateless check boxes |
| 136 | 022 H-6 | Security middleware boxes |
| 137 | 132 CH-13 | The OWASP agentic map |
| 138 | 017 H-4 | Harness feature boxes |
| 139 | 009 CH-1 | Strict xfails until P-7 and P-9 are picked |

Built and reviewed, in the commit queue, no PR yet: 012 H-3, 024 CH-3, 010 H-12, 008 H-14, 037 M-2, 004 G-2 (a test only, no box), 016 H-9, 013 CH-2, 056 CH-7, 019 H-17, 054 H-16, 059 H-15, 137 CH-17 (code only, no box), 129 CH-10, 046 A-5. 008 H-14 and 019 H-17 are in no phase, so their boxes do not count above.

Stopped: 003 G-1b. A fix round is half applied and waits on the user.

## Left to do

### 1. Offline, buildable now

- 014 H-7, 018 H-18, and 042 A-2. Start them after PRs 131, 136, and 138 merge.
- 025 H-10, 130 CH-11, 131 CH-12, 138 CH-18, and 005 G-3.
- 028 S-3: the four scoring boxes. Its judge agreement box needs the Mac run.
- 058 CH-8: two boxes. The eval box waits on 015 H-8.

### 2. Needs a user decision

- 009 CH-1: P-7 (cancel and streaming in `inprocess`) and P-9 (a JSON-RPC error from the workload), and the error code name.
- 055 CH-6: a default proxy key, or opt-in, for the two-key workload token Secret.
- 012 H-3: refuse a route that is not allowed, or force the allowed one.
- 010 H-12: which bootstrap fields act as ceilings, and what `/openapi.json` shows after a reload.
- 019 H-17: whether result events leave out `input` by default, and the model-source field name.
- 059 H-15: the `--trust` flag (P-15).
- 046 A-5: a run-level first-attempt counter. It is a contract change.
- 132 CH-13: the `platform-security` review box.
- P-28 (write tools off unless named, with an approval checkpoint) and P-31 (the rubric shape).
- 001 DEC-1: the open decisions, including the north star and the trace view above.
- 137 CH-17: a second person labels 20% of the gate set.
- P-30: the event schema versions on the agent card.

### 3. Needs a live stack

- Compose with a real LiteLLM router: the router boxes in 002 G-1, 003 G-1b, 004 G-2, 012 H-3, and 013 CH-2. Then 006 G-6 and 120 X-7, which read live router traffic.
- The kind cluster: 026 CH-4, 055 CH-6 (the T10 probe), 024 CH-3, and 139 CH-19.
- Live template runs: 016 H-9 (hot reload) and 059 H-15.
- The PoC-8 Compose stack: the fault and latency alerts of 117 X-3.
- The user's Mac run: the judge's numbers for 028 S-3 and 137 CH-17. Phases B and E cannot close without it ([what is left](../planning/poc/011-what-is-left.md), sections 1 to 3).
- A dedicated Linux host, the owner's: throughput at 4 pairs in 122 X-5.

## The order of work

Phases A and B can run in parallel. C needs B's route map. D needs C only for the demo. E can start after A. F is last of the template's phases. G can start after A and needs E's alert rules for the budget alert and the ring gate.

| Phase | Left | Done when |
| ----- | ---- | --------- |
| A. Close the must-haves | The T10 probe (user-owned); throughput at 4 pairs (122 X-5); the AI-generated marking client guide ([023 C-5](../planning/issues/023-C-5-ai-generated-marking.md) box 4), due before the AI Act Art. 50(2) date, 2026-12-02 | every PoC-1 to PoC-5 flag has plain evidence; the client guide is published before 2026-12-02 |
| B. Models and the judge | The route map is built. Left: the gate on by default, async at 0.1 on the judge route, after the judge's numbers from the Mac run; the retry stage ([017 H-4](../planning/issues/017-H-4-harness-features.md)), only for errors LiteLLM cannot see ([ADR-013](../planning/adr/013-model-call-retries-belong-to-the-router.md)) | the judge's numbers are recorded and the defaults confirmed or changed by ADR-010's rule |
| C. LangChain first | LangGraph is the template default, and the ADRs are accepted. Left: two boxes of 058 CH-8, and the MCP stand-in until `langchain-mcp-adapters` supports `mcp` 2 (a test watches for it) | `agentctl new` defaults to LangGraph and passes the lane contract and the evals |
| D. LangSmith | The `otlphttp/langsmith` exporter in the Collector; `fake-langsmith`; the `langsmith` feedback adapter; LangSmith datasets against the 20 golden cases; the two stack tests | one trace, one score, and one eval run visible in LangSmith, with the key only in the Collector and the chassis |
| E. Measure and show | SLOs and alerts ([117 X-3](../planning/issues/117-X-3-slos-alerts.md)); the container dashboard; a guard-stage span; sampling in the Collector; the OWASP agentic map; the weekly record | the north star, inputs, and counters are on one dashboard with one week of real numbers |
| F. Template GA | The chart in the generated repo and a published platform pin ([059 H-15](../planning/issues/059-H-15-agent-factory-cli.md), 025 H-10); a per-case scriptable fake model; a trial by a person new to the code | a person goes from `agentctl new` to a demo in under a day, recorded |
| G. Under load and on rollout | Backpressure and the model proxy under failure are done. Left: the rings ([056 CH-7](../planning/issues/056-CH-7-chassis-release-rings.md)) and the schema versions on the card (P-30) | an overload is a 503 a client can retry; a rollout drains with no failed request; a bad chassis tag stops at the canary |

### The issues in each phase

`docs/planning/tools/product_phases.py` holds the same list, and `make planning-check` fails when they disagree. A done issue stays in its phase, so its work counts. The number after each issue is its open boxes on `main`, 2026-11-10. `make progress PHASE=A` lists one phase.

| Phase | Issues |
| ----- | ------ |
| A | [007 H-1](../planning/issues/007-H-1-harness-library-ports-envelope.md) 2, [009 CH-1](../planning/issues/009-CH-1-engine-connectors-a2a.md) 2, [010 H-12](../planning/issues/010-H-12-config-loader.md) 5, [018 H-18](../planning/issues/018-H-18-idempotency.md) 1, [023 C-5](../planning/issues/023-C-5-ai-generated-marking.md) 4, [024 CH-3](../planning/issues/024-CH-3-helm-library-chart.md) 6, [026 CH-4](../planning/issues/026-CH-4-chassis-only-credentials-egress.md) 5, [055 CH-6](../planning/issues/055-CH-6-remote-lane-trust-rule.md) 2, [122 X-5](../planning/issues/122-X-5-load-tests.md) 4, [134 CH-14](../planning/issues/134-CH-14-cloud-security-defaults.md) done |
| B | [002 G-1](../planning/issues/002-G-1-litellm-router.md) 5, [003 G-1b](../planning/issues/003-G-1b-named-model-routes.md) 6, [012 H-3](../planning/issues/012-H-3-model-port.md) 6, [013 CH-2](../planning/issues/013-CH-2-outbound-model-proxy.md) 3, [017 H-4](../planning/issues/017-H-4-harness-features.md) 3, [028 S-3](../planning/issues/028-S-3-simplifier-metrics.md) 6, [037 M-2](../planning/issues/037-M-2-eval-gate-ci.md) 3, [042 A-2](../planning/issues/042-A-2-evaluator-gate-fallback.md) 7; second step: [137 CH-17](../planning/issues/137-CH-17-judge-calibration.md) 5, [138 CH-18](../planning/issues/138-CH-18-judge-per-agent.md) 5 |
| C | [058 CH-8](../planning/issues/058-CH-8-framework-event-mappings.md) 2, [128 CH-9](../planning/issues/128-CH-9-langgraph-checkpointer-interrupts.md) done |
| D | [129 CH-10](../planning/issues/129-CH-10-langsmith-trace-backend.md) 5, [130 CH-11](../planning/issues/130-CH-11-langsmith-feedback-adapter.md) 4, [131 CH-12](../planning/issues/131-CH-12-golden-cases-datasets.md) 6 |
| E | [001 DEC-1](../planning/issues/001-DEC-1-resolve-open-decisions.md) 8, [004 G-2](../planning/issues/004-G-2-token-cost-counting.md) 6, [006 G-6](../planning/issues/006-G-6-savings-dashboard.md) 4, [014 H-7](../planning/issues/014-H-7-observability.md) 4, [022 H-6](../planning/issues/022-H-6-security-middleware.md) 9, [046 A-5](../planning/issues/046-A-5-agent-dashboard.md) 6, [054 H-16](../planning/issues/054-H-16-tool-port.md) 9, [117 X-3](../planning/issues/117-X-3-slos-alerts.md) 9, [120 X-7](../planning/issues/120-X-7-cost-dashboard.md) 4, [132 CH-13](../planning/issues/132-CH-13-owasp-agentic-map.md) 4 |
| F | [015 H-8](../planning/issues/015-H-8-testing-kit.md) 6, [016 H-9](../planning/issues/016-H-9-local-debug-profile.md) 2, [025 H-10](../planning/issues/025-H-10-template-repo.md) 5, [057 H-19](../planning/issues/057-H-19-stateless-check-ci.md) 5, [059 H-15](../planning/issues/059-H-15-agent-factory-cli.md) 9, [139 CH-19](../planning/issues/139-CH-19-reference-stack.md) 5 |
| G | [056 CH-7](../planning/issues/056-CH-7-chassis-release-rings.md) 8, [135 CH-15](../planning/issues/135-CH-15-backpressure.md) done, [136 CH-16](../planning/issues/136-CH-16-model-proxy-failures.md) done |

The critical path to the whole plan, from `make progress`: 002 G-1 → 003 G-1b → 012 H-3 → 015 H-8 → 025 H-10 → 057 H-19 → 059 H-15.

## Gaps the PoC track did not cover

The product's gaps that are still open, beyond the per-PoC flags in [what is left](../planning/poc/011-what-is-left.md). P1 blocks a first production agent; P2 blocks the metrics; P3 is next.

| # | Gap | Phase |
| - | --- | ----- |
| P1 | The judge is off in every profile; the gate is off; no real judge numbers | B |
| P1 | LangSmith is in no code or config; Langfuse is the only `FeedbackPort` backend | D |
| P1 | LangGraph's MCP client is a stand-in until `langchain-mcp-adapters` supports `mcp` 2 | C |
| P2 | No SLOs, no alerts, no container dashboard, no weekly record | E |
| P2 | No guard-stage span; sampling deferred; the OTel SDK's cost unmeasured | E |
| P2 | The OWASP agentic threat list and the vault's threat model are not mapped to the controls | E |
| P2 | The retry stage on a model error is not built | B |
| P2 | The template's fake model cannot be scripted per case; the platform pin is a local symlink; no trial by a person | F |
| P3 | Caller API keys, rate limits with auth off, the URL allow-list default (ADR-008 open questions) | the user |
| P3 | Feedback: subject binding, a reviewer role, comment redaction (ADR-009 open questions) | the user |
| P3 | HPA on CPU only; the `remote` lane has one replica and no HPA | later ([104 O-3](../planning/issues/104-O-3-agent-pools-keda.md)) |
| P3 | The public A2A endpoint, the first cloud, and the broker are open decisions | [001 DEC-1](../planning/issues/001-DEC-1-resolve-open-decisions.md) |
| P3 | Orchestration of many agents (the epic's figure 3) | after the MVP |

## If phases A to F are all built: what is still missing

The gap review of 2026-11-07 (three read-only passes over `main` at `4aa4eb1`). Its verdict: phases A to F make the container complete for one stateless agent behind one chassis. They do not make it a platform a team can run under load, roll out safely, compose, or trust to judge its own answers. Most of its gaps are now issues: 134 CH-14 to 139 CH-19, with ADR-012 and ADR-013. The rows below are the ones still open. Code paths are under `packages/chassis/src/chassis/` unless named.

**Security:**

| Gap | Smallest change | Owner |
| --- | --------------- | ----- |
| The LiteLLM key never expires | `duration` on the key and a `seed.sh rekey` | 026 CH-4 |
| The remote token is one symmetric key for both directions | two keys in the workload token Secret | 055 CH-6 |
| Image signatures are not verified at admission | a `verifyImages` admission rule once the `sign` job is required | 056 CH-7 |
| MCP tool descriptions refresh every 60 s with no pin, so a rug pull goes unseen | hash and diff tool descriptions, alert on change | 054 H-16 |
| A write tool is "write" only by the absence of `readOnlyHint`, and no person approves a write with an outside effect (the vault's Rule of Two, phase 3, not met) | write tools off unless named in the key's `mcp_tool_permissions`; an ADR for an approval checkpoint | P-28, open |

**Under load and on rollout:**

| Gap | Smallest change | Owner |
| --- | --------------- | ----- |
| One bad image still reaches every service | three values overlays (canary, 10%, all) as sync waves, the X-3 error-rate alert as the promotion gate | 056 CH-7 |
| The "previous major accepted" rule has no second version to test | a card extension `chassis.event_schema_versions` read at `setup`, a test with a fake `"1"` | P-30, open |

**The judge (phase B, second step; blocks "on by default"):**

| Gap | Smallest change | Owner |
| --- | --------------- | ----- |
| No calibration against people: 10 `fail` cases against the vault's 30-fail floor, one author, no second label | 20 to 40 more `fail` cases, a second person labels 20%, kappa recorded; TPR and TNR at or above 0.85 (suggested) before `sync` gates anywhere | 137 CH-17 |
| The rubric returns a 0 to 1 score, score before reason, and a bad reply fails open | `judge-facts-v2` with `{reasoning, verdict}`; `unknown` and `bad_reply` count as a failed attempt in sync (ADR-010 open question 1) | P-31, open |
| One rubric for every agent and task, no code check first | `spec.harness.rubric`; skip the judge when the answer is structured | 138 CH-18 |
| In async mode the judge's cost reaches no metric | count `model_cost_microusd{route=judge}` and tokens from the score | 042 A-2 |
| "Escapes" needs a per-run join; "first-attempt pass rate" reads 100% in async | the gate score through `FeedbackPort`, named after the rubric version | 138 CH-18 |
| One evaluator, no drift check (075 E-3, ADR-010 item 10) | `spec.harness.shadow_evaluator` and `shadow_sample_rate` | 138 CH-18 |
| The golden set is author-written, not error analysis on real inputs | after the first Mac run, add cases per failure type with `source` | 137 CH-17 |
| The template's `make eval` has no judge and one plumbing case | render `evals/cases.yaml` in the `eval_kit` schema once the fake model scripts per case | phase F |

**Composition and entry points (after the MVP, but they decide the shape now):**

| Gap | Smallest change |
| --- | --------------- |
| No agent-to-agent path: the chassis is an A2A client only, so the epic's orchestrator (figure 3) and the public A2A endpoint have no counterpart | a new ADR: agents call agents as MCP tools through the gateway, complete-only, each callee on its own budget |
| Every run is one HTTP connection capped at 120 s; `spec.events.consume` is refused | build `consume` on `EventPort` (the 019 H-17 subscribe path) as the async entry |
| Multi-tenancy: the end user's id is unchecked (ADR-009 question 4) | an ADR on the end-user header and a per-subject daily token cap in `StatePort` |
| Streamed answers skip the `output` guard (a strict xfail) | LiteLLM's after-call guardrail, gated on the Mac run in phase E |

LangGraph state between runs is decided in [ADR-014](../planning/adr/014-langgraph-state-between-runs.md).

**Operating it (no phase owns these):**

- Runbooks for an operator: rotate a key, cut one service off and prove it, roll a chassis tag back, read one trace end to end, act on a gate fallback spike, a kill-switch drill (the vault's control 10). Only PoC-5 has runbooks.
- The epic's six success metrics: three (token spend, fallback rate, run recovery) belong to the parked SLM track and the orchestrator. [metrics](../specs/metrics.md) should say so in one line each.

The reference stack is 139 CH-19, and cost per task is a box in 120 X-7.

## What is reused

Product code, as is: `packages/chassis`, `contract-suites`, `workload-a2a`, `agent-testkit`, `agentctl`, `code-runner`, `guardrail-server`, `deploy/helm/agent-lib`, `deploy/dashboards`, `evals/deepeval` and the golden sets in `eval-kit`. Still to change: `profiles.py` (the judge on in `cloud`), `chassis/dashboards` (the container dashboard), `otel-collector.yaml` (the LangSmith exporter), `agentctl` (the chart and the published pin). Test-only, kept: the fake servers, `poc-harness`, `load-tools`, the Compose and kind stacks. Parked: `EncoderEvaluator`, the vLLM adapter, the router SLM, PoC-6c, `evals/promptfoo`, Dapr, and the `remote`-only engines as a first choice.

Related: [the specs](../specs/README.md), [the PoC track](../planning/poc/000-plan.md), [the backlog plan](../planning/issues/000-plan.md).
