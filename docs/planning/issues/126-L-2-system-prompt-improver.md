---
title: "L-2: System prompt improver"
labels: ["later", "priority:P3", "phase:11-later", "area:slm", "size:L", "layer:platform"]
milestone: "W12 Later"
index: 126
epic_id: L-2
depends_on: ["070 D-4", "075 E-3"]
blocks: []
epic_refs: [phase-11-later-ideas]
---

## Why

Prompts drift and have blind spots that nobody sees one record at a time. A system prompt improver finds recurring problems in stored records and proposes prompt changes, with the golden set as the judge. It is a Phase 11 idea, so this P3 issue is a plan with entry criteria, not a build. It needs golden set exports (070 D-4) and the evaluator SLM (075 E-3) first.

## What

- A design note for the loop: an SLM tags problems in stored records, a big model suggests prompt changes, and the golden set decides whether to keep them.
- A first list of problem tags (suggested: dropped fact, banned word, too long, wrong tone), built from reviewer edits in the Review UI.
- How a new prompt version is stored: a new file behind `prompts.system_ref` in the config store, pinned per run like any config.
- The keep rule: a new prompt ships only if it beats the current one on the golden set, with `facts_kept` never below 0.95, through the eval gate (037 M-2).
- A person approves each prompt change before it goes live. Rollback points back to the old version.
- The big-model cost per improvement cycle, next to the expected savings.
- Entry criteria, for example: enough reviewed records with problem tags, and a pass rate that has stayed flat for a set period.

## Out of scope

- Building the improver; this issue is a plan.
- Changes to model weights; this only changes prompts.
- Routing changes (127 L-3).

## Acceptance criteria

- [ ] A design note covers the tagger, the suggester, the keep rule, human approval, and rollback.
- [ ] The keep rule is written as a testable eval gate on the golden set, with `facts_kept` ≥ 0.95 as a floor.
- [ ] Entry criteria are measurable, with an owner who checks them.
- [ ] A cost estimate per improvement cycle exists.
- [ ] The plan is split into sized build issues.

## Dependencies

- Depends on: [070 D-4](070-D-4-export-formats.md), [075 E-3](075-E-3-evaluator-replaces-judge.md)
- Blocks: none

## References

- Epic story: [Phase 11](../slm-agent-platform-epic-v3.md#phase-11-later-ideas)
- Epic context: [Phase 11](../slm-agent-platform-epic-v3.md#phase-11-later-ideas)
- Backlog plan: [000-plan.md](000-plan.md)
