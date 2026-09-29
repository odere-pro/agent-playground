---
title: "D-7: Versioned datasets with lakeFS; each export is an immutable version with an ID"
labels: ["story", "priority:P1", "phase:4-golden-set", "area:data", "size:M", "layer:platform"]
milestone: "W6 Golden set and evaluator SLM"
index: 69
epic_id: D-7
depends_on: ["064 D-1"]
blocks: ["070 D-4", "071 D-8", "121 X-4"]
epic_refs: [H, R7, R8]
---

## Why

A model is only reproducible if the exact data it trained on can be read back later. lakeFS gives every golden set export an immutable version with an ID, which the training pipeline and MLflow link to. Immutable versions are part of the Phase 4 done-when, and backups (121 X-4) and deletion rules (071 D-8) build on them.

## What

- lakeFS runs as a service on the platform object storage (MinIO locally, S3 or GCS in the cloud), in Docker Compose and on Kubernetes.
- Golden set data lives in a lakeFS repository. Each version is a lakeFS commit, and its commit ID is the version ID.
- The golden set agent creates versions through the lakeFS API, with metadata: record count, split counts, schema version, `source` counts, and creator.
- The main branch is protected, so a committed version cannot be changed.
- An API to list versions, read a version's metadata, and diff two versions.
- The training pipeline (033 S-5) reads a dataset by version ID, and MLflow records that ID.
- Suggested: a readable tag per version, for example `simplifier-golden-v3`.

## Reuse

- **Decision:** lakeFS moved to the Business Source License 1.1 from v1.87.0 (2026-09-22). Unmodified internal use is allowed. Options: keep lakeFS and accept the license, use DVC (Apache 2.0, Git-based), or use Langfuse dataset versions if 064 D-1 moves to Langfuse. Record the choice in DEC-1.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Export formats (070 D-4).
- Retention and deletion on request (071 D-8).
- Backups and restore tests (121 X-4).

## Acceptance criteria

- [ ] lakeFS runs in Docker Compose and on Kubernetes on the platform object storage.
- [ ] Creating a version returns an ID, and reading that ID later returns byte-identical data.
- [ ] A write to a committed version, or a direct push to the main branch, fails.
- [ ] The version list shows ID, time, record count, split counts, and creator.
- [ ] A diff of two versions lists added, removed, and changed records.
- [ ] A training run reads data by version ID, and MLflow shows that ID on the run.

## Dependencies

- Depends on: [064 D-1](064-D-1-golden-set-agent.md)
- Blocks: [070 D-4](070-D-4-export-formats.md), [071 D-8](071-D-8-data-rules.md), [121 X-4](121-X-4-backups-restore.md)

## References

- Epic story: [D-7 in Phase 4](../slm-agent-platform-epic-v3.md#phase-4-golden-set-agent-and-evaluator-slm)
- Epic context: [H](../slm-agent-platform-epic-v3.md#app-h) · [R7](../slm-agent-platform-epic-v3.md#r7) · [R8](../slm-agent-platform-epic-v3.md#r8)
- Backlog plan: [000-plan.md](000-plan.md)
