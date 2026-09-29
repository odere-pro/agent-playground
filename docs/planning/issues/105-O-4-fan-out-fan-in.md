---
title: "O-4: Fan-out and fan-in with overlap and a consistency pass"
labels: ["story", "priority:P1", "phase:7-orchestrator", "area:orchestrator", "size:M", "layer:platform"]
milestone: "W9 Orchestrator"
index: 105
epic_id: O-4
depends_on: ["103 O-11", "104 O-3"]
blocks: []
epic_refs: [F.7, C.2]
---

## Why

Long documents do not fit one small-model call. Fan-out splits the text into sections, runs them in parallel on the pool, and merges the results; a consistency pass then fixes the seams. This issue delivers the Phase 7 done-when part "runs a fan-out simplification from a workflow file in the config store".

## What

- `split` from the workflow file: `by: section`, `max_tokens`, `overlap_tokens`. Section breaks follow headings and paragraphs; a section above `max_tokens` is split again.
- Each section overlaps its neighbor by `overlap_tokens`, so no fact is cut at a boundary.
- The `simplify` step runs once per section, with `parallel: true`, on the `simplify` pool.
- A `merger` agent joins the section outputs in order and removes the overlap text. C.2 names this agent but no story builds it, so it is built here as a stateless transformer (`agentctl new merger --kind transformer`; suggested: a deterministic join with no model call).
- The `consistency` step runs the simplifier on `input: merged` to smooth the seams.
- `evaluate` runs `evaluator-facts` on the final output against `threshold: 0.95`. Below it, `on_failure` applies.
- Uses the `simplify-long-doc` workflow file (version 2.1.0) from C.2 as written.

## Out of scope

- Compare runs (106 O-5).
- Workflow `budget` enforcement (107 O-6).
- Split modes other than `by: section`.

## Acceptance criteria

- [ ] `simplify-long-doc` runs from the config store on a test document of at least 10 sections and returns one simplified text.
- [ ] No section sent to the simplifier is longer than `max_tokens`, and neighbors share `overlap_tokens` of text.
- [ ] Sections run in parallel on the `simplify` pool; the trace shows overlapping spans.
- [ ] The merged output has no duplicated overlap text.
- [ ] The final output scores `facts_kept` ≥ 0.95 against the source, or the run follows `on_failure`.
- [ ] Changing `max_tokens` in the workflow file changes the split on the next run, with no code change.

## Dependencies

- Depends on: [103 O-11](103-O-11-workflow-engine.md), [104 O-3](104-O-3-agent-pools-keda.md)
- Blocks: none

## References

- Epic story: [O-4 in Phase 7](../slm-agent-platform-epic-v3.md#phase-7-orchestrator)
- Epic context: [F.7](../slm-agent-platform-epic-v3.md#f7) · [C.2](../slm-agent-platform-epic-v3.md#c2)
- Backlog plan: [000-plan.md](000-plan.md)
