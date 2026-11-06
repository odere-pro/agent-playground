# The agent container: one product from nine PoCs

Status: in progress
Date: 2026-11-06

## What this is

One definition of the product the PoC track built: the agent container. It merges PoC-1 to PoC-9 into one thing to ship. It names what is fixed, what is still open, the metrics that show whether it works, what is reused, and the gaps. The decisions behind it are [ADR-001](../planning/adr/001-chassis-delivery-model.md) to [ADR-010](../planning/adr/010-evals-offline-tool-online-gate.md). This doc changes no ADR. Where a change is needed, it says so and names the ADR.

Decisions from the user, 2026-11-06:

- PoC-1 to PoC-5 are must-haves. The architecture and the system design stay as built.
- The SLM track is parked: PoC-6c, the vLLM adapter (012 H-3), the router SLM (127 L-3). The model stays swappable, from a config map that says which model to use per task.
- A cheap judge model is on by default, so that routine judgments cost little and stay accurate. The user confirmed on 2026-11-06 that "JEV model" means the judge-evaluator model: the model behind `EvaluatorPort`.
- Security and observability (PoC-7 and PoC-8) are must-haves. The container must measure them and show them.
- Key metrics are defined: a north star, input metrics, and counter metrics.
- LangChain, LangGraph, and LangSmith come first among frameworks and tools.
- Amended on 2026-11-06, by the user: ADR-006 (LangGraph is the default engine), ADR-007 item 10 (LangSmith as a Collector backend), ADR-009 item 3a (a `langsmith` feedback adapter), and a note in ADR-010 (LangSmith datasets as a candidate store).

Words from the vault, used here: *fixed controls* and *model-based controls*, the four guards (*input*, *tool*, *output*, *budget*), *golden data set*, *model judge*, and the metric recipe "a north star, two input metrics, and one metric to measure failure". Notes: `wiki/security/Agent security strategy.md`, `wiki/patterns/Guardrails.md`, `wiki/evals/Eval strategy.md`, `notes/SDLC factory/01 - SDLC factory.md`.

## Open questions for the user

1. **The north star.** Section 6 proposes "good tasks per week". The alternative is "agents in production on the chassis". Pick one.
2. **Which trace view is primary per environment**, LangSmith or Langfuse, and where the score goes when both run (ADR-007 open question 4, ADR-009 open question 5).

Settled on 2026-11-06: LangSmith behind the Collector (ADR-007 item 10, ADR-009 item 3a) and LangGraph as the default engine (ADR-006 Decision 1).

## 1. The product in one paragraph

The agent container is one generic image, the chassis, that runs in front of every agent. It owns the public port. It maps native, OpenAI, Anthropic, MCP, A2A, and CloudEvents calls to one request. It runs one pipeline on every request: auth, rate limits, guardrails, idempotency, config, budgets, the evaluator gate, AI marking, events, and telemetry. The agent's own code plugs in behind it over A2A, in one of three lanes. Only the chassis holds credentials. A new agent is scaffolded with `agentctl new`, tested offline with `agent-testkit`, and deployed with the `agent-lib` Helm chart. The chassis ships as one image, rolled out in rings, with no agent rebuild.

## 2. What is fixed: PoC-1 to PoC-5

These rules are the product. A change to any of them is an ADR.

