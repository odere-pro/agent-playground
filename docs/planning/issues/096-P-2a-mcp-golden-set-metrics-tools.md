---
title: "P-2a: MCP golden set and metrics tools"
labels: ["story", "priority:P1", "phase:6-mcp-server", "area:mcp", "size:M", "layer:platform"]
milestone: "W8 Platform MCP server"
index: 96
epic_id: P-2a
depends_on: ["006 G-6", "070 D-4", "095 P-4", "046 A-5"]
blocks: []
epic_refs: [F.4]
---

## Why

The Phase 6 done-when asks that an MCP client export a golden set version end to end. This issue is split from P-2 for that reason: the golden set and metrics tools ship now, while the run, memory, config, and workflow tools wait for the orchestrator (114 P-2b). It comes after 095 P-4, because import and review decisions are write tools.

## What

- `golden_versions`: the immutable dataset versions (069 D-7), with version ID, record count, splits, and date.
- `golden_export`: exports a version ID in JSONL, CSV, Parquet, or Hugging Face datasets format (070 D-4). Suggested: it returns a short-lived download link, not the file inline.
- `golden_import`: imports from a file reference or bucket path through the golden set agent's import (068 D-3), with the same schema check and dedup (067 D-5).
- `review_list` and `review_decide`: the review queue, and approve, reject, or edit, with the same rules and access control as the review UI (065 D-2).
- `metrics_get`: tokens saved, cost, and pass rate per agent and time range, from the same data as the savings dashboard (006 G-6).
- `golden_import` and `review_decide` are write tools with confirmation and audit (095 P-4); the others are read tools.
- Tools generated from the OpenAPI spec (051 H-13) and forwarded to the golden set agent.

## Out of scope

- Run, memory, config, and workflow tools (114 P-2b).
- Read-only resources for dataset versions (097 P-3).

## Acceptance criteria

- [ ] `golden_export` of one version in each of the four formats gives the same records and record count as the direct export from 070 D-4.
- [ ] Exporting the same version ID twice gives the same records.
- [ ] `golden_import` and `review_decide` are refused without the write scope, ask for confirmation, and are audited.
- [ ] A `review_decide` call records the reviewer, the same as a decision made in the review UI.
- [ ] `metrics_get` numbers match the savings dashboard for the same agent and period.
- [ ] Phase 6 done-when: in one MCP client session, a user searches the registry, runs the simplifier, and exports a golden set version; the steps run as an end-to-end test.

## Dependencies

- Depends on: [006 G-6](006-G-6-savings-dashboard.md), [070 D-4](070-D-4-export-formats.md), [095 P-4](095-P-4-mcp-scopes-audit.md), [046 A-5](046-A-5-agent-dashboard.md)
- Blocks: none

## References

- Epic story: [P-2a in Phase 6](../slm-agent-platform-epic-v3.md#phase-6-platform-mcp-server)
- Epic context: [F.4](../slm-agent-platform-epic-v3.md#f4)
- Backlog plan: [000-plan.md](000-plan.md)
