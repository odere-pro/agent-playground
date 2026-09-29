# PoC-8: Build in security, observability, feedback, and evals on every engine

Status: not started
Planning doc: [008-PoC-8-build-cross-cutting.md](../../docs/planning/poc/008-PoC-8-build-cross-cutting.md)
Time box: 3–5 days

## Question

Do the designs chosen in PoC-7 work end to end, on every supported engine and in every lane, as one part of the chassis?

## Scope

- [ ] Security middleware per the security ADR: auth and scopes, rate and size limits, PII redaction, the injection check, and AI-generated marking.
- [ ] Observability per the observability ADR: spans at the chassis, its model and tool proxies, and the router; structured JSON logs with `request_id` and `trace_id`; Prometheus metrics at `/metrics`; one dashboard per agent (requests, latency, errors, tokens, cost, fallback rate). The workload's logs do not pass through the chassis. suggested: they are redacted in the OTel Collector log pipeline (gap (f) in the [backlog plan](../../docs/planning/issues/000-plan.md#adr-001-follow-ups)).
- [ ] Feedback per the feedback ADR: `POST /v1/feedback`, linked to the trace, and published as an event.
- [ ] Online evals: the evaluator gate with `threshold`, `retries`, and `fallback_route` from config.
- [ ] Offline evals: the eval runner in CI with a small golden set (about 50 cases per agent), blocking a merge on regression.
- [ ] The red-team set from PoC-7 runs in CI.
- [ ] Every pipeline stage runs in every lane: `inprocess`, `sidecar`, and `remote`.

## Exit criteria

Each one has a scenario test in `tests/` or a recorded reason it cannot have one yet.

- [ ] All suites, including red-team and offline evals, run in CI, and the `fake` profile still needs no network or keys.
- [ ] Every supported engine passes the red-team set in CI.
- [ ] Every call on every engine has a complete trace, logs with `trace_id`, and metrics.
- [ ] Feedback shows on the right trace for every engine.
- [ ] A regression in business logic fails CI through the offline eval.
- [ ] The online evaluator gate retries and falls back as configured.
- [ ] Every pipeline stage runs in every lane, and the red-team set passes in each.
- [ ] No engine-specific code outside the workloads' event mappings.

## How to run

```bash
make test-poc POC=08
```

## Demo

Not recorded yet. The script and its output go to `demo/`.

## Notes and decisions

Dated files in `notes/`. `notes/backlog-changes.md` lists what the backlog should change when this iteration closes.
