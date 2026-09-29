---
title: "R-13: Registry search test set of at least 200 queries with expected results"
labels: ["story", "priority:P1", "phase:5-registry", "area:registry", "size:M", "layer:platform"]
milestone: "W7 Registry and governance gate"
index: 90
epic_id: R-13
depends_on: ["076 R-1"]
blocks: ["091 R-11"]
epic_refs: [I, success-metrics]
---

## Why

The epic's target is the correct main tool in the top 3 for at least 90% of queries. Search cannot be tuned toward a target without a fixed test set. This issue is placed before hybrid search (091 R-11), so search is built against the test set from the start. Appendix I also asks to pick the embedding model with the registry's own queries, and this set is where they come from.

## What

- At least 200 queries, each with the expected main entry and any other acceptable entries.
- Queries in each style: task words, synonyms, exact names, misspellings, queries with filters, and queries that should return nothing.
- Queries for every entry type: agent, tool, API, and MCP.
- A fixture catalog to search against (suggested: at least 300 entries), with near-duplicates, fallback pairs, and inactive entries.
- Suggested: some queries taken from real task names in agent and workflow configs.
- A scoring script: top-3 accuracy overall and per group (suggested: also recall at 5 and MRR).
- A held-out part (suggested: 20%) that is never used for tuning.
- A baseline score from plain keyword matching, for 091 R-11 to beat.
- Versioned in the repo and run in CI.

## Out of scope

- The search itself (091 R-11) and the result shape (092 R-12).
- Load tests for the registry (122 X-5).

## Acceptance criteria

- [ ] The set has at least 200 queries, each with at least one expected entry, and a second person has reviewed it.
- [ ] Each query style and entry type has at least 10 queries, with counts shown in the scoring report.
- [ ] The fixture catalog loads into an empty registry with one command.
- [ ] The scoring script reports top-3 accuracy overall and per group, and flags a result below 90%.
- [ ] The keyword baseline score is recorded in this issue.
- [ ] The held-out part is fixed and marked, and the script can score it alone.
- [ ] CI runs the scoring script on every registry change.

## Dependencies

- Depends on: [076 R-1](076-R-1-registry-data-model-api.md)
- Blocks: [091 R-11](091-R-11-hybrid-search.md)

## References

- Epic story: [R-13 in Phase 5](../slm-agent-platform-epic-v3.md#phase-5-service-registry)
- Epic context: [I](../slm-agent-platform-epic-v3.md#app-i) · [Success metrics](../slm-agent-platform-epic-v3.md#success-metrics)
- Backlog plan: [000-plan.md](000-plan.md)
