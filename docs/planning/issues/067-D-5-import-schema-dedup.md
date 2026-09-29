---
title: "D-5: Schema check and deduplication on import"
labels: ["story", "priority:P1", "phase:4-golden-set", "area:data", "size:S", "layer:platform"]
milestone: "W6 Golden set and evaluator SLM"
index: 67
epic_id: D-5
depends_on: ["064 D-1"]
blocks: ["068 D-3"]
epic_refs: [H]
---

## Why

Imported data must be as clean as recorded data, or bad rows and duplicates skew training and tests. This issue builds the check and dedup step that every import path runs through. It comes before the import sources (068 D-3), so all of them share one set of rules.

## What

- Each incoming record is checked against the golden set record JSON Schema for its task, stored in the config store under `schemas/`.
- Required fields include `source`, so every record says where its original came from.
- Invalid rows are rejected with the row number and reason. Valid rows in the same batch still go in.
- Exact duplicates, within the batch or against stored records, are dropped by a hash of the normalized input and output.
- Suggested: near-duplicates are flagged for review, using BGE-M3 vectors in pgvector and a similarity threshold in config.
- Each import run returns a data quality report: accepted, rejected by reason, duplicates, and near-duplicates.
- The step is idempotent: running the same batch again stores nothing new.

## Out of scope

- The import sources: file upload, API, and bucket watch (068 D-3).
- PII removal on import (071 D-8).
- Tags and splits (066 D-6).

## Acceptance criteria

- [ ] A row with a missing required field or a wrong type is rejected with its row number and reason, and the valid rows in the same batch are stored.
- [ ] A row without `source` is rejected.
- [ ] An exact duplicate, in the batch or already stored, is stored once and counted.
- [ ] Near-duplicates above the threshold are flagged, not dropped.
- [ ] Every run returns the data quality report, with correct counts for a mixed test batch.
- [ ] Running the same batch twice stores nothing the second time.

## Dependencies

- Depends on: [064 D-1](064-D-1-golden-set-agent.md)
- Blocks: [068 D-3](068-D-3-import.md)

## References

- Epic story: [D-5 in Phase 4](../slm-agent-platform-epic-v3.md#phase-4-golden-set-agent-and-evaluator-slm)
- Epic context: [H](../slm-agent-platform-epic-v3.md#app-h)
- Backlog plan: [000-plan.md](000-plan.md)
