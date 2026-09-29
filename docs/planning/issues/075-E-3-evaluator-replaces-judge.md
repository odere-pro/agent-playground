---
title: "E-3: Evaluator SLM replaces the big-model judge in the harness"
labels: ["story", "priority:P1", "phase:4-golden-set", "area:evaluator", "size:S", "layer:platform"]
milestone: "W6 Golden set and evaluator SLM"
index: 75
epic_id: E-3
depends_on: ["042 A-2", "074 E-2", "047 M-3", "046 A-5"]
blocks: ["126 L-2"]
epic_refs: [F.1, J]
---

## Why

Until now, a big model judges every simplifier call, which eats into the savings. Swapping in the evaluator SLM, once it passes the agreement test (074 E-2), cuts the cost of judging every simplifier call with a big model, and it runs on CPU. It is the last issue of Phase 4, so it also closes the Phase 4 done-when.

**Open concern (review 2026-09-29), to explore until we align:** this is where the platform's cost thesis is tested end to end. Report the net saving per call with the encoder against the big-model judge, including the encoder's CPU cost, and keep a sampled big-model judge for drift checks.

## What

- Serve the evaluator SLM as the `evaluator-facts` agent (class `stateless`, kind `evaluator`) on CPU.
- The swap is a new config version of `evaluator-facts`. The simplifier's config (`harness.evaluator: facts_kept`, `allowed_agents: [evaluator-facts]`) and code do not change.
- Before the switch, the SLM evaluator runs in shadow next to the big-model judge on live traffic (047 M-3), and their pass and fail decisions are compared.
- Suggested: scores in a narrow band around 0.95 go to the big-model judge, so hard cases keep the stronger judge.
- The golden set agent (064 D-1) also scores records with the SLM evaluator.
- One-step rollback: point `evaluator-facts` back to the big-model judge by config.
- Judge cost and latency, before and after, show on the agent dashboard (046 A-5).

## Out of scope

- Evaluators for other tasks.
- The system prompt improver that builds on this evaluator (126 L-2).
- Scale to zero for evaluator pools (104 O-3).

## Acceptance criteria

- [ ] After one config change, the simplifier's gate calls the SLM-based `evaluator-facts` on CPU, with no simplifier code change.
- [ ] Before the switch, the shadow run on live traffic shows the SLM and the judge agree at the E-2 pass bar.
- [ ] After the switch, the gate still catches every fact-drop fixture from 042 A-2, and the fallback rate stays < 10%.
- [ ] Judge cost per simplifier call drops, and the drop shows on the agent dashboard.
- [ ] Evaluator p95 latency is lower than the big-model judge's.
- [ ] Rolling back to the big-model judge is one config change, with no restart.
- [ ] Phase 4 done-when: one checklist run shows import and export in one step in every format, immutable versions, PII rules applied, and the evaluator SLM passing E-2, with a link to each test.
- [ ] The evaluator SLM adapter passes the same `EvaluatorPort` contract suite as the LLM judge and the fake.

## Dependencies

- Depends on: [042 A-2](042-A-2-evaluator-gate-fallback.md), [074 E-2](074-E-2-evaluator-agreement-test.md), [047 M-3](047-M-3-shadow-mode.md), [046 A-5](046-A-5-agent-dashboard.md)
- Blocks: [126 L-2](126-L-2-system-prompt-improver.md)

## References

- Epic story: [E-3 in Phase 4](../slm-agent-platform-epic-v3.md#phase-4-golden-set-agent-and-evaluator-slm)
- Epic context: [F.1](../slm-agent-platform-epic-v3.md#f1) · [J](../slm-agent-platform-epic-v3.md#app-j)
- Backlog plan: [000-plan.md](000-plan.md)