| Rule | From | Where it lives | Evidence |
| ---- | ---- | -------------- | -------- |
| A core behind ports (hexagonal). `chassis.core` has no network, no SDK, no web framework. One `Protocol` per dependency in `chassis.ports`. One in-memory fake per port. The fake and every real adapter pass the same contract suite. Adapters are picked by `spec.adapters`, never in code | PoC-1, ADR-001 | `packages/chassis/src/chassis/{core,ports,fakes,adapters}`, `packages/contract-suites`, `profiles.py` (`fake`, `local`, `cloud`) | `make lint` enforces the import rules; `make test` runs offline with no keys |
| Eleven ports: model, engine, config, telemetry, tool, state, events, auth, guardrail, evaluator, feedback | PoC-1 to PoC-8 | `chassis/ports/`, `profiles.REGISTRY` | the contract suites, one per port |
| One `handle(input, ctx)` contract, served over A2A in every lane. Events in one versioned JSON schema: `start`, `delta`, `tool_call`, `metrics`, `end`, `error` | PoC-2, ADR-001 item 2 | `chassis/core`, `packages/workload-a2a`, `packages/chassis/schemas/` | four workloads in four styles pass the same suite (PoC-2) |
| Three lanes, picked by `spec.engine.connector`: `sidecar` (default), `remote`, `inprocess` (tests only). The trust rule and `spec.trust` decide the lane; an admission check enforces it | ADR-001 items 4 and 5 | `chassis/adapters/a2a`, `a2a/remote`, `deploy/kind/poc05/admission/` | PoC-5 |
| One front door: native `/v1/run`, OpenAI, Anthropic, MCP, A2A, CloudEvents. A base URL change is all a client needs | PoC-3, ADR-003 | `chassis/server/interfaces/` | PoC-3 |
| Stateless replicas. Idempotency and leases in `StatePort`. Config from the store, reloaded with no restart, bad config refused. `/ready` and drain. A broker client behind `EventPort`, not Dapr | PoC-4, ADR-004 | `chassis/server/{idempotency,config_loader,readiness,lifecycle}.py`, `adapters/valkey`, `adapters/kafka` | kill drill: 3,284 sent, 3,284 ok, 24 of 24 retried keys replayed |
| Hard requirement 1: only the chassis holds credentials. The workload has no key, no service account token, no route to the metadata service or the API server | ADR-001 | the Helm chart `agent-lib` (secrets to the chassis only, credential-looking names refused in the workload), `remote_auth.py` | PoC-5 criterion 3, flagged for MinIO only |
| Hard requirement 2: every lane is tested on every commit | ADR-001 | `chassis_contracts.lane`, `agent-testkit` | PoC-2, PoC-9 |
| The `remote` lane: gVisor, default-deny egress, remote auth (ADR-005), a code-execution tool with one sandbox per call | PoC-5, ADR-005 | `deploy/kind/poc05/`, `packages/code-runner` | PoC-5, criteria 1 and 8 flagged |
| Every model and tool call goes through the chassis proxies, keyed to its request by `traceparent`. Budgets are per call. The gateway allow-list and the scoped LiteLLM key are the hard limits | ADR-001 item 6 | `chassis/server/{model_proxy,model_admission}.py`, `adapters/mcp/gateway.py` | PoC-2 and PoC-5 |
| One image, one tag, set once in the Helm library chart; ring rollouts; a service can pin the previous tag; `latest` is refused | ADR-001 item 9 | `deploy/helm/agent-lib` | PoC-9 |

What PoC-1 to PoC-5 left open, and the product must close:

- Throughput at 4 pairs is unproven on the Mac. Rerun on Linux or pinned CPUs: [122 X-5](../planning/issues/122-X-5-load-tests.md).
- The hostile kind suites are in `run.sh test-remote`, but no CI run has shown them. Hard requirement 1 and the tool gateway suites run by hand (PoC-5 criterion 1).
- No chassis holds a MinIO key, so the allowed path through the chassis is not shown for MinIO (PoC-5 criterion 3).
- The in-pod probe workload is a recorded exception (PoC-5 criterion 8).
- Default-deny egress as a product rule is owned by [026 CH-4](../planning/issues/026-CH-4-chassis-only-credentials-egress.md), and ADR-007 item 9 lists it as not done.
- The admission rules live in `deploy/kind/poc05/admission/`, not in the chart.

## 3. Models: swappable, a map per task, a judge model

**Today.** The model is set per agent: `spec.model.route` and `spec.model.fallback_route` (`chassis/server/config.py`, `ModelSpec`). The pipeline copies the route into `ctx.model_route`, and the workload sends it as the OpenAI `model` field. The route can change with no restart. LiteLLM resolves the route to a provider model; the service's virtual key lists the routes it may use, and any other route is refused with 403 `model_route_denied`. The only override is the evaluator gate's fallback, which forces every model call of the last attempt to the fallback route. There is no per-request and no per-task choice. No LiteLLM `router_settings` or `fallbacks` exist in any config.

**What the product adds: a route map per task.** suggested: the shape.

