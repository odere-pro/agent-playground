---
title: "D-3: Import from file upload, API call, or bucket watch"
labels: ["story", "priority:P1", "phase:4-golden-set", "area:data", "size:S", "layer:platform"]
milestone: "W6 Golden set and evaluator SLM"
index: 68
epic_id: D-3
depends_on: ["067 D-5", "030 S-4"]
blocks: ["070 D-4", "071 D-8", "106 O-5", "113 O-9"]
epic_refs: [H]
---

## Why

Golden sets also come from outside the recorder: hand-labeled files, other teams, and the S-4 held-out test set. Import in one step, from any of three sources, is part of the Phase 4 done-when. It comes right after the check and dedup step (067 D-5), which every source runs through.

## What

- Three ways in, one pipeline: file upload, API call, and bucket watch on an object storage prefix (MinIO, S3, or GCS).
- Accepted formats: JSONL, CSV, Parquet, and Hugging Face datasets, the same formats as export.
- One step: one upload or call runs the schema check, dedup, and store, and returns an import ID and the data quality report.
- Very large uploads cannot go through the chassis ([ADR-001](../adr/001-chassis-delivery-model.md), Cons). The file upload path has a size limit (suggested: the chassis body limit). Large imports use the bucket watch, or a presigned URL to the watched prefix.
- Suggested: large files run as an import job with a status endpoint.
- The bucket watch imports each file once, keyed by object path and version.
- Imported records go through scoring and review like recorded ones, unless an admin marks the import as already reviewed.
- An import can set tags and a split for its records (066 D-6).
- Import needs the write scope.

## Reuse

- **Use:** Langfuse dataset import (CSV, JSONL, JSON Schema checks), and HF datasets for files and bucket reads.
- **Build:** the bucket watch and the API wrapper.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- The check and dedup rules (067 D-5).
- PII removal on import (071 D-8).
- The MCP `golden_import` tool (096 P-2a).

## Acceptance criteria

- [ ] One upload of a file in each of JSONL, CSV, Parquet, and Hugging Face datasets format imports its records and returns an import ID and a report.
- [ ] The same records sent in one API call give the same result.
- [ ] A file dropped into the watched prefix is imported once; dropping it again adds no records.
- [ ] The S-4 held-out test set (≥ 300 records) imports in one step, marked as already reviewed, with split `holdout`.
- [ ] A caller without the write scope gets `403`.
- [ ] A file upload over the size limit is refused with a clear error that points to the bucket watch or a presigned URL.
- [ ] A 100,000-record file imports through the bucket watch without the service running out of memory (suggested size).

## Dependencies

- Depends on: [067 D-5](067-D-5-import-schema-dedup.md), [030 S-4](030-S-4-hand-review-test-set.md)
- Blocks: [070 D-4](070-D-4-export-formats.md), [071 D-8](071-D-8-data-rules.md), [106 O-5](106-O-5-compare-runs.md), [113 O-9](113-O-9-orchestrator-memory.md)

## References

- Epic story: [D-3 in Phase 4](../slm-agent-platform-epic-v3.md#phase-4-golden-set-agent-and-evaluator-slm)
- Epic context: [H](../slm-agent-platform-epic-v3.md#app-h)
- Backlog plan: [000-plan.md](000-plan.md)
