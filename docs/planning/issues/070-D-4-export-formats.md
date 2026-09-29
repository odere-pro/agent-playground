---
title: "D-4: Export in JSONL, CSV, Parquet, and Hugging Face datasets format"
labels: ["story", "priority:P1", "phase:4-golden-set", "area:data", "size:S", "layer:platform"]
milestone: "W6 Golden set and evaluator SLM"
index: 70
epic_id: D-4
depends_on: ["066 D-6", "069 D-7", "068 D-3"]
blocks: ["073 E-1", "096 P-2a", "126 L-2"]
epic_refs: [H]
---

## Why

Golden sets are only useful if training jobs and other teams can take them out in the format they use. Export in every listed format, in one step, completes the import and export part of the Phase 4 done-when. It needs splits (066 D-6) and versions (069 D-7), and it feeds the evaluator SLM (073 E-1) and the MCP golden set tools (096 P-2a).

## What

- Export in JSONL, CSV, Parquet, and Hugging Face datasets format.
- The Hugging Face export is a dataset with `train`, `test`, and `holdout` splits taken from D-6.
- Filters: task, tags, splits, and state. Only approved records by default.
- Each export is tied to an immutable lakeFS version and returns its version ID.
- One step: one API call returns the version ID and a download link for the chosen format.
- Every export includes a metadata file: version ID, schema version, counts per split, and `source` counts.
- The same record schema in every format, so an export imports back with no loss.
- Export needs the read scope and is logged.

## Reuse

- **Use:** HF datasets (JSONL, Parquet, `push_to_hub`) and pandas for CSV.
- **Build:** the export job by version ID.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- The MCP `golden_export` tool (096 P-2a).
- PII rules on export (071 D-8).
- Training on exports (073 E-1).

## Acceptance criteria

- [ ] One API call exports approved records in each of the four formats and returns a version ID.
- [ ] Exporting the same version ID twice gives the same records in the same order.
- [ ] The Hugging Face export loads with the `datasets` library and has `train`, `test`, and `holdout` splits that match D-6.
- [ ] Filters by tag and split work, and records that are not approved are left out by default.
- [ ] Phase 4 done-when (import and export): a set exported in each format imports back in one step (068 D-3) with no record lost, changed, or duplicated.
- [ ] A caller without the read scope gets `403`.

## Dependencies

- Depends on: [066 D-6](066-D-6-tags-splits.md), [069 D-7](069-D-7-lakefs-versions.md), [068 D-3](068-D-3-import.md)
- Blocks: [073 E-1](073-E-1-evaluator-slm.md), [096 P-2a](096-P-2a-mcp-golden-set-metrics-tools.md), [126 L-2](126-L-2-system-prompt-improver.md)

## References

- Epic story: [D-4 in Phase 4](../slm-agent-platform-epic-v3.md#phase-4-golden-set-agent-and-evaluator-slm)
- Epic context: [H](../slm-agent-platform-epic-v3.md#app-h)
- Backlog plan: [000-plan.md](000-plan.md)