```yaml
spec:
  model:
    route: big-default            # kept: the default for every task
    fallback_route: small-default
    routes:                       # new: which route per task
      simplify: small-default
      lookup: big-default
      classify: decision-small
    judge_route: decision-small   # new: the judge model, on by default
```

- A task is named by the caller in `input.data.task` (suggested) or by the agent's declared operation. `context_for()` resolves `routes[task]`, else `route`, into `ctx.model_route`. The workload keeps sending `ctx.model_route`; no workload code changes.
- `model_admission.py` already forces a route per attempt. It enforces the resolved route the same way, so a workload cannot pick a route the map does not give it.
- Every route in the map must be in the service's LiteLLM key, or `agentctl` and the config schema refuse the config at load.
- The map is reloadable, like `route` today.
- Swapping a provider is a LiteLLM config change plus a key update. No agent changes. This is what "swappable" means here.

**The judge model.** One cheap route, `decision-small`, serves every routine judgment. The judge-evaluator behind `EvaluatorPort` is the first and main use:

- The evaluator gate (`GatedEngine`): `JudgeEvaluator` uses it as the judge route (today `CHASSIS_JUDGE_ROUTE`, default `judge`).
- Classification tasks in the map (`classify`, routing of a task to a workflow when [108 O-12](../planning/issues/108-O-12-routing-planner-modes.md) lands).
- A sampled drift check against the big model, as 075 E-3 describes.

Defaults the product sets, and why:

- `evaluator_gate: true`, `evaluator_mode: async`, `evaluator_sample_rate: 0.1`, `evaluator: judge` on `decision-small`. ADR-010 shows a sync judge at rate 1.0 costs about 2.6 times the answer on a small judge, and $855 per million requests on a large one. Async at 0.1 keeps the signal and costs $5 per million. Sync mode stays for agents that opt in, with the retry-then-fallback path.
- Fail open on an evaluator outage, as built. The gate is not a security boundary; the vault's rule is "No prompt, classifier or judge is a security boundary".
- The encoder evaluator (`EncoderEvaluator`, TEI) stays in the code but off, with the SLM track.

What must be measured before these defaults are final: judge latency, cost, and agreement with people on the 50-case gate set. Both wait on the Mac run (PoC-7). ADR-010's trigger stays: async becomes the default anyway if a sync judge adds more than 50% to p95.

## 4. Security: defined and implemented

