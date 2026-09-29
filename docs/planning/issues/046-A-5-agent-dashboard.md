---
title: "A-5: Simplifier agent dashboard: pass rate, fallback rate, tokens saved, latency"
labels: ["story", "priority:P1", "phase:3-simplifier-agent", "area:agent", "size:S", "layer:platform"]
milestone: "W4 Simplifier agent live"
index: 46
epic_id: A-5
depends_on: ["014 H-7", "042 A-2"]
blocks: ["048 M-4", "075 E-3", "096 P-2a"]
epic_refs: [G.2, success-metrics]
---

## Why

The simplifier needs its own view of quality and savings, so the team can see whether it meets the success metrics. It follows the gate (042 A-2), which produces the pass, retry, and fallback counts the dashboard shows. It is P1, because the router savings dashboard already shows the headline numbers.

## What

- A Grafana dashboard for the simplifier, built on the Prometheus `/metrics` (014 H-7) and the router's token and cost counts (004 G-2).
- Panels: pass rate (first-try `facts_kept` pass), retry rate, fallback rate, tokens saved, p50 and p95 latency, requests, errors, and cost.
- Tokens saved: big-model tokens avoided on calls the SLM served, minus fallback and judge tokens.
- Target lines for the success metrics: fallback rate < 10% and `facts_kept` ≥ 0.95.
- Filters by config version and model route version, so a new model can be compared with the last one.
- Links from panels to Langfuse traces and Loki logs.
- The dashboard is stored as code in the template repo, so every agent gets one with the G.2 panels.

## Out of scope

- SLOs and alerts (117 X-3).
- Cost per agent and per pool (120 X-7).
- The router savings dashboard (006 G-6).

## Acceptance criteria

- [ ] The dashboard shows every listed panel for the simplifier, in Docker Compose and on Kubernetes.
- [ ] A scripted load of 100 calls with 10 forced fallbacks shows a 90% pass rate and a 10% fallback rate.
- [ ] Tokens saved subtracts fallback and judge tokens, and matches the router counts for the same load.
- [ ] Target lines show at 10% fallback rate and 0.95 `facts_kept`.
- [ ] Filtering by model route version splits the numbers per model.
- [ ] The dashboard is provisioned from code, not built by hand.

## Dependencies

- Depends on: [014 H-7](014-H-7-observability.md), [042 A-2](042-A-2-evaluator-gate-fallback.md)
- Blocks: [048 M-4](048-M-4-canary-rollback.md), [075 E-3](075-E-3-evaluator-replaces-judge.md), [096 P-2a](096-P-2a-mcp-golden-set-metrics-tools.md)

## References

- Epic story: [A-5 in Phase 3](../slm-agent-platform-epic-v3.md#phase-3-simplifier-agent)
- Epic context: [G.2](../slm-agent-platform-epic-v3.md#g2) · [Success metrics](../slm-agent-platform-epic-v3.md#success-metrics)
- Backlog plan: [000-plan.md](000-plan.md)
