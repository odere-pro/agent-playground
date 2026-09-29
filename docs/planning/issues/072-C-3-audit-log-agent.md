---
title: "C-3: Audit log data agent: append-only, object lock, retention per risk class, export API"
labels: ["story", "priority:P1", "phase:9-governance", "area:governance", "size:L", "layer:platform"]
milestone: "W6 Golden set and evaluator SLM"
index: 72
epic_id: C-3
depends_on: ["008 H-14", "019 H-17", "020 X-8", "021 H-20", "053 H-22", "022 H-6"]
blocks: ["095 P-4", "110 C-6", "118 C-7", "124 C-9"]
epic_refs: [E.6, D.3]
---

## Why

The AI Act asks for automatic records of every call and run (Art. 12), and an auditor needs them to be tamper-proof. It is pulled forward from Phase 9: it uses the same data-agent pattern as the recorder, and later stories (P-4, C-6, C-7) write to it. Building it now also means the review decisions and model promotions from this wave are logged from the start.

## What

- A data agent built from the template that consumes, from the broker, every event type in the D.3 catalog that lists the audit log as a consumer: calls, runs, config changes, model promotions, review decisions, registry changes, and incidents.
- Append-only storage in object storage with object lock (write once, read many): MinIO locally, S3 or GCS in the cloud.
- Only the chassis holds the object storage credential. suggested: it exposes the audit store to its workload as MCP tools through the tool proxy (054 H-16). This is gap (a) in [the gaps in the backlog plan](000-plan.md#adr-001-follow-ups).
- Events arrive through a Dapr subscription on the chassis port, never on the workload's. How Dapr's API is kept from the workload is gap (c).
- Retention per risk class from the agent's `governance` block (`log_retention_days`). A `high` risk agent gets at least six months.
- An export API per agent, per agent version, and per time range.
- Suggested: entries batched per agent per minute, with a hash chain across batches, so a gap or a change can be found.
- Duplicates dropped by event `id`. Failed writes use retries and the dead-letter topic (053 H-22).
- PII redacted before storage, with the chassis redaction as in 022 H-6.
- Export needs its own read scope, and no role can delete entries.

## Reuse

- **Use:** S3 Object Lock (or GCS retention lock) for append-only storage, and a Dapr subscription on the chassis port for the events.
- **Build:** the data agent and the export API.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- The MCP audit log on every call (095 P-4).
- Human oversight controls (110 C-6) and the incident flow (118 C-7).
- The control mapping report (124 C-9).

## Acceptance criteria

- [ ] One test event of each D.3 type that lists the audit log as a consumer lands in the audit store.
- [ ] Overwriting or deleting an entry before its retention ends fails, on MinIO and on the first cloud's object storage.
- [ ] Retention follows `log_retention_days`, and a `high` risk agent's entries are kept at least six months.
- [ ] The export API returns entries for one agent, one agent version, and one time range, and the export's hash chain verifies.
- [ ] A duplicate event is stored once.
- [ ] The PII fixture is redacted in stored entries.
- [ ] The workload container holds no object storage or broker credential.
- [ ] Write lag and failed writes show as metrics.

## Dependencies

- Depends on: [008 H-14](008-H-14-one-agent-interface.md), [019 H-17](019-H-17-event-port.md), [020 X-8](020-X-8-event-broker.md), [021 H-20](021-H-20-event-consumer-adapter.md), [053 H-22](053-H-22-event-reliability.md), [022 H-6](022-H-6-security-middleware.md)
- Blocks: [095 P-4](095-P-4-mcp-scopes-audit.md), [110 C-6](110-C-6-human-oversight.md), [118 C-7](118-C-7-incident-flow.md), [124 C-9](124-C-9-control-mapping-report.md)

## References

- Epic story: [C-3 in Phase 9](../slm-agent-platform-epic-v3.md#phase-9-governance-and-compliance)
- Epic context: [E.6](../slm-agent-platform-epic-v3.md#e6) · [D.3](../slm-agent-platform-epic-v3.md#d3)
- Backlog plan: [000-plan.md](000-plan.md)
