---
title: "D-2: Review UI: approve, reject, edit, with access control and an audit log"
labels: ["story", "priority:P1", "phase:4-golden-set", "area:data", "size:M", "layer:platform"]
milestone: "W6 Golden set and evaluator SLM"
index: 65
epic_id: D-2
depends_on: ["022 H-6", "064 D-1"]
blocks: ["071 D-8", "073 E-1", "110 C-6"]
epic_refs: [E.5, D.3]
---

## Why

Uncertain records need a person to decide, and those decisions become the human labels that train and test the evaluator SLM (073 E-1, 074 E-2). Reviews are also compliance evidence, so every action needs access control and a record of who did what. It builds on the golden set agent (064 D-1) and the security middleware (022 H-6).

## What

- A review queue in Langfuse annotation queues (see Reuse), or Label Studio if roles are not enough. It shows input, output, scores, `source`, and versions.
- Actions: approve, reject, and edit (change the output, then approve). The original output is kept next to the edit.
- The reviewer also sets a human `facts_kept` label (kept or not kept) on each record.
- Access control: OAuth login, with read and write scopes from the security middleware. Suggested roles: reviewer and admin.
- Each action publishes `oversight.review.decided.v1` with the reviewer ID, decision, label, reason, and a diff for edits.
- An append-only review history (who, when, what changed) that the UI and API cannot edit or delete.
- Records are shown as stored, already redacted.
- Suggested: when two reviewers act on the same record, the first decision wins and the second sees a conflict.

## Reuse

- **Use:** Langfuse annotation queues as the review UI. Label Studio (Apache 2.0) if richer edit screens or stricter roles are needed.
- **Build:** SSO login and roles, the bridge that publishes `oversight.review.decided.v1`, and the append-only review history.
- **Scope change:** no custom web UI.
- **Watch:** check which role and audit controls the MIT self-hosted Langfuse includes. Our sources disagree; if they are not enough, use Label Studio.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- The central audit log (072 C-3), which also consumes the decision events.
- Review and approval steps for orchestrator runs (110 C-6).
- Tags and splits (066 D-6).

## Acceptance criteria

- [ ] A reviewer opens the queue and approves, rejects, or edits a record, and the golden set agent updates its state.
- [ ] Each action publishes exactly one `oversight.review.decided.v1` event with all listed fields.
- [ ] A user without the reviewer scope can neither see nor decide on records.
- [ ] Review history entries cannot be changed or deleted through the UI or the API.
- [ ] An edited record keeps both the original and the edited output.
- [ ] Two reviewers deciding the same record at once produce one decision.

## Dependencies

- Depends on: [022 H-6](022-H-6-security-middleware.md), [064 D-1](064-D-1-golden-set-agent.md)
- Blocks: [071 D-8](071-D-8-data-rules.md), [073 E-1](073-E-1-evaluator-slm.md), [110 C-6](110-C-6-human-oversight.md)

## References

- Epic story: [D-2 in Phase 4](../slm-agent-platform-epic-v3.md#phase-4-golden-set-agent-and-evaluator-slm)
- Epic context: [E.5](../slm-agent-platform-epic-v3.md#e5) · [D.3](../slm-agent-platform-epic-v3.md#d3)
- Backlog plan: [000-plan.md](000-plan.md)
