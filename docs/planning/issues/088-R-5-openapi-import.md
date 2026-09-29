---
title: "R-5: OpenAPI import: register each operation of a REST API"
labels: ["story", "priority:P1", "phase:5-registry", "area:registry", "size:M", "layer:platform"]
milestone: "W7 Registry and governance gate"
index: 88
epic_id: R-5
depends_on: ["051 H-13", "081 R-8"]
blocks: []
epic_refs: [F.3]
---

## Why

Much useful work sits behind REST APIs that are not agents. OpenAPI import registers each operation of an API, so the orchestrator can find and call them like any tool. It reuses the operation-to-tool mapping from 051 H-13, so REST and MCP entries never drift apart, and every operation description goes through the scanner (081 R-8).

## What

- Import by spec URL or file, for OpenAPI 3.1 (suggested: also 3.0), checked with an OpenAPI validator.
- Registers the API as an `api` entry, and each operation as an entry linked to it.
- Operation name from `operationId`, or a stable name from the method and path when there is none.
- Description from `summary` and `description`. Input schema from parameters and request body, output schema from the success response, with all `$ref` links resolved.
- Endpoint from the server URL and path. Auth from `securitySchemes`, with secrets as Vault references only.
- `GET` and `HEAD` operations are marked read; `POST`, `PUT`, `PATCH`, and `DELETE` are marked write, so a call needs an `idempotency_key` (B.3).
- External APIs get `trust: external` and wait for approval (080 R-7).
- Import again: a changed description goes back to review (082 R-9), and a removed operation becomes `inactive`.

## Out of scope

- MCP server import (087 R-4).
- AsyncAPI specs and event schemas (089 R-15).

## Acceptance criteria

- [ ] Importing a sample spec with 8 operations creates 1 API entry and 8 operation entries, with no `$ref` left in the stored schemas.
- [ ] An operation with no `operationId` gets the same name on every import.
- [ ] Read and write marks match the HTTP method of every operation.
- [ ] A poisoned operation description is flagged by the scanner.
- [ ] After one operation's description changes, a new import sends only that operation to review; a removed operation becomes `inactive`.
- [ ] An invalid spec is rejected with the validator's error, and nothing is registered.

## Dependencies

- Depends on: [051 H-13](051-H-13-openapi-mcp-tools.md), [081 R-8](081-R-8-description-scanning.md)
- Blocks: none

## References

- Epic story: [R-5 in Phase 5](../slm-agent-platform-epic-v3.md#phase-5-service-registry)
- Epic context: [F.3](../slm-agent-platform-epic-v3.md#f3)
- Backlog plan: [000-plan.md](000-plan.md)
