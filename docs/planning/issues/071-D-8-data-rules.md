---
title: "D-8: Data rules: PII removal before storage, retention period, deletion on request"
labels: ["story", "priority:P1", "phase:4-golden-set", "area:data", "size:M", "layer:platform"]
milestone: "W6 Golden set and evaluator SLM"
index: 71
epic_id: D-8
depends_on: ["040 D-0", "069 D-7", "065 D-2", "068 D-3"]
blocks: []
epic_refs: [E.5]
---

## Why

Records and golden sets hold real text, so they must follow data rules: no PII in storage, a retention period, and deletion on request. These rules are part of the Phase 4 done-when and are evidence for ISO 42001 A.7 and AI Act Art. 10. It comes after versions (069 D-7), because deletion has to work with immutable versions.

## What

- One PII removal step (the chassis redaction from 022 H-6) before every write: recorder records, imports (068 D-3), and review edits (065 D-2).
- A PII scan before each lakeFS commit. A version with unredacted PII is not committed.
- Retention periods per store in config (suggested key: `retention_days`), with a daily job that deletes records past their period.
- Deletion on request by record ID or subject identifier, across the records tables, the golden set tables, and the current lakeFS version.
- Suggested: past lakeFS versions that held deleted data are marked withdrawn, and their objects are removed by lakeFS garbage collection.
- Suggested: MLflow models trained on a withdrawn version are flagged.
- Each deletion writes a deletion report: who asked, what scope, when, and which stores changed, without the deleted content.

## Out of scope

- Backups and their retention (121 X-4).
- Audit log retention per risk class (072 C-3).
- Setting the retention periods. Legal decides them; the platform applies them.

## Acceptance criteria

- [ ] A PII fixture (email, phone number, name) sent through the recorder, an import, and a review edit is redacted in every store.
- [ ] A lakeFS commit that holds unredacted PII is blocked.
- [ ] With a short test retention period, the daily job deletes expired records and keeps the rest.
- [ ] A deletion request removes all matching data from Postgres and the current lakeFS version, and a search afterward finds none.
- [ ] Past versions that held the data are marked withdrawn, and their objects are gone after garbage collection.
- [ ] Each deletion leaves a report that holds none of the deleted content.
- [ ] Phase 4 done-when (PII rules): tests show the PII rules apply on every import, review, and export path.

## Dependencies

- Depends on: [040 D-0](040-D-0-recorder-agent.md), [069 D-7](069-D-7-lakefs-versions.md), [065 D-2](065-D-2-review-ui.md), [068 D-3](068-D-3-import.md)
- Blocks: none

## References

- Epic story: [D-8 in Phase 4](../slm-agent-platform-epic-v3.md#phase-4-golden-set-agent-and-evaluator-slm)
- Epic context: [E.5](../slm-agent-platform-epic-v3.md#e5)
- Backlog plan: [000-plan.md](000-plan.md)
