---
title: "X-4: Backups and a tested restore for Postgres, object storage, and golden sets"
labels: ["story", "priority:P1", "phase:8-production", "area:infra", "size:M", "layer:platform"]
milestone: "W10 Production readiness"
index: 121
epic_id: X-4
depends_on: ["038 X-1a"]
blocks: []
epic_refs: [E.6]
---

## Why

Records, golden sets, and the audit log are the platform's training data and its compliance evidence; losing them loses both. A backup only counts once a restore has been tested. This issue delivers the Phase 8 done-when part "a restore test passes". With the first cloud (038 X-1a) and SLO alerts (117 X-3) in place, it closes the Phase 8 done-when. The product plan needs no cloud before phase F.

## What

- Postgres (records, registry, long-term memory, and Temporal): CloudNativePG backups to object storage, with point-in-time recovery.
- Object storage (config store, datasets, model weights): bucket versioning plus a copy in a second location (suggested: another region).
- Golden sets live in git (`packages/eval-kit/src/eval_kit/golden`), so they restore from git.
- Audit log: backup copies keep object lock and the retention set in the `governance` block.
- Suggested targets, to confirm: RPO 24 hours, RTO 4 hours.
- A restore runbook, and a restore test on a schedule (suggested: every quarter), with results kept as evidence.

## Out of scope

- Runbooks for other failures (119 X-6).
- How deletion on request applies to backups, which follows the data rules (071 D-8).
- Disaster recovery across clouds.
- Golden-set versions in lakeFS (069 D-7). The product keeps its golden sets in git.

## Acceptance criteria

- [ ] Scheduled backups run for every store in the list, and a failed backup fires an alert.
- [ ] A restore test on staging brings back Postgres to a point in time and a config store version.
- [ ] Row counts and checksums match between the source and the restore.
- [ ] The restored audit log still has object lock and its retention.
- [ ] The restore test passes on staging on the first cloud (Phase 8 done-when).

## Dependencies

- Depends on: [038 X-1a](038-X-1a-terraform-first-cloud.md)
- Blocks: none

## References

- Epic story: [X-4 in Phase 8](../slm-agent-platform-epic-v3.md#phase-8-production-readiness)
- Epic context: [E.6](../slm-agent-platform-epic-v3.md#e6)
- Backlog plan: [000-plan.md](000-plan.md)
