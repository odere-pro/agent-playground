---
title: "A-3: Result events with source, stored by the recorder"
labels: ["story", "priority:P0", "phase:3-simplifier-agent", "area:agent", "size:S", "layer:platform"]
milestone: "W4 Simplifier agent live"
index: 43
epic_id: A-3
depends_on: ["019 H-17", "040 D-0", "041 A-1"]
blocks: ["045 A-4"]
epic_refs: [B.4, D.3]
---

## Why

Every simplifier call must become a record, so the golden set can grow. The `source` field says which model produced the original text, which matters because provider terms can restrict training data. This is the last P0 story: with it, the Phase 3 done-when is met and the first savings are measured. It comes after the recorder (040 D-0), which stores the events.

## What

- Switch on the `result_events` module, with `events.produces: [agents.task.completed.v1, agents.task.failed.v1]`.
- Each call sends one CloudEvents result event through the event port (019 H-17): input, output, metrics (`facts_kept`, attempts, tokens), versions, `idempotency_key`, and `source`.
- Events carry the `traceparent`, `idempotencykey`, `configversion`, and `modelroute` extensions.
- `source` names the model that produced the original text. Suggested: an optional `source` field in the input, with `unknown` as the default. The route that produced the simplified output stays in `versions`.
- Failed calls send `agents.task.failed.v1`. The event is one way: the simplifier has no read path to records.
- Suggested: a failed publish is retried and counted, and never changes the response.
- Simplifier traffic is tagged by agent in the router, so the savings dashboard (006 G-6) shows it.

## Out of scope

- The AsyncAPI spec for these events (052 H-21).
- Dead-letter topics and backoff (053 H-22).
- History swap on top of these records (044 G-4, 045 A-4).
- Building golden sets from records (064 D-1).

## Acceptance criteria

- [ ] Each call through the native, OpenAI, or Anthropic API, or through an event, sends exactly one result event with all listed fields.
- [ ] The recorder stores one record per call, with `source` set.
- [ ] A repeated call with the same `idempotency_key` returns the same result and leaves one record.
- [ ] The simplifier's workload container holds no credential at all. Only the chassis holds the broker credential.
- [ ] Phase 3 done-when: one end-to-end test in Docker Compose calls the agent through each API, checks that it keeps no state, repeats a call with the same key, finds one trace that covers the call and the recorder's write, finds the record, and forces a fallback.
- [ ] The savings dashboard shows simplifier tokens on `simplifier-slm` and the big-model tokens saved against the G-3 baseline.

## Dependencies

- Depends on: [019 H-17](019-H-17-event-port.md), [040 D-0](040-D-0-recorder-agent.md), [041 A-1](041-A-1-simplifier-core.md)
- Blocks: [045 A-4](045-A-4-history-swap-on.md)

## References

- Epic story: [A-3 in Phase 3](../slm-agent-platform-epic-v3.md#phase-3-simplifier-agent)
- Epic context: [B.4](../slm-agent-platform-epic-v3.md#b4) · [D.3](../slm-agent-platform-epic-v3.md#d3)
- Backlog plan: [000-plan.md](000-plan.md)
