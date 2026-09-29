---
title: "D-1: Golden set agent: reads records, scores them with a judge, sends uncertain ones to review"
labels: ["story", "priority:P1", "phase:4-golden-set", "area:data", "size:M", "layer:platform"]
milestone: "W6 Golden set and evaluator SLM"
index: 64
epic_id: D-1
depends_on: ["017 H-4", "040 D-0", "042 A-2"]
blocks: ["065 D-2", "066 D-6", "067 D-5", "069 D-7"]
epic_refs: [B.1, B.4]
---

## Why

Records alone are not a golden set. The golden set agent turns recorded calls into scored, reviewed data that trains better models and evaluators, which is how the platform gets better over time. It builds on the recorder (040 D-0) and opens the golden set wave, since review, tags, import, and versions all build on its store.

## What

- A data agent (class `data`, kind `data`) built from the template. It owns its golden set tables in Postgres, and only it writes them.
- It reads new records written by the recorder, with read-only access to the records tables.
- It scores each record with the judge through `EvaluatorPort` (the `evaluator-facts` agent, a big-model judge until 075 E-3), plus the S-3 metrics (028 S-3).
- Suggested: thresholds in config split scores into confident pass, confident fail, and an uncertain band in between.
- Uncertain records publish `oversight.review.required.v1`, written through a transactional outbox.
- It consumes `oversight.review.decided.v1` and sets the record to approved or rejected.
- Suggested record states: `new`, `scored`, `in_review`, `approved`, `rejected`.
- Records whose `source` is not cleared for training are flagged, so they never reach a training set.
- Metrics: records scored, share sent to review, and judge cost per record.

## Reuse

- **Use:** Langfuse datasets (versioned), LLM-as-judge evaluators, and annotation queues. All three are in the MIT self-hosted edition.
- **Build:** the glue: copy recorder records into Langfuse, send uncertain scores to a queue, and publish the review events.
- **Decision:** confirm Langfuse as the golden set workspace at the start of this issue. If not, keep the design in What.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- The review UI (065 D-2).
- Tags and splits (066 D-6), import (067 D-5, 068 D-3), and versions and export (069 D-7, 070 D-4).
- The evaluator SLM (073 E-1, 075 E-3).

## Acceptance criteria

- [ ] New simplifier records are scored, and each gets a score and a state.
- [ ] Each record in the uncertain band publishes one `oversight.review.required.v1` event; confident records publish none.
- [ ] A test `oversight.review.decided.v1` event sets the record to approved or rejected, and a duplicate decision changes nothing.
- [ ] Scoring the same record twice leaves one golden set entry.
- [ ] Only the golden set agent can write the golden set tables, and it cannot write the records tables.
- [ ] A record's state change and its review event are written together: a crash between them loses neither.
- [ ] The share sent to review and the judge cost per record show as metrics.

## Dependencies

- Depends on: [017 H-4](017-H-4-harness-features.md), [040 D-0](040-D-0-recorder-agent.md), [042 A-2](042-A-2-evaluator-gate-fallback.md)
- Blocks: [065 D-2](065-D-2-review-ui.md), [066 D-6](066-D-6-tags-splits.md), [067 D-5](067-D-5-import-schema-dedup.md), [069 D-7](069-D-7-lakefs-versions.md)

## References

- Epic story: [D-1 in Phase 4](../slm-agent-platform-epic-v3.md#phase-4-golden-set-agent-and-evaluator-slm)
- Epic context: [B.1](../slm-agent-platform-epic-v3.md#b1) · [B.4](../slm-agent-platform-epic-v3.md#b4)
- Backlog plan: [000-plan.md](000-plan.md)
