---
title: "H-21: AsyncAPI 3.0 spec per agent, generated next to the OpenAPI spec"
labels: ["story", "priority:P1", "phase:0-harness", "area:harness", "size:S", "layer:chassis"]
milestone: "W5 Chassis completion"
index: 52
epic_id: H-21
depends_on: ["019 H-17", "051 H-13"]
blocks: ["089 R-15", "123 C-4"]
epic_refs: [D.1, R14]
---

## Why

Every agent can be started by events and publishes events, so its event contract needs the same care as its REST contract. An AsyncAPI 3.0 spec next to the OpenAPI spec gives other systems one place to see what an agent consumes and produces. The registry (089 R-15) and the documentation pack (123 C-4) read it.

## What

- An AsyncAPI 3.0 spec generated per agent from `events.consumes` and `events.produces` in its config, in the same build step as the OpenAPI spec (051 H-13).
- Channels follow the topic naming rule `<domain>.<entity>.<event>.v<major>`.
- Messages are CloudEvents 1.0, with the `traceparent`, `idempotencykey`, `configversion`, and `modelroute` extensions.
- Payload schemas come from the same Pydantic models as the envelope, so REST and events share schemas.
- Payload schemas are also written as JSON Schema files to the config store under `schemas/`.
- Broker bindings for the broker chosen per DEC-1.
- The agent serves the spec (suggested: `GET /asyncapi.json`), and `/manifest` links to it.
- A CI check fails when a payload change breaks consumers and the topic's major version did not change.

## Reuse

- **Use:** AsyncAPI 3.1, with the AsyncAPI generator (Node) for docs and validation.
- **Build:** the AsyncAPI file, generated from `spec.events` in config. Dapr does not generate it.
- **Watch:** FastStream generates AsyncAPI, but it supports neither SQS, Pub/Sub, nor CloudEvents, so it is not used.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Registering event schemas in the registry (089 R-15).
- The documentation pack (123 C-4).
- Dead-letter topics, which join the spec in 053 H-22.

## Acceptance criteria

- [ ] The echo agent, the simplifier, and the recorder publish AsyncAPI 3.0 specs that pass validation in CI.
- [ ] Each spec lists exactly the topics in the agent's `events` config.
- [ ] A contract test fails if an agent emits an event type that is not in its spec.
- [ ] Real events captured from the broker in an integration test validate against the spec's payload schemas.
- [ ] Removing or renaming a payload field fails CI unless the topic moves to a new major version.
- [ ] `/manifest` links to the AsyncAPI spec, and the schema files are in the config store.

## Dependencies

- Depends on: [019 H-17](019-H-17-event-port.md), [051 H-13](051-H-13-openapi-mcp-tools.md)
- Blocks: [089 R-15](089-R-15-event-schemas-registered.md), [123 C-4](123-C-4-documentation-pack.md)

## References

- Epic story: [H-21 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [D.1](../slm-agent-platform-epic-v3.md#d1) · [R14](../slm-agent-platform-epic-v3.md#r14)
- Backlog plan: [000-plan.md](000-plan.md)
