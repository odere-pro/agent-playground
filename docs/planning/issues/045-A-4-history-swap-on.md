---
title: "A-4: History swap switched on for the simplifier"
labels: ["story", "priority:P1", "phase:3-simplifier-agent", "area:agent", "size:S", "layer:platform"]
milestone: "W4 Simplifier agent live"
index: 45
epic_id: A-4
depends_on: ["043 A-3", "044 G-4"]
blocks: []
epic_refs: [J]
---

## Why

This turns the history swap (044 G-4) on for real traffic, so the savings from short chat history become real and measurable. It comes after result events (043 A-3), because the short versions it uses are recorded simplifier results. It is P1: the first savings come from the simplifier itself, and this adds a second lever.

## What

- Switch on `harness.history_swap` in the config store for the first agent that sends multi-turn history to a big-model route. Suggested: the agent with the most chat traffic in the G-3 baseline.
- Suggested: after a long big-model answer, that agent's chassis sends it to the simplifier as a one-way `agents.task.requested.v1` event, so a short version exists by the next turn and the user's answer is not delayed.
- The request sets `source` to the big model that produced the answer, so the record says where the original came from.
- Measure the saving by replaying recorded conversations with the swap off and on, using the router's token counts.
- History-swap savings get their own line on the savings dashboard (006 G-6).
- The simplifier calls made for the swap are tagged, so their cost is counted against the saving.
- Rollback: switch the swap off by config.

## Out of scope

- The swap mechanics (044 G-4).
- Switching the swap on for every agent; each agent opts in by config later.
- Session context at scale (125 L-1).

## Acceptance criteria

- [ ] The swap is on for the pilot agent as a new config version, and switching it off by config restores the old behavior with no restart.
- [ ] In a scripted test, a long big-model answer gets a recorded short version, with `source` set to that model, before the next turn.
- [ ] The next turn's model call carries the short version.
- [ ] A replay of at least 50 recorded conversations (suggested size) uses fewer input tokens with the swap on, by the router counts.
- [ ] The saving, net of the simplifier calls it needed, shows as its own line on the savings dashboard.

## Dependencies

- Depends on: [043 A-3](043-A-3-result-events.md), [044 G-4](044-G-4-history-swap.md)
- Blocks: none

## References

- Epic story: [A-4 in Phase 3](../slm-agent-platform-epic-v3.md#phase-3-simplifier-agent)
- Epic context: [J](../slm-agent-platform-epic-v3.md#app-j)
- Backlog plan: [000-plan.md](000-plan.md)
