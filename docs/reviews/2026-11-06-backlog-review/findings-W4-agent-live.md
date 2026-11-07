# Findings W4: Simplifier agent live (recorder, gate, result events, history swap, dashboard, shadow, canary, 4-bit)

Session: W4. Issues: 040–049. Waves: W4.
Rubric: [rubric.md](rubric.md).

## Summary

W4's goal was the first measured savings from the simplifier SLM. The PoC track built none of that: there is no recorder, no simplifier workload, no history swap, no shadow or canary, and no 4-bit variant (a search of `packages/`, `pocs/`, `deploy/`, and `evals/` finds no code for any of them). It built the generic parts under them: result events with retry and counters (043), the evaluator gate with retry and fallback (042), and a per-agent chassis dashboard (046). This review ticks 4 of 65 boxes and retires none. Six issues are `parked` (041, 044, 045, 047, 048, 049), 043 splits into a `later` half and a parked half, 040 is `later`, and 042 and 046 are live: 042 is phase B (the judge on by default, the outage rule) and 046 is phase E (the container dashboard). The top risk: 042, the phase B issue, depends on the parked 041 A-1 and on 028 S-3, and it carries the one open code conflict (the gate fails open on an evaluator outage, W4-01).

## Issues

| Issue | Boxes | Phase | Verdicts | Evidence or reason |
| ----- | ----- | ----- | -------- | ------------------ |
| 040 D-0 | 0/9 | later | tick-blocked, resize L, drift, deps, cite | No recorder, `StorePort`, Postgres adapter, outbox, or consumer exists. Serves no phase: the weekly record is a note, and the north star reads Prometheus ([metrics](../../specs/metrics.md)). It moves to E only if the user wants "good tasks per week" from events. Box 7 has the port half only (`packages/chassis/tests/test_events_contract.py::TestInMemoryBus::test_a_failing_handler_gets_the_event_again`); box 3 has the publish-side redaction only, with no names (W4-08, W4-09, W4-10) |
| 041 A-1 | 0/6 | parked | parked, drift, deps | The `simplifier-slm` route, guided decoding on vLLM, and the S-4 set (≥ 300 records) are the SLM track. The generic cross-interface and versions behavior is ticked elsewhere (`packages/contract-suites` bindings, W2a). The template's `python` handle already rewrites text on `big-default`, so a demo agent exists |
| 042 A-2 | 3/7 | B | tick, conflict, resize M, deps, drift, cite, tick-blocked | Gate, one retry, and fallback tests (`packages/chassis/tests/test_evaluator_gate.py`). Open: 4 (`allowed_agents` not built; the judge is an adapter, not an agent), 5 (fails open, W4-01), 6 (no held-out set), 7 (judge cost is not in `/metrics`, W4-04). Phase B needs five things from it (W4-02). Depends on parked 041 and 028 (W4-03) |
| 043 A-3 | 0/6 | later, parked | split, tick-blocked, drift, deps | Result events are built and tested (`test_result_events.py`), but no box ticks: each names the simplifier, the recorder, or the savings dashboard. Split into 043a (chassis half: one event per call on every interface, a real event carrying its extensions, the `source` name) and 043b (simplifier half, parked) (W4-06) |
| 044 G-4 | 0/8 | parked | parked, drift | The history swap exists to save big-model tokens with simplified past answers from the SLM recorder. No code, no ADR, no spec (W4-13). The product plan has no such lever |
| 045 A-4 | 0/5 | parked | parked | Switches 044 on for a pilot agent; nothing exists to switch on |
| 046 A-5 | 1/6 | E | tick, tick-blocked, resize M, deps, cite | The generator is `packages/chassis/src/chassis/dashboards/__init__.py`; the committed JSON is checked by `test_dashboards.py::test_the_generated_files_are_up_to_date`. It has no first-try pass rate, retry rate, tokens saved, target lines, version filters, or links. Phase E's container dashboard has no other issue (W4-05) |
| 047 M-3 | 0/7 | parked | parked, deps | Runs a candidate SLM next to the current one; its report goes to MLflow (032 M-1, parked by W3; no MLflow outside `docs/planning`) and it needs a trained candidate and the simplifier (041). `grep shadow` finds nothing in code. The gate's async scoring, sample hash, and span attributes could be reused if a shadow route returns as `later` |
| 048 M-4 | 0/6 | parked | parked, deps | Canary by LiteLLM weights and a rollback for an SLM version; no weights, no `models.model.promoted.v1`, no code. The chassis-release rolling drills (PoC-4, PoC-9) are a different thing (W4-14) |
| 049 M-5 | 0/5 | parked | parked | 4-bit GGUF and AWQ variants of the SLM. PoC-6c ran only `Qwen3-1.7B-Q8_0.gguf`; no 4-bit variant, no MLflow, no `simplifier-slm-q4` route |

