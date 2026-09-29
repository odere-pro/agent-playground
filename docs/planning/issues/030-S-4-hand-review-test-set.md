---
title: "S-4: Hand review of a sample and a held-out test set of at least 300 records"
labels: ["story", "priority:P0", "phase:2-simplifier-slm", "area:slm", "size:M", "layer:platform"]
milestone: "W3 Simplifier SLM"
index: 30
epic_id: S-4
depends_on: ["029 S-2"]
blocks: ["033 S-5", "037 M-2", "068 D-3", "074 E-2"]
epic_refs: [E.5]
---

## Why

Metrics alone can miss bad pairs, so a person checks a sample before training. The held-out test set of at least 300 records is where the SLM targets are measured for the Phase 2 done-when. It also serves as the golden set for the eval gate (037 M-2) until the golden set agent exists in Phase 4, and the review record is evidence for ISO 42001 A.7 (data).

## What

- Hand review of a random sample of generated pairs by the golden set reviewer named in 001 DEC-1: pass, fail, or edit, with a reason (suggested sample: 5–10% of pairs).
- A review guide based on the 027 S-1 rules.
- A held-out test set of at least 300 records, every record hand-checked, never used in training.
- A split by source document, so no text from a test record's source is in training.
- Each decision recorded with reviewer, date, decision, and reason.
- A sample report with the pass rate and main failure reasons. If the pass rate is low, the rules or the teacher are fixed and the data is generated again.
- The test set locked with an immutable data version ID, and its S-3 reference scores stored.
- Suggested: a plain file or sheet for the review, until the review UI exists.

## Out of scope

- The golden set agent (064 D-1).
- The review UI (065 D-2).
- Tags and splits in the golden set store (066 D-6), and lakeFS versions (069 D-7).

## Acceptance criteria

- [ ] A random sample of generated pairs is reviewed, and the pass rate and main failure reasons are reported.
- [ ] A held-out test set of at least 300 records exists, and every record is hand-checked.
- [ ] No test record, and no record from the same source document, is in the training split.
- [ ] Every review decision records reviewer, date, and reason.
- [ ] The test set has an immutable data version ID and is read-only.
- [ ] S-3 scores for the test set are stored as the reference.

## Dependencies

- Depends on: [029 S-2](029-S-2-training-data-generation.md)
- Blocks: [033 S-5](033-S-5-training-pipeline.md), [037 M-2](037-M-2-eval-gate-ci.md), [068 D-3](068-D-3-import.md), [074 E-2](074-E-2-evaluator-agreement-test.md)

## References

- Epic story: [S-4 in Phase 2](../slm-agent-platform-epic-v3.md#phase-2-simplifier-slm-and-model-lifecycle)
- Epic context: [E.5](../slm-agent-platform-epic-v3.md#e5)
- Backlog plan: [000-plan.md](000-plan.md)
