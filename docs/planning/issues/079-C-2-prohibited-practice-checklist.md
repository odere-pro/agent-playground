---
title: "C-2: Prohibited-practice checklist at registration"
labels: ["story", "priority:P1", "phase:9-governance", "area:governance", "size:S", "layer:platform"]
milestone: "W7 Registry and governance gate"
index: 79
epic_id: C-2
depends_on: ["077 C-1"]
blocks: []
epic_refs: [E.4, R10]
---

## Why

The EU AI Act bans some AI practices outright, and the platform must catch them before an agent goes live. A prohibited-practice checklist is part of registration, and a failed check blocks activation (E.4). This issue is pulled forward from Phase 9 to sit next to the governance gate (077 C-1), so both checks exist before automatic registration starts.

## What

- A versioned checklist file in the config store (suggested: `governance/prohibited-practices.yaml`), with one yes or no question per prohibited practice in AI Act Article 5. The legal and compliance owner owns the wording.
- Answers per agent, kept in the config store next to the agent config (suggested: `governance/checklists/<agent>.yaml`), so automatic registration can read them.
- The registry checks the answers at registration. Missing answers, or answers to an older checklist version, block activation.
- Any "yes" answer sets `risk_class: prohibited_check_failed` on the entry, and the gate (077 C-1) blocks it.
- Each result stores who answered, when, and the checklist version, and is published in `registry.entry.changed.v1`, so the audit log keeps it.
- A new checklist version lists the active agents that need new answers.

## Out of scope

- The other governance rules on activation (077 C-1).
- Deciding whether a use case is prohibited. Legal makes that decision; the platform records it.
- The control mapping report (124 C-9).

## Acceptance criteria

- [ ] The checklist file is versioned, and its sign-off by the legal and compliance owner is recorded in this issue.
- [ ] An agent with no answers, or with answers to an older checklist version, stays `pending`.
- [ ] An agent with one "yes" answer gets `risk_class: prohibited_check_failed` and is not activated; a test covers each question.
- [ ] An agent with all "no" answers moves on to the other gate checks.
- [ ] The answers, the person, the date, and the checklist version show on the entry and in the inventory (078 R-16).
- [ ] Publishing a new checklist version lists every active agent that needs new answers.

## Dependencies

- Depends on: [077 C-1](077-C-1-governance-enforcement.md)
- Blocks: none

## References

- Epic story: [C-2 in Phase 9](../slm-agent-platform-epic-v3.md#phase-9-governance-and-compliance)
- Epic context: [E.4](../slm-agent-platform-epic-v3.md#e4) · [R10](../slm-agent-platform-epic-v3.md#r10)
- Backlog plan: [000-plan.md](000-plan.md)