Parked boxes are counted in the denominator. If the user agrees, the six parked issues leave the live total: 041 M, 044 M, 045 S, 047 M, 048 S, 049 M = 12 days, plus the parked half of 043 (S).

## Ticks

Ten test ids ran offline in this session: `scripts/check_offline.sh` on `test_evaluator_gate.py` (3 ids), `test_dashboards.py`, `test_metrics.py`, `test_result_events.py` (3 ids), `test_events_contract.py::TestInMemoryBus`, and `test_server.py`; result `11 passed in 4.82s` (one id is parametrized). The 042 ticks hold once the boxes are reworded as W4-07 says: `threshold` in place of `facts_kept ≥ 0.95`, and the configured `fallback_route` in place of `big-default`.

- `042 A-2`, box "An answer with `facts_kept` ≥ 0.95 returns `status: ok`": `· Delivered in PoC-7: packages/chassis/tests/test_evaluator_gate.py::test_a_passing_answer_goes_out_once_with_the_judge_metrics (status ok, one attempt, one model call; the threshold in the test is 0.7, so the box is reworded to the configured threshold)`
- `042 A-2`, box "A fixture that drops a fact triggers exactly one retry": `· Delivered in PoC-7, PoC-8: packages/chassis/tests/test_evaluator_gate.py::test_a_failing_answer_retries_once_then_falls_back, pocs/poc-07-cross-cutting-decisions/tests/test_poc07_evals_gate.py::test_poc07_evals_gate_retries_once_then_falls_back, pocs/poc-08-cross-cutting-build/tests/test_poc08_c6_gate.py::test_the_gate_retries_then_falls_back_to_the_configured_route (every lane; the fallback is the configured fallback_route, end status fallback)`
- `042 A-2`, box "No call makes more than one retry": `· Delivered in PoC-7: packages/chassis/tests/test_evaluator_gate.py::test_a_failing_answer_retries_once_then_falls_back (retries: 1 gives two scored attempts, then the unscored fallback; GATE_RETRIES == 1)`
- `046 A-5`, box "The dashboard is provisioned from code, not built by hand": `· Delivered in PoC-8: packages/chassis/tests/test_dashboards.py::test_the_generated_files_are_up_to_date, pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_static.py::test_grafana_provisions_every_file_of_deploy_dashboards`

Tick-blocked (named so the finding can say what would prove them):

- `046 A-5` box 1 (every panel, in Compose and on Kubernetes): the stack tests `pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_metrics.py::test_every_panel_query_has_data` and `::test_grafana_has_the_agent_dashboard_with_every_panel` are `network`-marked and wait on run 41011346864 (`pocs/poc-08-cross-cutting-build/notes/backlog-changes.md:31`). The panels differ from the box's list. No Kubernetes path exists.
- `043 A-3` box 1 (one event per call on every API): `test_result_events.py::test_a_completed_run_publishes_one_valid_cloudevent` covers the native interface only. A test that counts events on the OpenAI and Anthropic interfaces would prove it.
- `043 A-3` box 3 (a repeat leaves one record): the replay half is `test_result_events.py::test_a_replayed_idempotent_call_publishes_nothing_new`; the record half needs 040.
- `043 A-3` box 4 (no credential in the workload): the chassis-level proof is the 019 H-17 tick (`pocs/poc-05-sandboxed/tests/test_poc05_kafka_static.py::test_the_secret_reaches_only_the_chassis_container_of_the_events_agent`); this box names the simplifier, which does not exist.
- `040 D-0` box 3 (PII fixture redacted): `packages/chassis/tests/test_result_events_redaction.py::test_a_completed_runs_event_holds_no_pii` uses an email and a phone number. A test with a name fixture would prove the box, and the regex cannot pass it.
- `040 D-0` box 7 (ack after store): a recorder handler that stores, with `EventPortContract` redelivery on its failure, would prove it.

