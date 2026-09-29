---
title: "R-9: Description hash pinning: a changed description goes back to review"
labels: ["story", "priority:P1", "phase:5-registry", "area:registry", "size:S", "layer:platform"]
milestone: "W7 Registry and governance gate"
index: 82
epic_id: R-9
depends_on: ["080 R-7"]
blocks: ["087 R-4"]
epic_refs: [F.3, R5]
---

## Why

A tool can pass review and then change its description later, a known MCP attack [R5]. Pinning the description hash means any change after approval goes back to review, which the Phase 5 done-when asks for ("changed descriptions are caught"). It uses the `description_hash` from the data model (076 R-1) and the approval flow from trust levels (080 R-7).

## What

- On activation, the registry pins the entry's `description_hash`, with who approved it and when.
- The hash covers the `description` and the field descriptions in the input and output schema, as defined in 076 R-1.
- The check runs on every update of the same `name` and `version`: re-registration, re-import, or a manual edit.
- A changed hash moves the entry to `pending` and out of list results until someone approves it again.
- A new version is a new entry, so it goes through normal registration and scanning (080 R-7, 081 R-8).
- The review item shows the old and new text side by side.
- Approving pins the new hash. Every change and decision publishes `registry.entry.changed.v1`.

## Out of scope

- Re-reading `tools/list` from MCP servers, which calls this check (087 R-4).
- Re-reading OpenAPI specs (088 R-5).
- The approval UI (085 R-6).

## Acceptance criteria

- [ ] An activated entry has a pinned hash, an approver, and a time.
- [ ] Re-registering the same name and version with one changed character in the description moves the entry to `pending` and out of list results.
- [ ] A change in one schema field description does the same.
- [ ] Re-registering with no change keeps the entry `active` and makes no review item.
- [ ] The review item shows both versions of the text.
- [ ] Approving the change pins the new hash, and the entry goes live again.

## Dependencies

- Depends on: [080 R-7](080-R-7-trust-levels.md)
- Blocks: [087 R-4](087-R-4-mcp-import.md)

## References

- Epic story: [R-9 in Phase 5](../slm-agent-platform-epic-v3.md#phase-5-service-registry)
- Epic context: [F.3](../slm-agent-platform-epic-v3.md#f3) · [R5](../slm-agent-platform-epic-v3.md#r5)
- Backlog plan: [000-plan.md](000-plan.md)
