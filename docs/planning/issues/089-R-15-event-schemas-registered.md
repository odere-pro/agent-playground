---
title: "R-15: Event schemas (AsyncAPI) registered next to OpenAPI specs"
labels: ["story", "priority:P1", "phase:5-registry", "area:registry", "size:S", "layer:platform"]
milestone: "W7 Registry and governance gate"
index: 89
epic_id: R-15
depends_on: ["052 H-21", "076 R-1"]
blocks: []
epic_refs: [D.1, R14]
---

## Why

Agents are started by events and publish events, so other systems need to find event contracts as easily as REST ones. D.1 says event schemas are JSON Schema files in the config store, registered in the registry. This issue stores each agent's AsyncAPI 3.0 spec (052 H-21) next to its OpenAPI spec, per version.

## What

- The registry stores the AsyncAPI 3.0 spec [R14] for each agent version, next to its OpenAPI spec, and validates it on registration.
- Each agent entry lists the event types it consumes and produces, from its config (`events.consumes`, `events.produces`).
- Event schemas (JSON Schema files in the config store) are registered by event type and version.
- Every event type in the D.3 catalog has a registered schema.
- Event type names must follow `<domain>.<entity>.<event>.v<major>` (D.2).
- Lookup by event type: its schema, and the entries that produce and consume it.
- Suggested: a compatibility check, so a schema change within the same major version cannot remove a required field.

## Out of scope

- Generating the AsyncAPI spec in each agent (052 H-21).
- Starting workflows from events (112 O-16).

## Acceptance criteria

- [ ] Registering the echo agent stores its AsyncAPI spec, and the API returns it with the OpenAPI spec for the same version.
- [ ] An invalid AsyncAPI spec blocks the registration with the validator's error.
- [ ] A lookup of `agents.task.completed.v1` returns its schema, its producers, and its consumers.
- [ ] Every event type in D.3 has a registered schema.
- [ ] An event type name that breaks the D.2 pattern is rejected.
- [ ] A schema change that removes a required field without a new major version is rejected.

## Dependencies

- Depends on: [052 H-21](052-H-21-asyncapi-spec.md), [076 R-1](076-R-1-registry-data-model-api.md)
- Blocks: none

## References

- Epic story: [R-15 in Phase 5](../slm-agent-platform-epic-v3.md#phase-5-service-registry)
- Epic context: [D.1](../slm-agent-platform-epic-v3.md#d1) · [R14](../slm-agent-platform-epic-v3.md#r14)
- Backlog plan: [000-plan.md](000-plan.md)