## Status bullets folded

No issue in 040–049 has a "Status after PoC-N" bullet. The PoC-6c "to apply" list, the PoC-9 `2026-10-30-w2-w5-report.md`, and sections 3 and 5 of [011-what-is-left.md](../../planning/poc/011-what-is-left.md) name none of these issues.

- `042 A-2`, "Proposed in PoC-7" (the last `## What` bullet): fold to "Decided in ADR-010 (accepted)"; drop "the user accepts it at the PoC-7 pull request". The open part, "keep the `evaluator-facts` agent behind `EvaluatorPort`", is settled by what was built: `judge` and `encoder` are in-chassis adapters, and there is no A2A evaluator adapter. Drift (W4-07).
- `042 A-2`, the "Open concern (review 2026-09-29)" paragraph in `## Why`: out of scope. ADR-010 items 4 to 10 settled it: sample rate by request id, async mode, the judge's tokens as a `metrics` event, and the recommended defaults. The numbers wait on the Mac run (011, section 1).
- PoC-7 draft `backlog-changes.md`, the 042 A-2 part (one line, never applied): tick the gate shape as decided; the open question (keep the agent or use `judge`) is closed as above; the outage question is DEC-1 (ADR-010 open question 1). Its 017 H-4 part (the outage fails open, the judge-token gap, no timeout shrink across attempts) is W2b's.
- PoC-7 draft, the `040 D-0` line ("may consume `agents.feedback.received.v1`"): a note in 040 `## Out of scope`; no phase needs it, and the consumer is `later`.
- PoC-7 draft, the 075 E-3 line ("the encoder adapter ... where their numbers plug in"): not W4's; 075 E-3 depends on 042, 046, and parked 047 (W6's call; the product plan parks `EncoderEvaluator`).

## Findings

### W4-01 [HIGH] [rule 11] packages/chassis/src/chassis/server/evaluator_gate.py
Problem: an evaluator outage fails open in sync mode (`evaluator_gate.py:326-330`: `except EvaluatorError` counts `chassis.gate_failed_open`, emits the attempt as it is with end status `ok`, and returns, on the retry attempt too) and in async mode and for streams (`evaluator_gate.py:244`, `contextlib.suppress`); 042 A-2 box 5 says "With the evaluator stopped, calls fall back and never return an unchecked SLM answer" (`packages/chassis/tests/test_evaluator_gate.py::test_an_evaluator_outage_fails_open`, `::test_gate_sends_the_retry_answer_and_fails_open_when_the_evaluator_fails_on_it`, run in this session).
Why it matters: phase B turns the gate on by default, so an outage of the cheap judge would send every answer out unchecked and the only trace is a counter; ADR-010 item 4 leaves it as "OPEN (user)".
Fix: no code change in this review. Keep box 5 open; add one sentence to 042 "An evaluator outage fails open as built (ADR-010 item 4); keep or change is open in DEC-1"; the conflict goes to `proposals.md` with B-1, with two options: `fail_closed` in the `cloud` profile (count the outage as a failed attempt, so the run falls back) and `fail_open` in `local`, or keep fail-open and add an alert on `gate_failed_open / gate_runs` (the metrics spec already lists "fail-open rate" with a 10% bar).
Status: proposal

