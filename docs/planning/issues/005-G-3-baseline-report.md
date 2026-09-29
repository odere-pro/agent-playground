---
title: "G-3: Baseline report: token spend and cost over at least two weeks"
labels: ["story", "priority:P0", "phase:1-router", "area:router", "size:M", "layer:platform"]
milestone: "W1 Router and baseline"
index: 5
epic_id: G-3
depends_on: ["004 G-2"]
blocks: ["031 G-5"]
epic_refs: [J, success-metrics]
---

## Why

Every savings claim in the epic is measured against this baseline: big-model token spend −30%, and cost per 1,000 requests below the baseline. Collection starts on day one of 004 G-2; the report itself is written after at least two weeks of data. The report also makes the draft success-metric targets from 001 DEC-1 final, and it feeds the break-even check (031 G-5).

## What

- Router usage from 004 G-2, collected from the day it ships, for at least 14 days of real traffic.
- Totals: big-model input and output tokens, cost, requests, and cost per 1,000 requests.
- A breakdown per agent or service, per user, and per model route, with a daily trend.
- An estimate of the spend that could move to small models: narrow text-in, text-out tasks such as simplification, and long past answers sent again in chat history.
- A list of data gaps: untagged calls, services not yet behind the router, and days with no traffic.
- The queries behind every number, saved next to the report so it can be re-run.
- A review of the success-metric targets (marked "to confirm in Phase 1" in the epic), with each one confirmed or changed.

## Out of scope

- Collecting the usage data (004 G-2).
- The live dashboard (006 G-6).
- GPU hosting cost and the break-even point (031 G-5).

## Acceptance criteria

- [ ] The report covers at least 14 days in a row of router data, with start and end dates.
- [ ] It shows total big-model tokens, total cost, and cost per 1,000 requests.
- [ ] It breaks spend down per agent or service, per user, and per model route.
- [ ] Data gaps are listed with their share of total spend.
- [ ] The saved queries reproduce every number in the report.
- [ ] The success-metric targets are marked final, and the draft targets in 001 DEC-1 are updated to match.

## Dependencies

- Depends on: [004 G-2](004-G-2-token-cost-counting.md)
- Blocks: [031 G-5](031-G-5-cost-model-break-even.md)

## References

- Epic story: [G-3 in Phase 1](../slm-agent-platform-epic-v3.md#phase-1-baseline-and-llm-router)
- Epic context: [J](../slm-agent-platform-epic-v3.md#app-j) · [Success metrics](../slm-agent-platform-epic-v3.md#success-metrics)
- Backlog plan: [000-plan.md](000-plan.md)
