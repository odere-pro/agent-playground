# The agent container: the plan

Status: in progress
Date: 2026-11-06

## What this is

The one plan to ship the agent container: what is decided, what is built in which order, and what is still open. It merges PoC-1 to PoC-9 into one product. What the container *is* lives in [the specs](../specs/README.md), one fact in one place. Why each rule exists lives in the ADRs. What each PoC proved, and what it left open, lives in the PoC READMEs and in [what is left](../planning/poc/011-what-is-left.md). This plan repeats none of that; it links it.

## Decisions

From the user, 2026-11-06:

- PoC-1 to PoC-5 are must-haves. The architecture stays as built: [architecture](../specs/architecture.md).
- The SLM track is parked (PoC-6c, 127 L-3, and the vLLM half of [012 H-3](../planning/issues/012-H-3-model-port.md)). The other half of 012 H-3, the route map per task, is live in phase B. The model stays swappable from a route map per task: [models](../specs/models.md).
- A cheap judge model is on by default, behind `EvaluatorPort`: [models](../specs/models.md), "The judge model".
- Security and observability are must-haves, and the container measures and shows them: [security](../specs/security.md), [observability](../specs/observability.md), [metrics](../specs/metrics.md).
- LangChain, LangGraph, and LangSmith come first. Amended the same day: [ADR-006](../planning/adr/006-agent-engines-default-supported-lanes.md) (LangGraph is the default engine), [ADR-007](../planning/adr/007-observability-otel-collector-langfuse.md) item 10 (LangSmith behind the Collector), [ADR-009](../planning/adr/009-feedback-langfuse-scores-event.md) item 3a (a `langsmith` feedback adapter), and a note in [ADR-010](../planning/adr/010-evals-offline-tool-online-gate.md) (LangSmith datasets as a candidate store).

- P-1: an evaluator outage fails open everywhere, as built (ADR-010 item 4). No code change. An alert on `gate_failed_open / gate_runs` goes into [117 X-3](../planning/issues/117-X-3-slos-alerts.md). The same applies to P-2, a rate-limit store outage: fail open, as built. The gap review's `fail_closed` for the `cloud` rate limit (below) reopens P-2 for `cloud` only. It is proposal P-26, not yet decided.
- P-6: the drain box of [011 H-2](../planning/issues/011-H-2-inbound-adapters.md) is rewritten to the contract (`/ready` goes 503 first, in-flight runs finish, new calls are still served with `Connection: close`), and 011 H-2 is retired to wave `WD`. The container order on rollout is a deploy rule; the gap review below finds the chart breaks it, and P-29 asks for an ADR.

The sources for P-1, P-2, and P-6 are "Decided by the user, 2026-11-06" in the [backlog review proposals](../reviews/2026-11-06-backlog-review/proposals.md).

Open, for the user:

1. **The north star.** "Good tasks per week", or "agents in production on the chassis" ([metrics](../specs/metrics.md)).
2. **The primary trace view per environment**, LangSmith or Langfuse, and where the score goes when both run (ADR-007 open question 4, ADR-009 open question 5).

## The order of work

suggested: the sizes. Phases A and B can run in parallel. C needs B's route map. D needs C only for the demo. E can start after A. F is last of the template's phases. G, added on 2026-11-07, can start after A and needs E's alert rules for the budget alert and the ring gate.

