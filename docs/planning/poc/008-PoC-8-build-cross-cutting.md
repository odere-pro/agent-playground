---
title: "PoC-8: Build in security, observability, feedback, and evals on every engine"
labels: ["poc", "priority:P0", "area:security", "area:observability", "area:evals"]
milestone: "Agent MVP"
index: 8
iteration: PoC-8
timebox: "3–5 days"
depends_on: ["PoC-7"]
backlog_refs: ["022 H-6", "014 H-7", "017 H-4", "037 M-2", "023 C-5"]
---

## Question

Do the designs chosen in PoC-7 work end to end, on every supported engine and in every lane, as one part of the chassis?

## Why

PoC-7 proved each concern on two engines in spikes. This iteration builds them into the chassis for real, so every agent gets them by default, whatever its lane. After this, adding an engine or a business logic plug-in means no extra security, tracing, feedback, or eval work.

## Scope

- [ ] Security middleware per the security ADR: auth and scopes, rate and size limits, PII redaction, the injection check, and AI-generated marking.
- [ ] Observability per the observability ADR: spans at the chassis, its model and tool proxies, and the router; structured JSON logs with `request_id` and `trace_id`; Prometheus metrics at `/metrics`; one dashboard per agent (requests, latency, errors, tokens, cost, fallback rate). The workload's logs do not pass through the chassis. suggested: they are redacted in the OTel Collector log pipeline (gap (f) in the [backlog plan](../issues/000-plan.md#adr-001-follow-ups)).
- [ ] Feedback per the feedback ADR: `POST /v1/feedback`, linked to the trace, and published as an event.
- [ ] Online evals: the evaluator gate with `threshold`, `retries`, and `fallback_route` from config.
- [ ] Offline evals: the eval runner in CI with a small golden set (about 50 cases per agent), blocking a merge on regression.
- [ ] The red-team set from PoC-7 runs in CI.
- [ ] Every pipeline stage runs in every lane: `inprocess`, `sidecar`, and `remote`.

## Reuse

- **Use:** everything picked in PoC-7: Langfuse v4 with OpenInference, LiteLLM guardrails with Presidio and Prompt Guard 2, LiteLLM virtual keys, Snyk Agent Scan, and promptfoo or DeepEval.
- **Build:** configuration and glue: the chassis middleware, `/v1/feedback`, the evaluator gate, dashboards, and the CI steps.
- Details: [reuse analysis](010-reuse-analysis.md)

## Out of scope

- Human review of feedback and the golden set agent (backlog W6).
- Training on feedback (backlog W3 and W6).
- Governance enforcement by the registry (backlog [077 C-1](../issues/077-C-1-governance-enforcement.md)).

## Demo

A change to the business logic that drops facts fails the eval step in CI. In a live call, a low score triggers a retry and then a fallback. The trace shows each step, and a thumbs-down feedback call appears on the same trace and on the dashboard.

## Exit criteria

- [ ] All suites, including red-team and offline evals, run in CI, and the `fake` profile still needs no network or keys.
- [ ] Every supported engine passes the red-team set in CI.
- [ ] Every call on every engine has a complete trace, logs with `trace_id`, and metrics.
- [ ] Feedback shows on the right trace for every engine.
- [ ] A regression in business logic fails CI through the offline eval.
- [ ] The online evaluator gate retries and falls back as configured.
- [ ] Every pipeline stage runs in every lane, and the red-team set passes in each.
- [ ] No engine-specific code outside the workloads' event mappings.

## Links

- Plan: [000-plan.md](000-plan.md) · Previous: [PoC-7](007-PoC-7-cross-cutting-decisions.md) · Next: [PoC-9](009-PoC-9-agent-mvp-template.md)
- Backlog issues this previews: [022 H-6](../issues/022-H-6-security-middleware.md), [014 H-7](../issues/014-H-7-observability.md), [017 H-4](../issues/017-H-4-harness-features.md), [037 M-2](../issues/037-M-2-eval-gate-ci.md) (in part), [023 C-5](../issues/023-C-5-ai-generated-marking.md)
- Epic: [B.2](../slm-agent-platform-epic-v3.md#b2), [C.1](../slm-agent-platform-epic-v3.md#c1), [G.2](../slm-agent-platform-epic-v3.md#g2), [G.3](../slm-agent-platform-epic-v3.md#g3)
