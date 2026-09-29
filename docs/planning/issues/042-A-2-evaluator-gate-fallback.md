---
title: "A-2: Evaluator gate on facts_kept, one retry, then fallback to a big model"
labels: ["story", "priority:P0", "phase:3-simplifier-agent", "area:agent", "size:S", "layer:platform"]
milestone: "W4 Simplifier agent live"
index: 42
epic_id: A-2
depends_on: ["017 H-4", "028 S-3", "041 A-1"]
blocks: ["046 A-5", "047 M-3", "064 D-1", "075 E-3"]
epic_refs: [B.2]
---

## Why

The top quality risk is that the simplifier drops facts. This issue guards every answer with a `facts_kept` check, one retry, and a fallback to a big model, It uses a big-model judge behind the evaluator port (the `evaluator-facts` agent) until the evaluator SLM replaces it in 075 E-3. That judge costs money on every call, which is why E-3 is planned.

**Open concern (review 2026-09-29), to explore until we align:** the judge is a big-model call on every simplifier call, so until 075 E-3 the gate can cost more than the SLM saves, and it doubles p95 latency. Record the judge's tokens, cost, and latency in the request's `metrics` from the first day, so 031 G-5 and 006 G-6 show the net saving, not the gross. Explore a sample rate and an asynchronous gate before this runs on production traffic.

## What

- An `evaluator-facts` agent: class `stateless`, kind `evaluator`. It scores `facts_kept` with the S-3 judge (028 S-3) on a big-model route through the LLM router.
- The evaluator gate runs in the chassis, not in the workload. The chassis calls the evaluator only through `EvaluatorPort`, with `security.allowed_agents: [evaluator-facts]`.
- Simplifier config: `modules: [evaluator_gate]`, `harness.evaluator: facts_kept`, `threshold: 0.95`, `retries: 1`, `model.fallback_route: big-default`.
- Flow: score the SLM answer; below 0.95, retry once on `simplifier-slm`; still below, answer from `big-default`. suggested: the chassis retries by calling `handle` again, and falls back by setting `ctx.model.route` to `big-default` (gap (e) in [the gaps in the backlog plan](000-plan.md#adr-001-follow-ups)).
- The envelope `status` is `ok`, `retry`, or `fallback`. `metrics` carries the score, the attempt count, and the route that answered.
- Suggested: if the evaluator fails or times out, treat it as a failed check and fall back, so no unchecked SLM answer is returned.
- Pass, retry, and fallback counters, plus the judge's tokens and cost per agent (004 G-2).

## Out of scope

- The gate, retry, and fallback mechanics in the chassis (017 H-4). This issue configures and uses them.
- The agent dashboard (046 A-5).
- Sending low scores to human review (064 D-1, 065 D-2).
- The evaluator SLM (073 E-1) and the swap to it (075 E-3).

## Acceptance criteria

- [ ] An answer with `facts_kept` ≥ 0.95 returns `status: ok` after one model call.
- [ ] A fixture that drops a fact triggers exactly one retry and then a `big-default` answer, with `status: fallback`.
- [ ] No call makes more than one retry.
- [ ] The simplifier's chassis can call only `evaluator-facts`, as the grant on the simplifier's key allows. A call to any other evaluator is refused.
- [ ] With the evaluator stopped, calls fall back and never return an unchecked SLM answer.
- [ ] On the S-4 held-out test set, the fallback rate is < 10%.
- [ ] Pass, retry, and fallback counts and the judge's cost show in `/metrics` and in the router's per-agent counts.

## Dependencies

- Depends on: [017 H-4](017-H-4-harness-features.md), [028 S-3](028-S-3-simplifier-metrics.md), [041 A-1](041-A-1-simplifier-core.md)
- Blocks: [046 A-5](046-A-5-agent-dashboard.md), [047 M-3](047-M-3-shadow-mode.md), [064 D-1](064-D-1-golden-set-agent.md), [075 E-3](075-E-3-evaluator-replaces-judge.md)

## References

- Epic story: [A-2 in Phase 3](../slm-agent-platform-epic-v3.md#phase-3-simplifier-agent)
- Epic context: [B.2](../slm-agent-platform-epic-v3.md#b2)
- Backlog plan: [000-plan.md](000-plan.md)