### W4-02 [HIGH] [rule 14, 3] docs/planning/issues/042-A-2-evaluator-gate-fallback.md
Problem: the product plan's phase B turns the judge on by default (`judge_route`, async at 0.1 on `decision-small`, [models](../../specs/models.md) "The judge model"), and 042 A-2 is the only issue about the gate's use, yet it asks for a different design: `harness.evaluator: facts_kept` (not a key; `HarnessSpec` has `evaluator_gate` and `extra="forbid"`), `threshold: 0.95` (built default 0.7), and an `evaluator-facts` agent with `security.allowed_agents` (not built, [contract v6](../../contracts/contract-v6.md) lines 247 and 424).
Why it matters: phase B could close with 042 untouched, and its boxes would never describe the judge the product runs.
Fix: rewrite 042 for phase B and resize S to M. Phase B needs five things from it, each a box: (1) the judge on the route map: `spec.model.judge_route` read by `JudgeEvaluator` (it reads `CHASSIS_JUDGE_ROUTE` today), checked against the service's key; (2) the `cloud` profile sets `evaluator_gate: true`, `evaluator_mode: async`, `evaluator_sample_rate: 0.1`, `spec.adapters.evaluator: judge`, with a test per setting; (3) the outage rule, once DEC-1 decides (W4-01); (4) the judge's cost and tokens in `/metrics` (W4-04); (5) the judge's latency, cost, and agreement from the Mac run recorded in `pocs/poc-07-cross-cutting-decisions/notes/` and ADR-010 item 10 confirmed or changed. Reword boxes 1 to 3 to `threshold` and `fallback_route` (they are then ticked), reword box 4 to "the gate calls only the configured evaluator adapter" (the judge is not an agent), and reword box 6 to the 30-case gate set (`packages/eval-kit`, `golden/cases-gate.yaml`) in place of the S-4 held-out set.
Status: open

### W4-03 [HIGH] [rule 13, 4] docs/planning/tools/backlog.py
Problem: 042 A-2 (phase B) and 043a (`later`) depend on 041 A-1, which is parked; 042 also depends on 028 S-3, which W3 keeps live for its `facts_kept` half (phase B, [W3](findings-W3-simplifier-slm.md)).
Why it matters: a live issue may not depend on a parked one, and 042 blocks the phase B numbers.
Fix: in `ROWS`, set A-2 deps to `['H-4', 'S-3']` (drop `A-1`; the judge is `JudgeEvaluator` with rubric `judge-facts-v1`, and S-3 stays for the `facts_kept` definition W3 keeps), and A-3a deps to `['H-17']`.
Status: open

### W4-04 [HIGH] [rule 11, 14] packages/chassis/src/chassis/adapters/evaluators.py
Problem: the judge's cost and tokens do not reach `/metrics`: `count_usage` is called only from the model proxy (`model_admission.py:341`), the judge goes out through `ModelPort`, so `chassis_model_cost_microusd_total` leaves the judge out; they reach only the response `metrics` event (sync) or a span (async); [metrics](../../specs/metrics.md) defines "cost per good task" as "every route and the judge included", and 042 A-2's last box asks for "the judge's cost show in `/metrics`".
Why it matters: phase E's counter metric "cost per good task" and the gate-cost check in ADR-010 item 10 would both read too low by the judge's cost, which at rate 1.0 is about 2.6 times the answer.
Fix: add a box to 042: "the judge's tokens and cost count in `chassis_model_tokens_total` and `chassis_model_cost_microusd_total` under the judge route, or in a named judge family, and a scrape test shows it"; the code change goes to `proposals.md`.
Status: proposal

