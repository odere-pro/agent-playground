---
title: "D-6: Tags and splits (train, test, holdout) per record"
labels: ["story", "priority:P1", "phase:4-golden-set", "area:data", "size:S", "layer:platform"]
milestone: "W6 Golden set and evaluator SLM"
index: 66
epic_id: D-6
depends_on: ["064 D-1"]
blocks: ["070 D-4"]
epic_refs: [H]
---

## Why

Training and testing need clean splits, or test data leaks into training and the scores lie. Tags let teams build a golden set for one task, domain, or source. It builds on the golden set store (064 D-1) and comes before export (070 D-4), which exports by split.

## What

- Each golden set record gets any number of tags (for example task, domain, language, source model) and exactly one split: `train`, `test`, or `holdout`.
- Suggested: the split is set by a hash of a stable key (the input text hash), so it is deterministic and the same input never lands in two splits.
- Target split ratios in config (suggested: 80/10/10).
- `holdout` is locked: once set, it changes only by a logged admin action.
- An API to set tags and to list records by tag and split. Changes need the write scope.
- Tags can also be set in the review UI (065 D-2).

## Out of scope

- Export by split (070 D-4).
- Dataset versions (069 D-7).
- Import of existing sets, such as the S-4 test set (068 D-3).

## Acceptance criteria

- [ ] Every approved record has exactly one split.
- [ ] Running the split step again gives every record the same split.
- [ ] Records with the same input hash always share a split.
- [ ] On 1,000 synthetic records, each split is within 2 points of its target ratio.
- [ ] A `holdout` record cannot change split without an admin action, and that action is logged.
- [ ] The API lists records by tag and by split, and refuses tag changes without the write scope.

## Dependencies

- Depends on: [064 D-1](064-D-1-golden-set-agent.md)
- Blocks: [070 D-4](070-D-4-export-formats.md)

## References

- Epic story: [D-6 in Phase 4](../slm-agent-platform-epic-v3.md#phase-4-golden-set-agent-and-evaluator-slm)
- Epic context: [H](../slm-agent-platform-epic-v3.md#app-h)
- Backlog plan: [000-plan.md](000-plan.md)
