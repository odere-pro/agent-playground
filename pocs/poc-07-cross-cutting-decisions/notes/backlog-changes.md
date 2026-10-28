# Backlog changes when PoC-7 closes

Status: **draft, 2026-10-28.** Not applied. Apply with the skill `planning-sync` when PoC-7 closes and the user accepts the ADRs. Each change becomes a "Status after PoC-7" bullet at the end of the issue's `## What`. A short "Proposed in PoC-7" bullet with the ADR links is already in 022, 014, 017, 037, 023, 042, and 081.

Evidence: [ADR-007](../../../docs/planning/adr/007-observability-otel-collector-langfuse.md), [ADR-008](../../../docs/planning/adr/008-security-identity-guardrails-defense-in-depth.md), [ADR-009](../../../docs/planning/adr/009-feedback-langfuse-scores-event.md), [ADR-010](../../../docs/planning/adr/010-evals-offline-tool-online-gate.md), [contract v6](../../../docs/contracts/contract-v6.md). EVIDENCE: the dated notes in this folder (trace join, red team, eval trial, CI stack run).

No order, size, or dependency change is proposed yet, so `backlog.py` `ROWS` stays as it is. If one is needed, it is listed under "ROWS" below and applied by the orchestrator, not here.

## The epic's event catalog (recorded here, because the epic is never edited)

Add one row to the epic's D.3 event catalog:

| Event type | Producer | Main consumers |
| ---------- | -------- | -------------- |
| `agents.feedback.received.v1` | Any agent's chassis (`POST /v1/feedback`) | Recorder (040 D-0), golden set agent (064 D-1), audit log (072 C-3) |

Payload `feedback.v0.json` (contract v6, part C). The schema is registered by 089 R-15. EVIDENCE: the schema file and its test.

## Per issue

### 022 H-6 Security middleware

- Caller auth is `OidcAuth` behind `AuthPort`; scopes per route from `spec.security` (`run_scope`, `feedback_scope`, `read_scope`); codes 401 `unauthenticated`, 403 `insufficient_scope`, 503 `auth_unavailable`. EVIDENCE: suite binding test path, route tests.
- Content guardrails in LiteLLM for every key (Presidio hook; Prompt Guard 2 through the generic guardrail API and `guardrail-server`, 512-token chunks). Gap (d) settled for now: per route, not per key. EVIDENCE: LiteLLM config, `guardrail-server` tests.
- Chassis guard stages `input`, `tool_output`, `output` as defense in depth; `on_unavailable` fail closed. EVIDENCE: stage tests; p50 and p95 per stage on the CI stack.
- Red-team set: EVIDENCE: pass rate per case and per layer, owners of misses.
- Chassis RSS with regex redaction on, against ADR-001: EVIDENCE.
- Fix the generated `## Reuse` text (in `docs/planning/tools/reuse_map.py`, `H-6`): it still says "The Presidio library in the chassis for redaction before logging and publishing", which ADR-001 and ADR-008 rule out. suggested: "Regex redaction in the chassis; Presidio in LiteLLM and the Collector".
- Still open, carried: API keys for callers; `/metrics` scope; rate limits in Valkey (PoC-8).

### 014 H-7 Observability

- `OtelTelemetry`, run span on the request's W3C trace id, Collector fan-out to Langfuse and Jaeger (swap by exporter config). EVIDENCE: `poc07-stack.yml` run id; trace-join test names in `inprocess` and `sidecar`.
- Baggage `user.id` and `session.id` over A2A; caller baggage dropped. EVIDENCE: lane test.
- Per-engine telemetry list (contract v6, F.3). EVIDENCE: the two PoC-7 engines; anything that had to live in an event mapping (expected: nothing).
- Open, carried: named-entity redaction in the Collector log pipeline; head or tail sampling; whether a `remote` workload's spans are accepted.

### 017 H-4 Harness features

- `GatedEngine`: sync (score, retry, fallback via `ctx.model_route`, `end.status: fallback`) and async (score after; every stream). Sample rate by request id hash. Judge tokens as a `metrics` event; chassis numbers `metrics.attempt`. EVIDENCE: gate tests on two engines, both transports.
- Open concern on gate cost: answered by ADR-010 with EVIDENCE: the judge and encoder numbers from the trial. Record the decision on defaults (`sync`/1.0 today against the suggested `async`/0.1 for a judge).
- Open, carried: no-fallback-route behavior (`evaluator_gate_failed`); proxy enforcement of the run's route.
- The epic's `harness.evaluator`, `timeout_ms`, `budget` names against `spec.harness` (contract v6, config table).

### 037 M-2 Eval gate in CI

- The tool picked for CI: EVIDENCE: TBD by the trial (promptfoo or DeepEval), with the 20-case reports for two engines and the same report format.
- Langfuse datasets and experiments as the case and result store. EVIDENCE: dataset name, experiment runs.

### 023 C-5 AI-generated marking

- Headers `X-AI-Generated` and `X-AI-Disclosure-Required` on every public interface, complete and streaming; `spec.governance` with the load-time refusal. EVIDENCE: test per interface and mode.
- Still open: body field for OpenAI and Anthropic formats; flag on result events (suggested: CloudEvent extension `aigenerated`); `Access-Control-Expose-Headers`; legal on disclosure for `minimal` with marking on.

### 042 A-2 Evaluator gate and fallback

- The gate shape is decided (ADR-010). Open here: keep the `evaluator-facts` agent behind `EvaluatorPort` (needs an A2A adapter) or use `judge`.

### 081 R-8 Description scanning

- Snyk Agent Scan in CI in a sandbox, on our MCP servers. EVIDENCE: workflow job name and first run.

### Others touched (status bullets suggested)

- **064 D-1, 040 D-0, 072 C-3**: may consume `agents.feedback.received.v1`. 064 D-1's Langfuse decision reopens ADR-009 if it goes another way.
- **089 R-15**: register `feedback.v0.json`.
- **026 CH-4**: the workload's egress to the Collector's OTLP port is already in its allow-list; add the guardrail-server and the encoder service to the chassis's egress (not the workload's). OPEN: a remote's path to the Collector.
- **075 E-3, 073 E-1, 074 E-2, 031 G-5**: the encoder adapter and the gate cost formula (ADR-010 item 9) are where their numbers plug in. EVIDENCE: encoder agreement when a model is available.
- **013 CH-2**: OPEN: the model proxy refusing or rewriting a call whose `model` is not the run's route (fallback safety).

## ROWS

None proposed. EVIDENCE: confirm at close that no size changes (022 H-6 may shrink, since guardrails are LiteLLM config; 017 H-4 may grow by the async mode).
