---
title: "R-1: Registry data model and API"
labels: ["story", "priority:P1", "phase:5-registry", "area:registry", "size:L", "layer:platform"]
milestone: "W7 Registry and governance gate"
index: 76
epic_id: R-1
depends_on: ["008 H-14", "051 H-13", "019 H-17", "022 H-6"]
blocks: ["077 C-1", "080 R-7", "083 R-2", "084 R-3", "086 R-10", "089 R-15", "090 R-13", "091 R-11"]
epic_refs: [F.3]
---

## Why

The registry is how agents and the orchestrator find agents, tools, APIs, and MCP servers, and it is also the AI system inventory. This issue fixes the entry model and the API that every later registry issue builds on. It comes after 051 H-13, because an agent's entry is built from its `/manifest`.

## What

- A registry service built on the chassis, from the service template (025 H-10), with its own OpenAPI 3.1 spec, on Postgres 17 with pgvector (CloudNativePG). Suggested: class `data`, since it alone writes its store.
- Only the registry's chassis holds the store credential. suggested: the chassis exposes the store to its workload as MCP tools through the tool proxy (054 H-16). This is gap (a) in [the gaps in the backlog plan](000-plan.md#adr-001-follow-ups).
- Entry fields from F.3: `name`, `type` (`agent`, `tool`, `api`, `mcp`), `task`, `description`, input and output schema, `endpoint`, `version`, `auth`, `owner`, `tags`, `trust`, `status`.
- `endpoint` is the chassis's public address, never the workload's. The workload listens only on localhost, or on a private address in the `remote` lane ([ADR-001](../adr/001-chassis-delivery-model.md) item 3).
- Fields from ADR-001: `chassis_version`, `engine_connector` (the lane: `sidecar`, `remote`, or `inprocess`), and the runtime trust value from `spec.trust` (suggested field name: `runtime_trust`). It is not the registry's `trust` level; 080 R-7 maps one to the other.
- `description_hash` (`sha256:...`), computed by the registry over the `description` and the schema field descriptions, where tool poisoning can also hide [R5].
- `fallback_of` links with difference metadata: cost, speed, quality, limits, notes.
- Slots that later issues fill: live metrics (`p95_latency_ms`, `error_rate`, cost), and vectors with their embedding model name.
- Status values (suggested): `pending`, `active`, `inactive`, `rejected`. Activation from `pending` to `active` is one explicit step that later gates hook into.
- API (suggested: under `/v1/entries`): register or update, get, list with SQL filters, and change status. There is no delete.
- Publishes `registry.entry.changed.v1` on every change, through the event port (019 H-17).

## Reuse

- **Use:** the MCP Registry `server.json` format and API shape, and A2A agent cards, as record formats. Postgres 17 + pgvector, per the epic.
- **Decision:** evaluate ToolHive Registry Server (Apache 2.0, implements the MCP Registry API, pre-1.0 project) as the base. Use AWS Agent Registry only if the platform goes AWS-native.
- **Build:** our extra fields (trust, governance, fallbacks, metrics) and the change events.
- **Watch:** the official MCP Registry code is not built for self-hosting. Copy its API, not its code.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Governance checks (077 C-1), and trust levels and approval (080 R-7).
- Automatic registration (083 R-2, 084 R-3).
- Health checks and live metric refresh (086 R-10).
- Embeddings and search (091 R-11).

## Acceptance criteria

- [ ] The F.3 example entry is registered and read back with the same fields.
- [ ] An entry with a missing required field or an unknown `type` is rejected with a 422 that names the field.
- [ ] `description_hash` changes when the description or a schema field description changes, and only then.
- [ ] A `fallback_of` link and its metadata come back with the main entry.
- [ ] The echo agent's `/manifest` maps to a valid entry with no hand edits, with `chassis_version`, `engine_connector`, and `runtime_trust` set, and the chassis's public address as `endpoint`.
- [ ] Each SQL filter (type, task, status, owner, tags, trust) returns only matching entries on a fixture of at least 50 entries.
- [ ] Every create, update, and status change publishes one `registry.entry.changed.v1` event.
- [ ] Reads need a read scope and writes need a write scope (022 H-6).

## Dependencies

- Depends on: [008 H-14](008-H-14-one-agent-interface.md), [051 H-13](051-H-13-openapi-mcp-tools.md), [019 H-17](019-H-17-event-port.md), [022 H-6](022-H-6-security-middleware.md)
- Blocks: [077 C-1](077-C-1-governance-enforcement.md), [080 R-7](080-R-7-trust-levels.md), [083 R-2](083-R-2-kubernetes-controller.md), [084 R-3](084-R-3-docker-watcher.md), [086 R-10](086-R-10-health-checks.md), [089 R-15](089-R-15-event-schemas-registered.md), [090 R-13](090-R-13-search-test-set.md), [091 R-11](091-R-11-hybrid-search.md)

## References

- Epic story: [R-1 in Phase 5](../slm-agent-platform-epic-v3.md#phase-5-service-registry)
- Epic context: [F.3](../slm-agent-platform-epic-v3.md#f3)
- Backlog plan: [000-plan.md](000-plan.md)
