---
title: "D-0: Recorder agent: reads result events, removes PII, drops duplicates, writes records to Postgres"
labels: ["story", "priority:P0", "phase:4-golden-set", "area:data", "size:M", "layer:platform"]
milestone: "W4 Simplifier agent live"
index: 40
epic_id: D-0
depends_on: ["008 H-14", "019 H-17", "020 X-8", "021 H-20", "022 H-6"]
blocks: ["043 A-3", "044 G-4", "064 D-1", "071 D-8", "127 L-3"]
epic_refs: [B.4, D.1, D.3]
---

## Why

Stateless agents never store anything. The recorder is the data agent that turns their result events into records. It is pulled forward from Phase 4 because the simplifier agent's done-when says every call is recorded by the recorder. It is also the first data agent, so it proves the pattern the golden set agent and the audit log reuse.

## What

- A data agent built on the chassis, from the service template (class `data`, kind `data`), with the same interface and config schema as every agent.
- Consumes `agents.task.completed.v1` and `agents.task.failed.v1` through the event consumer adapter.
- Removes PII before anything is stored, using the chassis redaction (022 H-6). There is no SDK: the workload does not redact in its own code.
- Drops duplicates by event `id` and `idempotency_key`, because delivery is at least once.
- Writes one record per call to Postgres: input, output, metrics, versions, `idempotency_key`, `source` (which model produced the original text, filled by A-3), `trace_id`, and timestamps.
- Uses a transactional outbox for any event it publishes, so a record and its event are written together.
- Only the recorder has write access to the records tables, and only its chassis holds the store credential. suggested: the chassis exposes the records store to its workload as MCP tools through the tool proxy. This is gap (a) in [the gaps in the backlog plan](000-plan.md#adr-001-follow-ups). The tool proxy is part of 054 H-16, which comes later, so the order is part of that gap.
- Runs in Docker Compose and on Kubernetes (Postgres on CloudNativePG).

## Reuse

- **Use:** Postgres on CloudNativePG as the record store, per the epic. suggested: also link each record to its Langfuse trace, so review and datasets can use it (see 064 D-1).
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Scoring records and building datasets (D-1).
- Retention and deletion on request (D-8).
- The audit log (C-3), which is a separate data agent.

## Acceptance criteria

- [ ] A `task.completed` event from the echo agent becomes one row in Postgres with all the listed fields.
- [ ] Sending the same event twice, or two events with the same `idempotency_key`, stores one record.
- [ ] A PII test fixture (email, phone number, name) is redacted in the stored record.
- [ ] Records carry the `source` field.
- [ ] No other agent's database role can write to the records tables.
- [ ] The recorder's workload container holds no store credential. Only its chassis does.
- [ ] An event is acknowledged only after its record is stored, so a failed write is redelivered, not lost. (Dead-letter topics come with H-22.)
- [ ] Record count, duplicate count, and write latency show as metrics and traces.
- [ ] The Postgres adapter and an in-memory or SQLite fake pass the same `StorePort` contract suite.

## Dependencies

- Depends on: [008 H-14](008-H-14-one-agent-interface.md), [019 H-17](019-H-17-event-port.md), [020 X-8](020-X-8-event-broker.md), [021 H-20](021-H-20-event-consumer-adapter.md), [022 H-6](022-H-6-security-middleware.md)
- Blocks: [043 A-3](043-A-3-result-events.md), [044 G-4](044-G-4-history-swap.md), [064 D-1](064-D-1-golden-set-agent.md), [071 D-8](071-D-8-data-rules.md), [127 L-3](127-L-3-router-slm.md)

## References

- Epic story: [D-0 in Phase 4](../slm-agent-platform-epic-v3.md#phase-4-golden-set-agent-and-evaluator-slm)
- Epic context: [B.4](../slm-agent-platform-epic-v3.md#b4) · [D.1](../slm-agent-platform-epic-v3.md#d1) · [D.3](../slm-agent-platform-epic-v3.md#d3)
- Backlog plan: [000-plan.md](000-plan.md)