| Phase | What | Done when | Size |
| ----- | ---- | --------- | ---- |
| A. Close the must-haves | Done 2026-11-06 (PR #79): the sidecar hostile suite in CI (PoC-5 criterion 1, run 42299094368), default-deny egress as a chart rule ([026 CH-4](../planning/issues/026-CH-4-chassis-only-credentials-egress.md)), and the admission rules in `agent-lib` (`agent-admission` installs them once per cluster). Left, from [what is left](../planning/poc/011-what-is-left.md#4-engineering-work): MinIO through the chassis, the T10 probe (user-owned), throughput at 4 pairs on Linux (122 X-5), warn-first for a new minimum chassis version; the AI-generated marking client guide ([023 C-5](../planning/issues/023-C-5-ai-generated-marking.md) box 4), added on 2026-11-06 by the user, due before the AI Act Art. 50(2) date, 2026-12-02 | every PoC-1 to PoC-5 flag has plain evidence; the client guide is published before 2026-12-02 | 1 week left |
| B. Models and the judge | Done 2026-11-06 (PR #78): `ModelSpec.routes` and `judge_route`, resolved per task and enforced by the proxy. Left: the gate on by default, async at 0.1 on the judge route, after the judge's cost, latency, and agreement from the Mac run ([what is left](../planning/poc/011-what-is-left.md#1-runs-on-the-users-mac)); the retry stage on a model error ([017 H-4](../planning/issues/017-H-4-harness-features.md)) | the judge's numbers are recorded and the defaults confirmed or changed by ADR-010's rule | 1 week left |
| C. LangChain first | LangGraph as the template default (step 1, done: merged to `main` on 2026-11-06 in PR #71); the MCP stand-in replaced by `langchain-mcp-adapters` on `mcp` 2, or a test that fails when it is ready; a trace-join test that shows LangGraph's graph steps under the run span; a `langchain` `create_agent` variant; the checkpointer and interrupt decisions (an ADR each, `chassis-architect`) | `agentctl new` defaults to LangGraph and passes the lane contract and the evals; the two ADR-006 Revisit triggers are closed | 2 weeks |
| D. LangSmith | The `otlphttp/langsmith` exporter in the Collector; `fake-langsmith`; the `langsmith` feedback adapter; LangSmith datasets against the 20 golden cases (ADR-010's trial rule); the two stack tests | one trace, one score, and one eval run visible in LangSmith, with the key only in the Collector and the chassis | 1–2 weeks |
| E. Measure and show | SLOs and alerts ([117 X-3](../planning/issues/117-X-3-slos-alerts.md)); the container dashboard; a guard-stage span; sampling in the Collector; the OWASP agentic threat list (ASI01–ASI10) mapped to the controls; the weekly record | the north star, inputs, and counters are on one dashboard with one week of real numbers | 2 weeks |
| F. Template GA | The chart in the generated repo and a published platform pin ([059 H-15](../planning/issues/059-H-15-agent-factory-cli.md), 025 H-10); a per-case scriptable fake model; a trial by a person new to the code | a person goes from `agentctl new` to a demo in under a day, recorded | 1–2 weeks |
| G. Under load and on rollout | Added 2026-11-07 from the gap review below, agreed by the user. Backpressure ([135 CH-15](../planning/issues/135-CH-15-backpressure.md)); the model proxy under failure: the budget refusal code, a per-call timeout, LiteLLM retries and fallbacks per route ([136 CH-16](../planning/issues/136-CH-16-model-proxy-failures.md)); the rings ([056 CH-7](../planning/issues/056-CH-7-chassis-release-rings.md)); the drain order and the schema versions on the card (P-29, P-30 in the [backlog review proposals](../reviews/2026-11-06-backlog-review/proposals.md)) | an overload is a 503 a client can retry; a rollout drains with no failed request; a bad chassis tag stops at the canary | suggested: 2 weeks |

### The issues in each phase

From [the phase map](../reviews/2026-11-06-backlog-review/phase-map.md) (2026-11-06, refreshed 2026-11-07). Days use the `sync.py` sizes: S 1, M 2.5, L 5. The map's days are the backlog sizes set before PRs #78 and #79 landed, so A and B overstate what is left.

| Phase | Issues | Days |
| ----- | ------ | ---- |
| A | [007 H-1](../planning/issues/007-H-1-harness-library-ports-envelope.md), [009 CH-1](../planning/issues/009-CH-1-engine-connectors-a2a.md), [010 H-12](../planning/issues/010-H-12-config-loader.md), [018 H-18](../planning/issues/018-H-18-idempotency.md), [023 C-5](../planning/issues/023-C-5-ai-generated-marking.md), [024 CH-3](../planning/issues/024-CH-3-helm-library-chart.md), [026 CH-4](../planning/issues/026-CH-4-chassis-only-credentials-egress.md), [055 CH-6](../planning/issues/055-CH-6-remote-lane-trust-rule.md), [122 X-5](../planning/issues/122-X-5-load-tests.md); [134 CH-14](../planning/issues/134-CH-14-cloud-security-defaults.md), the `cloud` security defaults (P0, added after the map); and [056 CH-7](../planning/issues/056-CH-7-chassis-release-rings.md), warn-first for a new minimum chassis version (moved from G on 2026-11-08; its rings stay G work) | 21.5 + 2.5 + 5 |
| B | [002 G-1](../planning/issues/002-G-1-litellm-router.md), [003 G-1b](../planning/issues/003-G-1b-named-model-routes.md), [012 H-3](../planning/issues/012-H-3-model-port.md), [013 CH-2](../planning/issues/013-CH-2-outbound-model-proxy.md), [017 H-4](../planning/issues/017-H-4-harness-features.md), [028 S-3](../planning/issues/028-S-3-simplifier-metrics.md), [037 M-2](../planning/issues/037-M-2-eval-gate-ci.md), [042 A-2](../planning/issues/042-A-2-evaluator-gate-fallback.md); and, as its second step, [137 CH-17](../planning/issues/137-CH-17-judge-calibration.md) (calibration) and [138 CH-18](../planning/issues/138-CH-18-judge-per-agent.md) (the judge per agent) | 19.5 + 5 |
| C | [058 CH-8](../planning/issues/058-CH-8-framework-event-mappings.md), [128 CH-9](../planning/issues/128-CH-9-langgraph-checkpointer-interrupts.md) (the checkpointer and interrupt ADRs) | 6 |
| D | [129 CH-10](../planning/issues/129-CH-10-langsmith-trace-backend.md), [130 CH-11](../planning/issues/130-CH-11-langsmith-feedback-adapter.md), [131 CH-12](../planning/issues/131-CH-12-golden-cases-datasets.md) | 4.5 |
| E | [001 DEC-1](../planning/issues/001-DEC-1-resolve-open-decisions.md), [004 G-2](../planning/issues/004-G-2-token-cost-counting.md), [006 G-6](../planning/issues/006-G-6-savings-dashboard.md), [014 H-7](../planning/issues/014-H-7-observability.md), [022 H-6](../planning/issues/022-H-6-security-middleware.md), [046 A-5](../planning/issues/046-A-5-agent-dashboard.md), [054 H-16](../planning/issues/054-H-16-tool-port.md), [117 X-3](../planning/issues/117-X-3-slos-alerts.md), [120 X-7](../planning/issues/120-X-7-cost-dashboard.md), [132 CH-13](../planning/issues/132-CH-13-owasp-agentic-map.md) (the OWASP agentic map) | 21.5 |
| F | [015 H-8](../planning/issues/015-H-8-testing-kit.md), [016 H-9](../planning/issues/016-H-9-local-debug-profile.md), [025 H-10](../planning/issues/025-H-10-template-repo.md), [057 H-19](../planning/issues/057-H-19-stateless-check-ci.md), [059 H-15](../planning/issues/059-H-15-agent-factory-cli.md); and [139 CH-19](../planning/issues/139-CH-19-reference-stack.md), the reference stack (`make platform-up`, added after the map) | 13.5 + 5 |
| G | [135 CH-15](../planning/issues/135-CH-15-backpressure.md), [136 CH-16](../planning/issues/136-CH-16-model-proxy-failures.md); the rings of 056 CH-7 (the issue is counted in A); the drain order, [ADR-012](../planning/adr/012-drain-order-on-rollout.md) (P-29, option 1, decided 2026-11-08) | 5 |

**Why the plan's sizes are smaller than the days.** The phases A to F hold 37 issues and 86.5 days in the map, against about 9 weeks (suggested: 45 days) in the table above. Three reasons. First, phases A, B, and E hold more issues than the plan listed: A, B, and E are each about twice the plan's size. Second, the map's days are the backlog sizes, set before PRs #78 and #79 closed part of 012 H-3, 026 CH-4, and 042 A-2, so A and B overstate what is left until those issues are re-sized. Third, the phases overlap (A with B, E after A), so the calendar is shorter than the sum. The plan's weeks stay as the estimate of work left. The map's 86.5 days are the backlog's own count. Issues 134 to 139 are not in the 86.5: they add 17.5 days (134, 137, 138 and 139 to A, B, and F as above; 135 and 136 to G), so A to F plus G is about 104 days in the backlog's sizes. 056 CH-7 (L, 5 days), moved from G to A on 2026-11-08, was in neither count, so the total is now about 109 days.

The user's own items, Mac runs and repository settings, are in [what is left](../planning/poc/011-what-is-left.md), sections 1 to 3. The product cannot close phase B or E without the Mac run.

## Gaps the PoC track did not cover

These are the product's gaps, beyond the per-PoC flags in [what is left](../planning/poc/011-what-is-left.md). P1 blocks a first production agent; P2 blocks the metrics; P3 is next. Closed on 2026-11-06: the route map per task (PR #78) and default-deny egress with the admission rules in the chart (PR #79).

| # | Gap | Phase |
| - | --- | ----- |
| P1 | The judge is off in every profile; the gate is off; no real judge numbers | B |
| P1 | LangSmith is in no code or config; Langfuse is the only `FeedbackPort` backend | D |
| P1 | LangGraph's MCP client is a stand-in; its instrumentor coverage is a doc claim | C |
| P2 | No SLOs, no alerts, no container dashboard, no weekly record | E |
| P2 | No guard-stage span; sampling deferred; the OTel SDK's cost unmeasured; `/metrics` open by default | E |
| P2 | The OWASP agentic threat list and the vault's threat model are not mapped to the controls | E |
| P2 | The retry stage on a model error is not built; only the gate retries | B |
| P2 | The template's fake model cannot be scripted per case; the platform pin is a local symlink; no trial by a person | F |
| P3 | A LangGraph agent keeps no state (no checkpointer); interrupts have no place in the `handle` contract | C |
| P3 | Caller API keys, `user.id` hashing, rate limits with auth off, the URL allow-list default (ADR-008 open questions) | the user |
| P3 | Feedback: subject binding, a reviewer role, comment redaction (ADR-009 open questions) | the user |
| P3 | HPA on CPU only; the `remote` lane has one replica and no HPA | later ([104 O-3](../planning/issues/104-O-3-agent-pools-keda.md)) |
| P3 | The public A2A endpoint, the first cloud, and the broker are open decisions | [001 DEC-1](../planning/issues/001-DEC-1-resolve-open-decisions.md) |
| P3 | Orchestration of many agents (the epic's figure 3) | after the MVP |

## If phases A to F are all built: what is still missing

Reviewed on 2026-11-07 by three read-only passes over `main` at `4aa4eb1` (the chassis architect, platform security against the OWASP agentic list and the vault's threat model, the eval expert against the vault's eval rules). Verdict: phases A to F make the container complete for one stateless agent behind one chassis. They do not make it a platform a team can run under load, roll out safely, compose, or trust to judge its own answers. The gaps, ranked by what blocks a first production agent, each with the smallest change that closes it. Code paths are under `packages/chassis/src/chassis/` unless named.

**Security defaults a hijacked agent would use (fix first, one PR, mostly profile values):**

| Gap | Smallest change |
| --- | --------------- |
| The loopback model proxy serves uncorrelated calls, 20k tokens a minute per replica with no run (`server/model_admission.py`), so a hijacked sidecar spends the key without a run | `uncorrelated_tokens_per_minute: 0` in the `cloud` profile |
| A tool name not in the gateway's cached list skips the write-key and schema checks (`adapters/mcp/gateway.py`, `if definition is not None`) | raise `unknown_tool` after a refresh that still has no definition, before any request |
| `/metrics` is open by default and the NetworkPolicy admits any pod to it; the rate limit is `fail_open` and off under `auth: none` | in `cloud`: `metrics_scope: agent:read`, refuse `auth: none` as it refuses `static`; and `rate_limit_on_unavailable: fail_closed`, which conflicts with the user's P-2 (fail open everywhere, decided 2026-11-06) and waits on proposal P-26 in the [backlog review proposals](../reviews/2026-11-06-backlog-review/proposals.md) |
| `user.id` goes into baggage raw (`server/pipeline.py`), so the IdP subject reaches an untrusted pod | hash it with the `sha256[:32]` the rate limit already uses (ADR-007 open question 1) |
| The LiteLLM key never expires; the remote token is one symmetric key for both directions; image signatures are not verified at admission; MCP tool descriptions refresh every 60 s with no pin (a rug pull goes unseen) | `duration` on the key and a `seed.sh rekey`; two keys in the workload token Secret; a `verifyImages` admission rule once the `sign` job is required; hash and diff tool descriptions, alert on change |
| A write tool is "write" only by the absence of `readOnlyHint`, and no person approves a write with an outside effect: the vault's lethal trifecta meets in any agent loop whose key grants one (Rule of Two, phase 3, not met) | write tools off unless named in the key's `mcp_tool_permissions`; an ADR for an approval checkpoint on writes with outside effect |

**Runtime behavior under load and on rollout (a new phase G, suggested: 2 weeks):**

| Gap | Smallest change |
| --- | --------------- |
| No backpressure: nothing caps runs in flight per replica; the HPA is CPU-only while the chassis idles on CPU waiting for the model; overload shows as 504s | `spec.limits.max_in_flight` (suggested: 32), 503 `overloaded` with `Retry-After`, counter `requests_refused{reason="overloaded"}`, a gauge for 104 O-3 |
| The drain order is wrong on every rollout: the chart makes the chassis the native sidecar, so Kubernetes stops the workload first while the chassis still answers `/ready` 200 (`deploy/helm/agent-lib/templates/_deployment.tpl`; 024 CH-3 left the pick open) | [ADR-012](../planning/adr/012-drain-order-on-rollout.md) (accepted 2026-11-08, P-29 option 1): the chassis is the main container and the workload the native sidecar |
| LiteLLM's daily budget refusal reaches the workload as `http_400` with no counter and no alert | a fixed code `service_budget_exhausted` on both proxy routes, counter `model_budget_exhausted{route}`, an alert in phase E |
| The model proxy's upstream call has no per-call timeout: a cancelled run keeps paying for it (`adapters/litellm/client.py`) | pass the run's remaining time into `ModelPort.complete` and `.stream` |
| Rings are in no phase: CH-7 "not built: warn-first, the rings, and the promotion checks"; one bad image still reaches every service | three values overlays (canary, 10%, all) as sync waves, the X-3 error-rate alert as the promotion gate |
| The "previous major accepted" rule has no second version to test; a chassis ahead of a workload learns of a mismatch on the first run | a card extension `chassis.event_schema_versions` read at `setup`, a test with a fake `"1"` |
| The retry stage (017 H-4) is planned as a rerun of the whole agent; LiteLLM has no `num_retries` or `fallbacks` per route | set those per route first; build the stage only for errors LiteLLM cannot see |

**The judge (phase B, second step; blocks "on by default"):**

| Gap | Smallest change |
| --- | --------------- |
| No calibration against people: the gate set has 10 `fail` cases against the vault's 30-fail floor, one author, no second label; the vault bars an uncalibrated judge from blocking | add 20–40 `fail` cases to `eval-kit` `golden/cases-gate.yaml`, a second person labels 20% and kappa is recorded; TPR and TNR at or above 0.85 before `sync` gates anywhere; `async` only until then |
| The rubric returns a 0–1 score, score before reason, and a bad reply fails open; the vault wants pass, fail, or unknown, reasoning first, and a reply that does not parse is never a pass | `judge-facts-v2` with `{reasoning, verdict}`; `unknown` and `bad_reply` count as a failed attempt in sync (ADR-010 open question 1) |
| One module-constant rubric for every agent and task, no code check first, no agent kind in the chassis | `spec.harness.rubric` (a name, version pinned); skip the judge when the answer is structured (`end.output` without `text`) |
| The judge calls LiteLLM directly, so in async mode its cost reaches no metric; "cost per good task, judge included" is not computable at the target defaults | count `model_cost_microusd{route=judge}` and tokens from the score, `evaluator.cost_usd` on the span |
| "Escapes" needs a per-run join of two counters; "first-attempt pass rate" reads 100% in async, which never retries | write the gate score through `FeedbackPort` as a score named after the rubric version, so escapes are one query on the trace; the formula is `1 − evaluator_calls{outcome="fail"} / evaluator_calls{outcome in pass, fail}` |
| One evaluator, no drift check, no sampled second judge (075 E-3, ADR-010 item 10) | `spec.harness.shadow_evaluator` and `shadow_sample_rate`, agreement counted as `evaluator_agreement{pair, agree}` |
| The golden set is author-written, not error analysis on real inputs; phase D would add a second result store | after the first Mac run, add cases per failure type with `source`; the YAML stays the only source of cases, one result store per environment |
| The template's `make eval` has no judge and one plumbing case | render `evals/cases.yaml` in the `eval_kit` schema once the fake model scripts per case (phase F) |

**Composition and entry points (after the MVP, but they decide the shape now):**

| Gap | Smallest change |
| --- | --------------- |
| No agent-to-agent path: the chassis is an A2A client only, a workload may call only the model and tool proxies, so the epic's orchestrator (figure 3) and the public A2A endpoint have no counterpart | a new ADR (the number [ADR-011](../planning/adr/011-per-run-secret-and-v0-wire-bounds.md) is taken by the per-run secret): agents call agents as MCP tools through the gateway (the callee's `/v1/mcp` exists; the gateway holds the `agent:run` token, allow-listed per key), complete-only; the callee's budget is its own; `traceparent` passes the gateway |
| Every run is one HTTP connection capped at 120 s; `spec.events.consume` is refused, though the epic says every agent starts from an event, and result events and A2A task ids exist | build `consume` on `EventPort` (the 019 H-17 subscribe path) as the async entry; a run from an event answers through `agents.task.completed.v1` |
| `context_ref` is accepted and unused; a workload may hold no store credential, so a LangGraph checkpointer has nowhere to write | the phase C checkpointer ADR: the chassis exposes the store as MCP tools scoped by `session.id`, writes keyed by the run; LangGraph's saver sits over them in the template |
| Multi-tenancy: the subject is the app; the end user's id is unchecked (ADR-009 question 4); budgets and rate limits are per service and per subject only | an ADR on the end-user header and a per-subject daily token cap in `StatePort` |
| Streamed answers skip the `output` guard (a strict xfail; LiteLLM's after-call guardrail waits on the Mac run) | phase E carries that run as a gate, not a wish |

**Operating it (no phase owns these):**

- One reference stack. Each PoC has its own Compose or kind stack; nothing brings up LiteLLM, the gateway, Valkey, Kafka, the Collector, a trace backend, and the example agent together. suggested: `make platform-up` on kind from `deploy/helm`, with the example agent's demo as its smoke test, and the chart's default chassis image a published tag, not `kind.local/...:poc09`.
- Runbooks for an operator: rotate a key, cut one service off and prove it, roll a chassis tag back, read one trace end to end, act on a gate fallback spike, a kill-switch drill (the vault's control 10). Today only PoC-5 has runbooks.
- Cost per task: `chassis_model_cost_microusd_total` is labeled `route` and `agent`; with routes per task, a `task` label is what the route map's decisions need.
- The epic's six success metrics: three (token spend, fallback rate, run recovery) belong to the parked SLM track and the orchestrator; [metrics](../specs/metrics.md) should say so in one line each rather than replace them silently.

## What is reused

Product code, as is: `packages/chassis`, `contract-suites`, `workload-a2a`, `agent-testkit`, `agentctl`, `code-runner`, `guardrail-server`, `deploy/helm/agent-lib`, `deploy/dashboards`, `evals/deepeval` and the golden sets in `eval-kit`. Changed by this plan: `chassis/server/config.py` (the route map), `profiles.py` (the judge on in `cloud`), `chassis/dashboards` (the container dashboard), `otel-collector.yaml` (the LangSmith exporter), `agentctl` (the default, the chart, the published pin). Test-only, kept: the fake servers, `poc-harness`, `load-tools`, the Compose and kind stacks. Parked: `EncoderEvaluator`, the vLLM adapter, the router SLM, PoC-6c, `evals/promptfoo`, Dapr, and the `remote`-only engines as a first choice.

Related: [the specs](../specs/README.md), [the PoC track](../planning/poc/000-plan.md), [the backlog plan](../planning/issues/000-plan.md).