### W4-05 [HIGH] [rule 14, 3] docs/planning/issues/046-A-5-agent-dashboard.md
Problem: phase E needs "the container dashboard" (the north star, inputs, and counters on one dashboard, `python -m chassis.dashboards` writing `deploy/dashboards/container.json`), and the product plan's gap "no container dashboard" has no issue; 117 X-3 is SLOs and alerts, and 046 A-5 is a per-simplifier dashboard whose panels are half simplifier-only.
Why it matters: the phase E done-when ("one dashboard with one week of real numbers") has nowhere to land.
Fix: rescope 046 to "Container dashboard and per-agent gate panels" (phase E), resize S to M. Keep the generic boxes: first-try pass rate (1 − (`chassis_gate_retries_total` + `chassis_gate_fallbacks_total`) / `chassis_gate_runs_total`), retry rate, fallback rate with a 10% target line, links to Langfuse and Loki, and "provisioned from code" (ticked). Move "tokens saved" and the G-3 baseline to Out of scope with the parked simplifier. Add three rows from the metrics spec (north star, inputs, counters) and a test that the file is up to date. The filter by config version and model route version needs a label the families do not carry ([contract v7 A.3](../../contracts/contract-v7.md#a3-the-families): only `agent`, `interface`, `status`, `code`, `route`, `direction`, `tool`, `mode`); make it an open box tied to a contract change, or drop it. Drop the template-repo box (`agentctl` ships no dashboards; that is 059 H-15) and the 100-calls-10-fallbacks box in favor of the PoC-8 stack drill.
Status: open

### W4-06 [HIGH] [rule 9, 13] docs/planning/issues/043-A-3-result-events.md
Problem: 043 A-3 mixes a live chassis half (result events: built, with retry, counters, redaction, and a failed type) and a simplifier half (`source` naming the original model, the recorder's record, the Phase 3 end-to-end test, the savings dashboard); no box ticks, because each names the simplifier.
Why it matters: the whole issue reads as undone, though the publisher is the one W4 part the PoCs built.
Fix: split. 043a (S, `later`): "one result event per call on the native, OpenAI, and Anthropic interfaces" (a counting test; today only the native case and a native/OpenAI stream takeover case exist), "a real result event carries `traceparent`, `idempotencykey`, `configversion`, and `modelroute`" (the contract suite checks a hand-built event; `test_a_completed_run_publishes_one_valid_cloudevent` asserts `idempotencykey`, `partitionkey`, `configversion`, `id`, `source`, `subject`, and `dataschema`, but not `traceparent` or `modelroute`), and the `source` decision (below). 043b (parked): the recorder box, the Phase 3 test, the savings dashboard. The switch is `spec.events.result_events` (a boolean, default false), not a module with `events.produces`; [contract v3](../../contracts/contract-v3.md) (lines 224 to 232) holds the event contract (v7 anchors only the `aigenerated` extension).
Status: open

### W4-07 [MEDIUM] [rule 5, 6, 10] docs/planning/issues/042-A-2-evaluator-gate-fallback.md
Problem: `## What` and the criteria describe a design the code does not have: the `evaluator-facts` agent, `harness.evaluator`, `modules: [evaluator_gate]`, a `retry` envelope status (the gate sets only `fallback`; a passing retry ends `ok`, `packages/chassis/tests/test_evaluator_gate.py::test_a_retry_that_passes_is_ok_not_fallback`), "a failed evaluator is a failed check" (the opposite of the built outage rule), and a "Proposed in PoC-7" bullet that says the user accepts ADR-010 at a pull request; the "Open concern" paragraph is settled.
Why it matters: an engineer who builds from this text writes a second gate beside the one in the chassis.
Fix: replace the flow bullets with the built flow (score, retry once on the same route below `threshold`, one unscored attempt on `fallback_route`, end status `fallback`; with no fallback route `error evaluator_gate_failed`), "Decided in ADR-010" in place of "Proposed in PoC-7", move the open concern to a one-line pointer to ADR-010 items 9 and 10, and mark `threshold: 0.95` as `suggested:` (built default 0.7; a facts-rubric value, not a platform value).
Status: open

### W4-08 [MEDIUM] [rule 14, 3, 4] docs/planning/issues/040-D-0-recorder-agent.md
Problem: 040 D-0 has no phase: the product plan never names a records store, the weekly record is a note by the `record-measurement` skill, and the metrics come from Prometheus; it is sized M, but its 9 boxes need a new `StorePort`, a Postgres adapter and its contract suite, a consumer wired in the chassis (`spec.events.consume` is refused at load, `packages/chassis/tests/test_server.py::test_events_consume_is_refused_until_event_triggered_runs_exist`), roles, an outbox, and Compose and CloudNativePG deployments.
Why it matters: it blocks 064 D-1, 071 D-8, and the history swap, and it is the largest un-built piece of W4.
Fix: phase `later`; resize M to L (nine open boxes, nothing ticked). It becomes phase E only if the user picks events over Prometheus for the north star (an open decision in the product plan). Add 021 H-20 as a real dependency of the consume path (it is listed already) and note it is `later`.
Status: open

### W4-09 [MEDIUM] [rule 5, 1] docs/planning/issues/040-D-0-recorder-agent.md
Problem: the issue's text differs from the code in five places: (a) redaction runs in the producer before publish (`packages/chassis/src/chassis/redaction.py`, applied in `server/results.py`), so the recorder gets events already redacted, and the regex covers email, phone, card, IBAN, and US SSN but no names, while box 3 puts a name in the fixture; (b) events carry only the hash of the idempotency key (`idempotencykey`, `partitionkey`), never the raw key; (c) the payload has no `trace_id` field, only the `traceparent` extension; (d) `source` collides with the CloudEvent `source` (`/agents/<name>`); (e) `StorePort` does not exist (`StatePort` is a TTL bytes store on Valkey).
Why it matters: the recorder built from this text would dedupe on a value it never receives and claim name redaction it cannot do.
Fix: reword the dedupe to the event `id` (unique per run and type) and the key hash; read `trace_id` from `traceparent`; rename the model-source field (suggested: `original_model`) and decide it once with 043a; box 3 uses an email and a phone number and puts names under the open Q6 decision (named-entity masking, 011 section 4); add a box for defining `StorePort` ("a port in `chassis.ports`, with a fake and a contract suite"); note the open dead-letter bug (a failed dead-letter publish ends the subscription's consumer task, [W2b-13](findings-W2b-chassis-security-deploy.md)); the recorder relies on that path.
Status: open

### W4-10 [MEDIUM] [rule 12] docs/planning/issues/040-D-0-recorder-agent.md
Problem: 040 D-0 box 4 ("records carry `source`, filled by A-3") needs 043, and box 6 ("the workload holds no store credential") needs the tool proxy that the issue says is "part of 054 H-16, which comes later"; both point to higher numbers, and 043 depends on 040.
Why it matters: the order of work has a loop and a forward reference.
Fix: move the `source` field to 043a (the recorder stores whatever the event carries; its box says "the stored record has the event's model-source field"); for box 6, depend on the tool gateway PoC-5 already runs (the MCP gateway behind the chassis, `pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py::test_mcp_gateway_refuses_the_workload_without_the_chassis_key`) and say 054 H-16 extends it, or keep the box open with the dependency `054 H-16` stated and the issue `later`.
Status: open

### W4-11 [MEDIUM] [rule 13] docs/planning/tools/backlog.py
Problem: five issues outside W4 depend on W4 issues that this review parks or sets to `later`: 075 E-3 depends on 047 M-3 and 046 A-5; 096 P-2a depends on 046 A-5; 064 D-1 depends on 040 D-0 and 042 A-2; 071 D-8 depends on 040 D-0; 127 L-3 (parked) depends on 040 D-0.
Why it matters: parking 047 breaks 075 E-3's chain, and the product plan parks `EncoderEvaluator`, which E-3 swaps in.
Fix: list for the consolidation session and the W6 and W8 sessions: 075 E-3 is parked with `EncoderEvaluator` (its deps on 047 and 046 then do not matter); 096 P-2a keeps 046 (live, phase E); 064 D-1 and 071 D-8 keep 040 (`later`), so they are `later` at best; no live issue (A to F) depends on a parked one after W4-03. Every dependency still points to a lower number.
Status: open

### W4-12 [MEDIUM] [rule 6] docs/planning/issues/042-A-2-evaluator-gate-fallback.md
Problem: three values in 042 are `suggested:` or hard-coded and settled elsewhere: the fallback rate bar (< 10%) is the metrics spec's counter-metric target ("below 10% (117 X-3)"); the sample rate and async mode are ADR-010 item 10's recommendation (still open for the user: ADR-010 questions 1 and 2); the gate default `sync` and 1.0 are built, the target is `async` and 0.1.
Why it matters: phase B's "defaults confirmed or changed by ADR-010's rule" needs these as one DEC-1 item, not three scattered values.
Fix: cite the metrics spec for the 10% bar; list for the DEC-1 decision pack: outage rule (W4-01), default mode and sample rate (ADR-010 question 2), and where the judge score goes (ADR-010; Langfuse scores for async results are not written yet per the PoC-7 draft).
Status: open

### W4-13 [LOW] [rule 5, 13] docs/planning/issues/044-G-4-history-swap.md
Problem: 044 G-4 suggests the swap as a LiteLLM pre-call hook but ADR-001 item 6 and its follow-up 3 keep the proxies thin ("Guardrails and PII checks stay in LiteLLM"); the pattern exists (`deploy/compose/poc08-litellm/litellm.yaml` runs `pii-mask` and `prompt-guard` as `pre_call` guardrails), but no spec, ADR, or note decides the swap, and `docs/specs/models.md` names none.
Why it matters: low while parked; if the lever returns it needs a decision first.
Fix: park 044 and 045 with one line in each: "Parked 2026-11-06 with the SLM track: the short versions come from the SLM simplifier's records; revisit as `later` if a route map and a recorder exist"; keep the pre-call-hook suggestion and cite ADR-001 item 6.
Status: open

### W4-14 [LOW] [rule 14, 8] docs/planning/issues/048-M-4-canary-rollback.md
Problem: 048 M-4 is a model canary by LiteLLM weights; what the product has are chassis-release rolling updates and drains (`pocs/poc-04-stateless-scalable/tests/test_kind.py::test_rolling_restart_fails_no_request`, `pocs/poc-09-agent-mvp-template/notes/2026-11-05-kind-hpa-drill.md`), and the metrics spec lists "chassis release lead time" and "rollbacks per chassis release" with "none yet".
Why it matters: a reader could think the rolling drills close 048; they close none of its boxes.
Fix: park 048; point the metrics spec's chassis-release rollback to 056 CH-7 (rings), and note in 048 that a per-task model swap (phase B) needs no canary because it is a config reload of the route map. No box ticks.
Status: open

### W4-15 [LOW] [rule 7] docs/planning/issues/044-G-4-history-swap.md
Problem: 044 G-4 is epic story G-4 in Phase 1 (labels `phase:1-router`, `area:router`) but sits in wave W4, and its `epic_refs` F.1 and J describe a router lever, not the recorder; 041 A-1 and 046 A-5 refs are fine.
Why it matters: wording only; the epic is never edited.
Fix: keep the refs; add "Parked with the SLM track" to the title line of 044 and 045 so the index shows it.
Status: open

## Live issues that depend on a parked one

After the fixes above (W4-03, W4-11) none remain in A to F. Before them:

- 042 A-2 (B) depends on 041 A-1 (parked). Its other dependency, 028 S-3, is live in part (W3, phase B).
- 043a (`later`) depends on 041 A-1.
- 046 A-5 (E) depends on 042 A-2 only (live); it blocks 048 M-4 (parked) and 075 E-3 (parked with `EncoderEvaluator`).
- 040 D-0 (`later`) depends on no parked issue; it blocks 044 G-4 (parked) and 127 L-3 (parked), 064 D-1, and 071 D-8.
- 047 M-3 and 049 M-5 (both parked) depend on 037 M-2, which W3 keeps live (phase B), so those edges are fine; 047 also depends on 041 (parked, so parked to parked). 049 M-5's `What` names the training pipeline 033 S-5 (parked by W3). W3 leaves the decision on 047 and 049 to this session ([W3](findings-W3-simplifier-slm.md)): both parked.
