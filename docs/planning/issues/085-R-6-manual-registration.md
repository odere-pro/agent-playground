---
title: "R-6: Manual registration: API, UI, and YAML import"
labels: ["story", "priority:P1", "phase:5-registry", "area:registry", "size:M", "layer:platform"]
milestone: "W7 Registry and governance gate"
index: 85
epic_id: R-6
depends_on: ["080 R-7"]
blocks: []
epic_refs: [F.3]
---

## Why

Not everything can register itself. External tools, SaaS APIs, and older services need a way in by hand. Manual registration covers that through an API, a UI, and YAML import, and every manual entry goes through approval (Phase 5 done-when). It comes right after trust levels (080 R-7), which define that approval.

## What

- The register call for hand-made entries (the API behind `registry_register` in F.4). Entries get `trust: manual` and `status: pending`.
- YAML import of one or many entries in one file, checked against the entry JSON Schema, with the file position of each error.
- Suggested: YAML import is all or nothing, with a dry-run mode that shows what would change.
- A simple UI: a form to register an entry, and a queue page to approve or reject pending entries through the 080 R-7 API. Suggested: the same stack and login as the review UI (065 D-2).
- Scanning (081 R-8) and hash pinning (082 R-9) run on manual entries, and the UI shows the scan findings.
- Importing the same file again creates no duplicates; a changed description goes back to review.
- Write actions need the write scope, and approval needs the approve scope.

## Out of scope

- Importing MCP servers (087 R-4) and OpenAPI specs (088 R-5).
- Automatic registration (083 R-2, 084 R-3).

## Acceptance criteria

- [ ] An entry registered through the API is `manual` and `pending`, and does not show in list results until approved.
- [ ] A YAML file with 10 entries imports in one call; a file with one bad entry imports nothing and reports that entry's line.
- [ ] Dry run shows the changes and writes nothing.
- [ ] Importing the same file again creates no new entries and no review items.
- [ ] In the UI, one user registers an entry and a second user approves it; both actions appear in `registry.entry.changed.v1` events.
- [ ] A user without the write scope gets a 403 from the API and cannot open the form.
- [ ] A flagged entry shows its scan findings on the queue page.

## Dependencies

- Depends on: [080 R-7](080-R-7-trust-levels.md)
- Blocks: none

## References

- Epic story: [R-6 in Phase 5](../slm-agent-platform-epic-v3.md#phase-5-service-registry)
- Epic context: [F.3](../slm-agent-platform-epic-v3.md#f3)
- Backlog plan: [000-plan.md](000-plan.md)
