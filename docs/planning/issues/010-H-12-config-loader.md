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
- Status after PoC-1: `ChassisConfig` (`chassis.server`) exists, with `profile`, `agent`, `spec.adapters`, `spec.engine`, `spec.model`, `spec.prompt`, and a `version` that is the file's content hash. Every response already reports `versions.config` and `versions.prompt`. Open: the MinIO source, hot reload, the `risk_class` and `vault://` checks, and the `cloud` plus `inprocess` refusal.
- Status after PoC-2: one lane key, `spec.engine.connector`; `spec.adapters.engine` is refused with an error that names the one field. `spec.adapters` is merged over the profile defaults field by field. There is a `tools` field. The `cloud` profile refuses `inprocess` and the `fake` and `memory` adapters (suggested). Evidence: `packages/chassis/tests/test_profiles.py::test_adapters_engine_is_refused_and_names_the_one_field`, `::test_adapters_merge_over_the_profile_defaults_per_field`, `::test_cloud_refuses_a_fake_or_memory_adapter`. Still open: the MinIO source, hot reload, and the `risk_class` and `vault://` checks.
- Status after PoC-4: built. A `ConfigPort` on the S3 API (`minio` and `s3`, one class `S3Config`); the store document checked against `ChassisConfig` (published as `schemas/chassis-config.v0.json`) at start and on every poll. A valid change to a reloadable path (`version`, `spec.limits`, `spec.model.route`, `spec.prompt`, `spec.idempotency.ttl_s`) takes effect with no restart; an invalid document, or one that changes a restart-only path, is refused and the last good config stays. `versions.config` names the content (`<version>+<hash12>`), so every replica that reads the same document reports the same value ([contract v3](../../contracts/contract-v3.md#config)). Tests: `pocs/poc-04-stateless-scalable/tests/test_config_reload.py`, `test_config_minio.py` (`network`), `packages/chassis/tests/integration/test_s3_config_contract.py`; the live drill is section 4 of [the drills note](../../../pocs/poc-04-stateless-scalable/notes/2026-10-01-drills.md). Not ticked here: the gate run goes in the PoC-4 README. Open: no ceilings. A store document can raise `spec.limits` and `spec.idempotency.ttl_s` without bound and change `spec.model.route` and `spec.prompt` at run time (`deploy/compose/SECURITY.md`, section 7); suggested: ceilings in the bootstrap file that a reload cannot pass. Also open: `/openapi.json` keeps the start limits after a reload; the `risk_class`, `vault://`, and `remote` URL checks.

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
- [x] Each response reports the config version and the prompt version in `versions`. Delivered in PoC-1: `pocs/poc-01-walking-skeleton/tests/test_serve.py::test_stream_and_complete_carry_the_same_output`.
- [x] Switching `spec.adapters.model` between `fake` and `litellm` changes the adapter with no code change. Delivered in PoC-1: `pocs/poc-01-walking-skeleton/tests/test_model_adapter.py::test_switching_the_model_adapter_is_a_config_change`.
- [x] The agent starts in the `fake` profile with no network and no keys, in the `inprocess` lane. Delivered in PoC-1: `pocs/poc-01-walking-skeleton/tests/test_serve.py::test_chassis_serve_starts_from_config`, run by `make test` with sockets disabled and keys stripped.
- [x] The `cloud` profile with `spec.engine.connector: inprocess` fails at start. Delivered in PoC-2: `packages/chassis/tests/test_profiles.py::test_inprocess_refused_in_cloud`, `::test_build_ports_refuses_inprocess_in_cloud`; `packages/chassis/tests/test_server.py::test_config_refuses_the_lane_the_profile_forbids`.
- [ ] A `remote` config without `spec.engine.url` fails the schema check.
- [ ] The workload gets its config and system prompt through `ctx`. The workload container is given no config-store credential.

## Dependencies

- Depends on: [007 H-1](007-H-1-harness-library-ports-envelope.md)
- Blocks: [016 H-9](016-H-9-local-debug-profile.md), [017 H-4](017-H-4-harness-features.md), [023 C-5](023-C-5-ai-generated-marking.md), [050 CH-5](050-CH-5-service-operations.md), [055 CH-6](055-CH-6-remote-lane-trust-rule.md), [077 C-1](077-C-1-governance-enforcement.md), [098 O-10](098-O-10-orchestrator-schemas.md)

## References

- Epic story: [H-12 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [C.1](../slm-agent-platform-epic-v3.md#c1) · [E.4](../slm-agent-platform-epic-v3.md#e4)
- Backlog plan: [000-plan.md](000-plan.md)