The boundary is made of fixed controls in shared services. The chassis stages are model-based controls and hygiene, and they only lower the odds (ADR-008, option S3; the vault's "Assume the model will be tricked").

| Control | Kind | Where | Status |
| ------- | ---- | ----- | ------ |
| One scoped LiteLLM virtual key per service: models, budget, rate limit, tag | fixed | `seed.sh`, `stack.sh`; `max_budget 1.0/day`, `rpm 600`, suggested | built |
| MCP gateway allow-list per key; write tools need an `idempotency_key`; `tool_denied` | fixed | `adapters/mcp/gateway.py` | built (PoC-5) |
| Default-deny NetworkPolicy, ingress and egress, per pod; no `ipBlock`; the remote workload reaches only the chassis proxy, with no DNS | fixed | `deploy/helm/agent-lib/templates/_networkpolicy.tpl` | built in the chart; CH-4 as a product rule not done |
| gVisor for `remote`; uid 10001/10002, read-only root, no capabilities, seccomp, no service account token | fixed | the chart, `deploy/kind/poc05` | built |
| Admission: `untrusted` must use `remote`; minimum chassis version | fixed | `deploy/kind/poc05/admission/` | built for kind; not in the chart |
| Identity: OIDC JWT against JWKS; scopes `agent:run`, `agent:feedback`, `agent:read`; 401, 403 `insufficient_scope`, 503 `auth_unavailable` | fixed | `chassis/server/security.py`, `adapters/oidc` | built; `cloud` refuses the `static` fake |
| Rate limit per caller, token bucket in `StatePort` | budget guard | `chassis/server/rate_limit.py` | built; behavior with auth off is an open question |
| Per-call model budget: `max_tokens` capped at what the run has left; uncorrelated calls capped per minute | budget guard | `model_proxy.py`, `model_admission.py` | built |
| Input, tool-output, and output guard stages through LiteLLM's guardrail API: Presidio for PII, Prompt Guard 2 for injection; `on_unavailable: fail_closed` | input, tool, output guards (model-based) | `chassis/server/guard.py`, `adapters/litellm/guardrails.py`, `packages/guardrail-server` | built; Prompt Guard 2 is a regex stand-in offline (Hugging Face answers 403) |
| Stream secret guard; output URL allow-list, default deny | output guard | `stream_guard.py`, `output_filter.py` | built |
| AI marking: `X-AI-Generated`, `X-AI-Disclosure-Required`, the `ai_marking` body field, the `aigenerated` CloudEvent extension | hygiene | `adapters/ai_marking.py` | built |
| Regex PII redaction in the chassis and the Collector | hygiene | `chassis/logs.py`, `otel-collector.yaml` | built |
| Supply chain: images pinned by tag, never `latest`; CI actions pinned by SHA; SBOM, provenance, keyless cosign in the template's CI; Snyk Agent Scan in a sandbox | fixed | the template, `deploy/agent-scan/` | built; no real Snyk run (`SNYK_TOKEN`) |
| Red-team set, 18 cases | test | `packages/bakeoff` redteam, `evals/` | 14/18 on `litellm`, 13/18 on `keyword` |

Open, in order of risk:

1. CH-4 default-deny egress as a product rule, and the admission rules in the chart.
2. The four red-team misses: spaced-out and French injections, base64 tool output, a write tool that receives a secret.
3. The hostile suites in a CI run that has passed.
4. `user.id` as a keyed hash; API keys for callers; rate limits with auth off; the URL allow-list default; disclosure for `minimal` agents (the ADR-008 user questions).
5. Prompt Guard 2 on real weights, and a real Snyk scan.

The vault's threat list, OWASP ASI01 to ASI10, is not yet mapped to these controls. Section 9 lists it as a gap.

## 5. Observability: defined and implemented

**The trace model (ADR-007).** The chassis and the workload send OTLP to one Collector. The Collector redacts and fans out to Langfuse v4 and a second backend (Jaeger in PoC-7, Tempo planned). The run continues the caller's `traceparent`. The connector span is the parent of the workload's spans. The workload runs the OpenInference instrumentor for its framework. LiteLLM adds its own span under the model call. Baggage carries `user.id` and `session.id`; an inbound `baggage` header is dropped.

**Spans the chassis emits.** `chassis.run`, `chassis.engine.run`, `chassis.model.call`, `chassis.model.route`, `chassis.tool.call`, `chassis.evaluator.score` (with `evaluator.score`, `passed`, `threshold`, `latency_ms`), `chassis.feedback`, `chassis.idempotent_replay`. Any span can carry `error.code`. There is no guard-stage span yet; guards emit counters and logs only.

**Metrics.** `chassis/metrics.py`, every sample labeled `agent`:

- `chassis_requests_total{interface}`, `chassis_run_duration_seconds{interface,status}`, `chassis_run_errors_total{interface,code}`
- `chassis_model_calls_total{route}`, `chassis_model_call_duration_seconds{route,status}`, `chassis_model_tokens_total{route,direction}`, `chassis_model_cost_microusd_total{route}`
- `chassis_tool_calls_total{tool}`, `chassis_tool_call_duration_seconds{tool,status}`
- `chassis_gate_runs_total{mode}`, `chassis_gate_retries_total`, `chassis_gate_fallbacks_total`, `chassis_evaluator_score_duration_seconds{mode,status}`
- `chassis_feedback_total{label}`
- counters for every refusal and failure: `guardrail_blocked{stage,reason}`, `guardrail_unavailable`, `auth_refused`, `rate_limited`, `output_url_masked`, `evaluator_calls{evaluator,mode,outcome}`, `gate_failed_open`, `model_calls_uncorrelated`, `model_route_forced`, `model_upstream_denied`, `config.rejected`, the `idempotency.*` family, and more

**Logs.** JSON lines with `request_id` and `trace_id` on both sides (`chassis/logs.py`, `workload_a2a.logs`).

**Dashboards.** One per agent, seven panels, written by `python -m chassis.dashboards` to `deploy/dashboards/`: requests by interface and status, latency p50 and p95, errors by code, tokens by direction, model cost per hour, gate fallback rate, feedback by label.

**Feedback (ADR-009).** `POST /v1/feedback` with a score in [0, 1], a label, and a `feedback_id`; the trace is found from `request_id` in `StatePort`; written to Langfuse scores; idempotent; optional `agents.feedback.received.v1` event.

Open:

1. SLOs and burn-rate alerts: [117 X-3](../planning/issues/117-X-3-slos-alerts.md) (availability, p95, error rate, gate fallback rate below 10%).
2. A guard-stage span (ADR-007 item 2).
3. Sampling, head or tail, is deferred. The OTel SDK's image, RSS, and latency cost are not measured.
4. `/metrics` is open by default.
5. Named-entity masking in the Collector is not built; the Collector redacts by regex.
6. Langfuse datasets are tested only against a stub; nobody has looked at the user and session view in the Langfuse UI.
7. Feedback: bind the record to the subject, a reviewer role, redact the stored comment.

## 6. Key metrics: north star, input, counter

The vault's recipe: "define at least four metrics for your product: a north star, two input metrics, and one metric to measure failure." Every metric below is computed from what the chassis already emits. Nothing here needs a new instrument except the two marked *new*.

**North star: good tasks per week.** A good task is one run that ends `ok`, within its budget, and that the evaluator passed when it was scored. It counts value the container delivered, not traffic. It rises only when more agents run on the chassis, they answer well, and the controls do not get in the way.

- Formula: runs with `status="ok"` in `chassis_run_duration_seconds`, minus runs with `evaluator_calls{outcome="fail"}`, per week, summed over agents.
- Alternative, if adoption matters more than output right now: agents in production on the chassis, from the manifests registered in the service registry. *new*: the registry count.

**Input metrics.** The levers that move the north star. The first two are the primary pair.

| Metric | Formula | Lever | Target on record |
| ------ | ------- | ----- | ---------------- |
| Agents live on the chassis | distinct `agent` labels with traffic in the week | adoption | none yet |
| First-attempt pass rate | 1 − (`chassis_gate_retries_total` + `chassis_gate_fallbacks_total`) / `chassis_gate_runs_total` | prompt and model quality, the route map | none yet |
| Task success rate | `ok` runs / `chassis_requests_total`, per agent and interface | reliability | X-3 error-rate SLO |
| p95 run latency, and the chassis's share of it | `chassis_run_duration_seconds` p95; the stages' overhead | the pipeline's cost | stage budget 25 ms p50, 50 ms p95; measured +1.0 / +0.8 ms (PoC-8) |
| Time to first demo | hours from `agentctl new` to a passing demo, by someone new | the template | under a day (PoC-9); stand-in 8 min |
| Chassis release lead time | hours from a chassis tag to the last ring | the update path | none yet |

**Counter metrics.** What must not get worse while the north star rises. The first one is the vault's "metric to measure failure".

| Metric | Formula | Why it counters | Target on record |
| ------ | ------- | --------------- | ---------------- |
| Escapes: passed the gate, then bad feedback | runs with `evaluator_calls{outcome="pass"}` and later `chassis_feedback_total{label="bad"}` (or score below 0.5), per week | a cheap judge can pass bad answers | none; the vault's rule: if online scores fall more than 10 points under offline, review the golden set |
| Cost per good task | `chassis_model_cost_microusd_total` (every route, judge included) / good tasks | the gate and the route map must not buy quality with money | G-5 break-even; judge at 0.1 is $5 per million |
| Guardrail false blocks | blocks on golden inputs / golden inputs; plus `guardrail_unavailable` rate | more blocking is not more safety | ADR-008: below 1% false positives; revisit if a stage adds more than 150 ms p95 |
| Secret or credential leaks | stream secret guard hits that reached a client; red-team misses | hard requirement 1 | zero; red team 18/18 (now 14/18) |
| Gate fallback rate and fail-open rate | `chassis_gate_fallbacks_total` / sync gate runs; `gate_failed_open` / gate runs | the gate must not become the answer path | X-3: below 10% |
| Sidecar cost per replica | CPU and RSS per chassis container, from the load run | one image must stay thin | ADR-001: 0.05–0.1 vCPU, 128–256 MiB; measured 0.105–0.129 vCPU at 10 RPS, 150–182 MiB |
| Rollbacks per chassis release | releases pinned back by any service / releases | ring rollouts must stay safe | none yet |

**How the container shows them.** *new*: one container dashboard next to the per-agent ones, with three rows: north star, inputs, counters. `python -m chassis.dashboards` writes it to `deploy/dashboards/container.json`. The weekly numbers go to one note per week in `notes/`, by the `record-measurement` skill, so a number is never claimed without its run.

## 7. The template: how a new agent is built (PoC-9)

The path, as built:

1. `make setup` in the platform checkout, once.
2. `uv run agentctl new <name> --kind transformer|evaluator|tool --framework <fw> --connector sidecar|remote`. The trust rule is enforced: smolagents and claude-agent must use `remote`. Copier renders `packages/agentctl/src/agentctl/template/` into a git repo on `main`, one commit, `uv.lock` pinned to the platform.
3. `make setup test`. Edit `handle.py`, `evals/cases.yaml`, the tests. Then `make lint test load-smoke hostile redteam eval image`.
4. `make run` serves A2A on loopback; `docker compose --profile fake up` runs the pair with the fake model server.
5. Deploy with the `agent-lib` Helm chart. The generated repo has no chart yet ([059 H-15](../planning/issues/059-H-15-agent-factory-cli.md)).

What the rendered repo holds: `handle.py` plus the framework's `mapping.py`, `tools.py`, `telemetry.py` (byte copies of the echo workloads, with a drift test); `config/agent.yaml` and `config/fake.yaml`; contract tests through `agent-testkit` (the chassis runs in the `fake` profile as its own process, the workload cannot import `chassis`, Unix sockets only); eval, load-smoke, hostile, and red-team suites; a hardened Dockerfile; Compose with a debug profile; CI for GitHub and GitLab with SHA-pinned actions, SBOM, provenance, and keyless signing.

The product keeps the path and changes three things: the default framework becomes LangGraph (section 10), the chart lands in the repo (H-15), and the platform is pinned to a published package, not a `.platform` symlink to a local checkout.

## 8. What is reused

| Reused as is | Reused with a change | Stays test-only | Parked |
| ------------ | -------------------- | --------------- | ------ |
| `packages/chassis` (234 files): the core, 11 ports, fakes, adapters, server | `profiles.py`: `cloud` turns the gate and the judge on by default | `packages/fake-model-server`, `fake-mcp-server`, `fake-langfuse`, `poc-harness` | `EncoderEvaluator`, the vLLM adapter (H-3), the router SLM (L-3), PoC-6c |
| `packages/contract-suites`: one suite per port, inbound, interface, lane | `chassis/server/config.py`: `ModelSpec.routes`, `judge_route` | `packages/bakeoff`, except `evalgate`, `evalrun`, `redteam`, and the golden set, which CI uses | kagent-adk, smolagents, Claude Agent SDK as engines: supported in `remote`, not first |
| `packages/workload-a2a`, `agent-testkit`, `agentctl`, `code-runner`, `guardrail-server` | `agentctl`: default framework, chart in the template, published pins | `deploy/compose/*`, `deploy/kind/*`: the PoC stacks | Dapr (ADR-004) |
| `deploy/helm/agent-lib`, `deploy/dashboards`, `evals/deepeval`, the golden sets | `chassis/dashboards`: the container dashboard; `otel-collector.yaml`: a LangSmith exporter | | promptfoo (`evals/promptfoo/`) |
| `packages/workloads/echo-langgraph`: the template source for LangGraph | ADR-006, ADR-007, ADR-009: amendments | | |

## 9. Gaps, in priority order

What the nine PoCs did not cover, or left flagged, and the product needs. P1 blocks a first production agent. P2 blocks the metrics in section 6. P3 is next.

| # | Gap | Why it matters | Owner or issue |
| - | --- | -------------- | -------------- |
| P1 | No per-task model map. One route per agent, no per-request or per-task choice | the user's config map does not exist yet | section 3; new issue |
| P1 | The judge model is off by default: every profile has `evaluator: none`; the gate is off; no real judge numbers | "on by default" is not true today | section 3; the Mac run |
| P1 | Default-deny egress is not a product rule (CH-4); admission rules are not in the chart | hard requirement 1 holds by convention, not by the chart | 026 CH-4, H-15 |
| P1 | The hostile suites have no passing CI run; hard requirement 1 and the tool gateway run by hand | the boundary is not gated on every commit | PoC-5 criterion 1 |
| P1 | LangSmith is nowhere; the Langfuse adapter is the only `FeedbackPort` backend | the user's priority tool is not supported | section 10; ADR-007 and ADR-009 amendments |
| P1 | LangGraph is a supported engine, not the default; its MCP client is a stand-in until `langchain-mcp-adapters` imports against `mcp` 2; the instrumentor's LangGraph coverage is from docs, not a test | the user's priority framework is second | section 10; ADR-006 amendment |
| P2 | No SLOs or alerts; no container dashboard; no weekly record | the metrics cannot be shown | 117 X-3, section 6 |
| P2 | No guard-stage span; sampling deferred; OTel SDK cost unmeasured; `/metrics` open by default | observability has holes it cannot see | ADR-007 open list |
| P2 | Red team 14/18; Prompt Guard 2 on a regex stand-in; no Snyk run | the model-based controls are weaker than planned | ADR-008 open list |
| P2 | The OWASP agentic threat list (ASI01–ASI10) and the vault's threat model are not mapped to the controls | no way to say which threats are covered | new note, `platform-security` |
| P2 | Throughput at 4 pairs unproven; load tests on a Linux host | "scales by adding replicas" is shown in part | 122 X-5 |
| P2 | The retry stage on a model error (H-4) is not built; only the gate retries | a 5xx from the provider is a failed task | 017 H-4 |
| P2 | The template's fake model cannot be scripted per case, so `make eval` cannot test model-based logic offline; no trial by a person; the platform pin is a local symlink | the "under a day" claim rests on an AI stand-in | PoC-9 criterion 6, 059 H-15 |
| P3 | A LangGraph agent keeps no state (no checkpointer); human-in-the-loop interrupts have no place in the `handle` contract (one answer or one stream) | the two LangGraph features teams ask for first | `chassis-architect`; 125 L-1 |
| P3 | Caller API keys, `user.id` hashing, rate limits with auth off, the URL allow-list default | open product questions from ADR-008 | the user |
| P3 | Feedback: subject binding, a reviewer role, comment redaction | feedback can be spoofed or leak | ADR-009 open list |
| P3 | HPA on CPU only; the remote lane has one replica and no HPA; KEDA later | scaling the `remote` lane is manual | 104 O-3 |
| P3 | The public A2A endpoint, the first cloud, and the broker are still open decisions | deploy target unknown | 001 DEC-1 |
| P3 | Orchestration of many agents (the epic's figure 3) | out of the MVP; the chassis does not yet front an orchestrator | later waves |

## 10. LangChain and LangSmith first

**LangGraph as the default engine.** ADR-006 is amended (2026-11-06): default `langgraph`; PydanticAI stays supported. The code still defaults to `pydanticai` until phase C lands. The numbers from the PoC-6a scorecard support it: 10/10 on every task in every lane, 224 lines of mapping code (8% less than plain Python), sidecar p50 45 ms on smoke, 252 MB image, 133 MB idle memory, all five event types with no change to the schema, `traceparent` on every model and MCP call, the chassis proxy as the only model URL, `max_retries=0` because the chassis owns retries. The template already has `_frameworks/langgraph/`.

Work, in order:

1. Replace the MCP stand-in with `langchain-mcp-adapters` once it imports against `mcp` 2, or pin `mcp` to what it supports and record the choice. Until then the stand-in stays, with a test that fails when the adapter is ready.
2. A test that the OpenInference LangChain instrumentor covers LangGraph spans, not a doc claim.
3. Add a `langchain` variant for simple agents (`create_agent` on LangChain 1.x, which runs on LangGraph) next to the `StateGraph` variant.
4. Decide the checkpointer: none (stateless, as today), or a `StatePort` checkpointer in Valkey with the lease the idempotency path already uses. `chassis-architect` owns this.
5. Decide interrupts: out of scope for the MVP, or a `resume` operation on the contract. This is an ADR.
6. Churn: 37 releases a year. Renovate opens the bump; the contract suite and the drift test are the gate.

**LangSmith as a backend, with the workload kept clean.** The vault's LangChain note keeps `LANGSMITH_TRACING` off in the agent and sends traces through the tracer port. The product keeps that rule. LangSmith sits where Langfuse sits today: behind the Collector, with the key in the Collector, never in the workload. Hard requirement 1 holds.

1. Tracing: an `otlphttp/langsmith` exporter in `otel-collector.yaml`, next to `otlphttp/langfuse`. suggested: endpoint `https://api.smith.langchain.com/otel`, headers `x-api-key` and `Langsmith-Project`; check the LangSmith docs before pinning. The redaction processors run before both exporters. `fake-langsmith`, a test-only fake of the routes the Collector and the chassis use, follows `fake-langfuse`.
2. Feedback: a `langsmith` adapter for `FeedbackPort`, next to `LangfuseFeedback`. It needs the run id that LangSmith gave the OTel trace; the lookup by `request_id` in `StatePort` stays, and the stored value gains the LangSmith run id. Same contract suite.
3. Evals: LangSmith datasets and experiments run the golden data set next to DeepEval. DeepEval stays the CI pick (ADR-010) until LangSmith gives the same verdicts on the 20 golden cases on two engines, the test ADR-010 used for promptfoo. The release gate stays where the vault puts it: "at release, never at merge", and "a judge reports and a person decides".
4. ADR-007 (item 10) and ADR-009 (item 3a) are amended as of 2026-11-06. Langfuse stays supported; the `cloud` profile picks one by `spec.adapters.feedback` and the Collector config. Which is primary per environment is open (ADR-007 open question 4).

## 11. Order of work

suggested: sizes.

| Phase | What | Done when | Size |
| ----- | ---- | --------- | ---- |
| A. Close the must-haves | CH-4 egress rule and admission in the chart; the hostile suites green in CI; a MinIO key in a chassis; X-5 on Linux | every PoC-1 to PoC-5 flag has plain evidence | 2 weeks |
| B. Models and the judge model | `ModelSpec.routes` and `judge_route`; the gate on by default, async at 0.1 on `decision-small`; judge numbers from the Mac run; H-4 retry | a config map swaps a model per task with no agent change; the judge's cost, latency, and agreement are recorded | 2 weeks |
| C. LangChain first | ADR-006 amendment; MCP adapters; instrumentor test; the `langchain` variant; the checkpointer and interrupt decisions | `agentctl new` defaults to LangGraph and passes the lane contract and the evals | 2 weeks |
| D. LangSmith | ADR-007 and ADR-009 amendments; the Collector exporter; the feedback adapter; the eval comparison | one trace, one score, and one eval run visible in LangSmith, with the key only in the Collector | 1–2 weeks |
| E. Measure and show | X-3 SLOs and alerts; the container dashboard; the guard-stage span; sampling; the OWASP mapping; the weekly record | the north star, inputs, and counters are on one dashboard, with one week of real numbers | 2 weeks |
| F. Template GA | the chart in the repo (H-15); a published platform pin; a per-case scriptable fake model; a trial by a person | a person new to the code goes from `agentctl new` to a demo in under a day, recorded | 1–2 weeks |

Phases A and B can run in parallel. C needs B's route map. D needs C's default engine only for the demo. E can start after A. F is last.

Related: [the PoC track](../planning/poc/000-plan.md), [the backlog plan](../planning/issues/000-plan.md), [ADR-001](../planning/adr/001-chassis-delivery-model.md), [ADR-006](../planning/adr/006-agent-engines-default-supported-lanes.md), [ADR-007](../planning/adr/007-observability-otel-collector-langfuse.md), [ADR-008](../planning/adr/008-security-identity-guardrails-defense-in-depth.md), [ADR-009](../planning/adr/009-feedback-langfuse-scores-event.md), [ADR-010](../planning/adr/010-evals-offline-tool-online-gate.md), [PoC-9](../../pocs/poc-09-agent-mvp-template/README.md).
