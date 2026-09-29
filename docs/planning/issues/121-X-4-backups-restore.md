---
title: "X-4: Backups and a tested restore for Postgres, object storage, and golden sets"
labels: ["story", "priority:P1", "phase:8-production", "area:infra", "size:M", "layer:platform"]
milestone: "W10 Production readiness"
index: 121
epic_id: X-4
depends_on: ["038 X-1a", "069 D-7"]
blocks: []
epic_refs: [E.6]
---

## Why

Records, golden sets, and the audit log are the platform's training data and its compliance evidence; losing them loses both. A backup only counts once a restore has been tested. This issue delivers the Phase 8 done-when part "a restore test passes". With both clouds (116 X-1b) and SLO alerts (117 X-3) already in place, it closes the Phase 8 done-when.

## What

- Postgres (records, registry, long-term memory, Temporal, and lakeFS metadata): CloudNativePG backups to object storage, with point-in-time recovery.
- Object storage (config store, datasets, model weights): bucket versioning plus a copy in a second location (suggested: another region).
- Golden sets: lakeFS repositories and their storage, so every dataset version ID can be restored.
- Audit log: backup copies keep object lock and the retention set in the `governance` block.
- Suggested targets, to confirm: RPO 24 hours, RTO 4 hours.
- A restore runbook, and a restore test on a schedule (suggested: every quarter), with results kept as evidence.

## Out of scope

- Runbooks for other failures (119 X-6).
- How deletion on request applies to backups, which follows the data rules (071 D-8).
- Disaster recovery across clouds.

## Acceptance criteria

- [ ] Scheduled backups run for every store in the list, and a failed backup fires an alert.
- [ ] A restore test on staging brings back Postgres to a point in time, a config store version, and a golden set version.
- [ ] Row counts and checksums match between the source and the restore.
- [ ] The restored golden set exports with the same version ID and checksum as before.
- [ ] The restored audit log still has object lock and its retention.
- [ ] The restore test passes on staging on both clouds (Phase 8 done-when).

## Dependencies

- Depends on: [038 X-1a](038-X-1a-terraform-first-cloud.md), [069 D-7](069-D-7-lakefs-versions.md)
- Blocks: none

## References

- Epic story: [X-4 in Phase 8](../slm-agent-platform-epic-v3.md#phase-8-production-readiness)
- Epic context: [E.6](../slm-agent-platform-epic-v3.md#e6)
- Backlog plan: [000-plan.md](000-plan.md)
