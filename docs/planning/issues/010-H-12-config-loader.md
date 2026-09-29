---
title: "H-12: Config loader: object storage, JSON Schema check, reload on change"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:M", "layer:chassis"]
milestone: "W2 Chassis MVP"
index: 10
epic_id: H-12
depends_on: ["007 H-1"]
blocks: ["016 H-9", "017 H-4", "023 C-5", "050 CH-5", "055 CH-6", "077 C-1", "098 O-10"]
epic_refs: [C.1, E.4]
---

## Why

Every agent's config lives in the config store, checked against a JSON Schema and reloaded without a restart (B.2). Under [ADR-001](../adr/001-chassis-delivery-model.md), only the chassis reads the store: the store needs a credential that only the chassis holds (hard requirement 1). The workload gets its config and prompts from the chassis. Most chassis features read their settings from it, so it comes early in the chassis wave. The config JSON Schema includes the governance block fields from day one, as compliance by design; 077 C-1 later adds enforcement on activation.

## What

- A loader, run only in the chassis, that reads one `AgentConfig` YAML file (`apiVersion: agents/v1`) from the config store: a versioned object storage bucket (MinIO locally, S3 on AWS, GCS on GCP), through the S3 API.
- The workload gets its config and prompts through `ctx`, never from the store.
- `agent-config.schema.json` in the store's `schemas/` folder, covering every C.1 field, from `metadata` and `spec.class` to `events` and `secrets`.
- The fields ADR-001 adds to the schema:
  - `spec.engine`: `connector`, plus `url` and `auth` for `remote`;
  - `spec.trust`;
  - `spec.operations`: each with a name, a path, an input schema, and one answer or a stream;
  - references to the service's credentials.
- The governance fields from E.4 with their allowed values: `intended_purpose`, `risk_class`, `eu_ai_act_role`, `impact_assessment_ref`, `owner`, `human_oversight`, `ai_generated_marking`, `data_sources`, `log_retention_days`.
- A check on load: an invalid config stops the chassis from starting.
- Reload on change without a restart. An invalid change is refused and the last good version stays active (suggested: poll the object version; the `config.config.changed.v1` event can replace polling later).
- Referenced files, such as `prompts.system_ref`, loaded by the chassis and passed to the workload in `ctx`.
- Secrets only as references (for example `vault://...`), never as values. They resolve only into the chassis container.
- `spec.adapters`: the adapter per port (for example `model: litellm`, `events: dapr`, `state: valkey`), and profiles `fake`, `local`, and `cloud` that set them all at once. This is an addition to the C.1 schema, so any component can be swapped by config. The `fake` profile implies `inprocess`. The `cloud` profile refuses `inprocess`.
- A local-file `ConfigPort` adapter for the `fake` profile, next to the S3 one.
- The config and prompt versions in use reported in each response's `versions` field.

## Out of scope

- Seeding the config store in Docker Compose (016 H-9).
- Enforcing the governance block on activation (077 C-1).
- Orchestrator and workflow schemas (098 O-10).
- Routing the declared operations (050 CH-5).
- The `remote` connector and its auth adapters (055 CH-6).
- Provisioning the per-service credentials, and the config store refusing calls without one (026 CH-4).

## Acceptance criteria

- [ ] The echo agent starts with its config read from a MinIO bucket (started by the test).
- [ ] A config that breaks the schema is refused at start, and the error names the failing field.
- [ ] A valid change is picked up without a restart; an invalid change is refused and the last good version stays active.
- [ ] A wrong `risk_class` or `human_oversight` value fails the check.
- [ ] A plain secret value in the config fails the check; a `vault://` reference passes.
- [ ] Each response reports the config version and the prompt version in `versions`.
- [ ] Switching `spec.adapters.model` between `fake` and `litellm` changes the adapter with no code change.
- [ ] The agent starts in the `fake` profile with no network and no keys, in the `inprocess` lane.
- [ ] The `cloud` profile with `spec.engine.connector: inprocess` fails at start.
- [ ] A `remote` config without `spec.engine.url` fails the schema check.
- [ ] The workload gets its config and system prompt through `ctx`. The workload container is given no config-store credential.

## Dependencies

- Depends on: [007 H-1](007-H-1-harness-library-ports-envelope.md)
- Blocks: [016 H-9](016-H-9-local-debug-profile.md), [017 H-4](017-H-4-harness-features.md), [023 C-5](023-C-5-ai-generated-marking.md), [050 CH-5](050-CH-5-service-operations.md), [055 CH-6](055-CH-6-remote-lane-trust-rule.md), [077 C-1](077-C-1-governance-enforcement.md), [098 O-10](098-O-10-orchestrator-schemas.md)

## References

- Epic story: [H-12 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [C.1](../slm-agent-platform-epic-v3.md#c1) · [E.4](../slm-agent-platform-epic-v3.md#e4)
- Backlog plan: [000-plan.md](000-plan.md)
