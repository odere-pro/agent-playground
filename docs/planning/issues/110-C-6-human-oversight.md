---
title: "C-6: Human oversight controls: review queue, approval steps, stop control, all audited"
labels: ["story", "priority:P1", "phase:9-governance", "area:governance", "size:M", "layer:platform"]
milestone: "W9 Orchestrator"
index: 110
epic_id: C-6
depends_on: ["065 D-2", "072 C-3", "109 O-8"]
blocks: ["115 O-15"]
epic_refs: [E.5, Fig.3]
---

## Why

Human oversight must be a set of real controls with a record: AI Act Art. 14 and ISO 42001 A.9 ask for approval steps, a review queue, and a stop control, with decisions kept as evidence. This issue is pulled forward from Phase 9, because it is built together with the orchestrator's approval and cancel steps (109 O-8). It connects them to the Review UI and the audit log.

## What

- Approval requests go out as `oversight.review.required.v1` events and show in the Review UI queue next to golden set reviews.
- A reviewer's decision goes out as `oversight.review.decided.v1`. The orchestrator consumes it and resumes or stops the run.
- A stop control in the Review UI for a running run, which calls the orchestrator cancel.
- The `governance.human_oversight` value is enforced: `approve_every_run` adds an approval before a run that uses the agent returns its result; `review_on_low_score` sends results below the evaluator threshold to the review queue; `none` adds nothing.
- Every request, decision, and stop is recorded in the audit log, with reviewer, time, `run_id`, and reason.
- Only users with the reviewer scope can decide or stop.

## Out of scope

- Approval and cancel mechanics in the engine (109 O-8).
- The MCP tools `review_list`, `review_decide` (096 P-2a), and `run_cancel` (114 P-2b).
- Incident handling (118 C-7).

## Acceptance criteria

- [ ] An approval request from a run shows in the Review UI queue.
- [ ] Approving in the UI resumes the run; rejecting stops it.
- [ ] Stop in the UI cancels a running run, and no further step starts.
- [ ] A run that uses an `approve_every_run` agent cannot return a result without a recorded approval.
- [ ] A below-threshold result from a `review_on_low_score` agent lands in the review queue.
- [ ] An audit log export for the run shows each request, decision, and stop, with reviewer and time.
- [ ] A user without the reviewer scope cannot decide or stop.

## Dependencies

- Depends on: [065 D-2](065-D-2-review-ui.md), [072 C-3](072-C-3-audit-log-agent.md), [109 O-8](109-O-8-cancel-approval.md)
- Blocks: [115 O-15](115-O-15-save-planned-run.md)

## References

- Epic story: [C-6 in Phase 9](../slm-agent-platform-epic-v3.md#phase-9-governance-and-compliance)
- Epic context: [E.5](../slm-agent-platform-epic-v3.md#e5) · [Fig. 3](../slm-agent-platform-epic-v3.md#fig3)
- Backlog plan: [000-plan.md](000-plan.md)
