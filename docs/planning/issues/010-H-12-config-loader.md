---
title: "H-12: Config loader: object storage, JSON Schema check, reload on change"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:M", "layer:chassis"]
milestone: "W2 Chassis MVP"
index: 10
epic_id: H-12
depends_on: ["007 H-1"]
blocks: ["017 H-4", "023 C-5", "050 CH-5", "055 CH-6", "077 C-1", "098 O-10"]
epic_refs: [C.1, E.4]
---

## Why

Every agent's config lives in the config store, checked against a JSON Schema and reloaded without a restart (B.2). Under [ADR-001](../adr/001-chassis-delivery-model.md), only the chassis reads the store: the store needs a credential that only the chassis holds (hard requirement 1). The workload gets its config and prompts from the chassis. Most chassis features read their settings from it, so it comes early in the chassis wave. The config JSON Schema checks `risk_class` from day one, as compliance by design; 077 C-1 later adds the other governance fields and enforcement on activation.

## What

- A loader, run only in the chassis, that reads one `AgentConfig` YAML file (`apiVersion: agents/v1`) from the config store: a versioned object storage bucket (MinIO locally, S3 on AWS, GCS on GCP), through the S3 API. Built in PoC-4: a `ConfigPort` on the S3 API (`minio` and `s3`, one class `S3Config`).
- The workload gets its config and prompts through `ctx`, never from the store.
- `agent-config.schema.json` in the store's `schemas/` folder, covering every C.1 field, from `metadata` and `spec.class` to `events` and `secrets`. Today the store document is checked against `ChassisConfig`, published as `schemas/chassis-config.v0.json`.
- The fields ADR-001 adds to the schema:
  - `spec.engine`: `connector`, plus `url` and `auth` for `remote`;
  - `spec.trust`;
  - `spec.operations`: each with a name, a path, an input schema, and one answer or a stream;
  - references to the service's credentials.
- The governance field `risk_class` from E.4, with its allowed values.
- A check on load: an invalid config stops the chassis from starting.
- Reload on change without a restart. An invalid change is refused and the last good version stays active (suggested: poll the object version; the `config.config.changed.v1` event can replace polling later). Built in PoC-4: a valid change to a reloadable path takes effect with no restart, and a change to a restart-only path is refused. `versions.config` names the content (`<version>+<hash12>`), so every replica that reads the same document reports the same value ([contract v3](../../contracts/contract-v3.md#config)).
- Ceilings: the bootstrap file's per-call `spec.limits` (`max_tokens_max`, `timeout_ms_max`, `body_bytes_max`, `messages_max`) and `spec.idempotency.ttl_s` bound every store document; a value above one is refused with `over_ceiling` and the last good config stays (suggested: `CEILINGS` in `chassis.server.config_loader`). A store document can still change `spec.model.route` and `spec.prompt` at run time (`deploy/compose/SECURITY.md`, section 7). `/openapi.json` keeps the start limits after a reload, and the `/v1/run` description says so.
- Referenced files, such as `prompts.system_ref`, loaded by the chassis and passed to the workload in `ctx`.
- Secrets never appear as values in the config. They reach the chassis container through the chart (`secretEnv`; architecture spec, "The deploy shape"), and a credential-shaped value in the config is refused.
- `spec.adapters`: the adapter per port (for example `model: litellm`, `events: dapr`, `state: valkey`), and profiles `fake`, `local`, and `cloud` that set them all at once. This is an addition to the C.1 schema, so any component can be swapped by config. `spec.adapters` is merged over the profile defaults field by field. The `fake` profile implies `inprocess`. The `cloud` profile refuses `inprocess` and the `fake` and `memory` adapters (suggested). The one lane key is `spec.engine.connector`; `spec.adapters.engine` is refused.
- A local-file `ConfigPort` adapter for the `fake` profile, next to the S3 one.
- The config and prompt versions in use reported in each response's `versions` field.

## Out of scope

- Seeding the config store in Docker Compose (016 H-9).
- The governance fields other than `risk_class` (`intended_purpose`, `eu_ai_act_role`, `impact_assessment_ref`, `owner`, `human_oversight`, `ai_generated_marking`, `data_sources`, `log_retention_days`), and enforcing the governance block on activation (077 C-1).
- Orchestrator and workflow schemas (098 O-10).
- Routing the declared operations (050 CH-5).
- The `remote` connector and its auth adapters (055 CH-6).
- Provisioning the per-service credentials, and the config store refusing calls without one (026 CH-4).

## Acceptance criteria

- [x] The echo agent starts with its config read from a MinIO bucket (started by the test). · Delivered in PoC-4: `pocs/poc-04-stateless-scalable/notes/2026-10-01-drills.md` section 4; `pocs/poc-04-stateless-scalable/tests/test_config_minio.py::test_a_store_change_reaches_the_next_request_when_it_comes_from_s3` (`network`).
- [x] A config that breaks the schema is refused at start, and the error names the failing field. · Delivered in PoC-4: `packages/chassis/tests/test_config_loader.py::test_start_fails_on_an_invalid_document_and_names_the_field_not_the_value`.
- [x] A valid change is picked up without a restart; an invalid change is refused and the last good version stays active. · Delivered in PoC-4: `pocs/poc-04-stateless-scalable/tests/test_config_reload.py::test_a_change_takes_effect_on_the_next_request_with_no_restart`, `::test_a_bad_config_is_refused_and_the_last_good_one_stays`; [contract v3](../../contracts/contract-v3.md#config).
- [x] A wrong `risk_class` value fails the check. · Delivered by: `packages/chassis/tests/test_config_checks.py::test_a_wrong_risk_class_fails_the_check`, `::test_each_allowed_risk_class_passes`.
- [x] A credential-shaped value anywhere in the config is refused. · Delivered by: `packages/chassis/tests/test_config_checks.py::test_a_credential_shaped_value_is_refused_and_named_by_path`, `::test_a_password_in_a_url_is_refused_and_not_echoed`, `::test_a_credential_in_the_version_is_refused_too`, `::test_a_store_document_with_a_credential_is_rejected_by_path_only`; the message names the path, never the value.
- [x] Each response reports the config version and the prompt version in `versions`. Delivered in PoC-1: `pocs/poc-01-walking-skeleton/tests/test_serve.py::test_stream_and_complete_carry_the_same_output`.
- [x] Switching `spec.adapters.model` between `fake` and `litellm` changes the adapter with no code change. Delivered in PoC-1: `pocs/poc-01-walking-skeleton/tests/test_model_adapter.py::test_switching_the_model_adapter_is_a_config_change`.
- [x] The agent starts in the `fake` profile with no network and no keys, in the `inprocess` lane. Delivered in PoC-1: `pocs/poc-01-walking-skeleton/tests/test_serve.py::test_chassis_serve_starts_from_config`, run by `make test` with sockets disabled and keys stripped.
- [x] The `cloud` profile with `spec.engine.connector: inprocess` fails at start. Delivered in PoC-2: `packages/chassis/tests/test_profiles.py::test_inprocess_refused_in_cloud`, `::test_build_ports_refuses_inprocess_in_cloud`; `packages/chassis/tests/test_server.py::test_config_refuses_the_lane_the_profile_forbids`.
- [x] A `remote` config without `spec.engine.url` fails the schema check. · Delivered in PoC-5: `pocs/poc-05-sandboxed/tests/test_poc05_trust_config.py::test_the_remote_lane_requires_a_url`.
- [ ] The workload gets its config and system prompt through `ctx`. The workload container is given no config-store credential.
- [x] A reload that passes a ceiling from the bootstrap file is refused, and the last good config stays (suggested: ceilings for `spec.limits` and `ttl_s`). · Delivered by: `packages/chassis/tests/test_config_loader.py::test_a_reload_past_a_bootstrap_ceiling_is_refused_and_the_last_good_stays`, `::test_a_reload_at_or_under_the_bootstrap_ceiling_is_taken`, `::test_a_start_document_past_a_bootstrap_ceiling_fails_startup`.
- [x] `/openapi.json` shows the limits in force after a reload, or says it shows the start values. · Delivered by: `packages/chassis/tests/test_limits.py::test_openapi_says_it_shows_the_start_limits_after_a_reload`.

## Dependencies

- Depends on: [007 H-1](007-H-1-harness-library-ports-envelope.md)
- Blocks: [017 H-4](017-H-4-harness-features.md), [023 C-5](023-C-5-ai-generated-marking.md), [050 CH-5](050-CH-5-service-operations.md), [055 CH-6](055-CH-6-remote-lane-trust-rule.md), [077 C-1](077-C-1-governance-enforcement.md), [098 O-10](098-O-10-orchestrator-schemas.md)

## References

- Epic story: [H-12 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [C.1](../slm-agent-platform-epic-v3.md#c1) · [E.4](../slm-agent-platform-epic-v3.md#e4)
- Backlog plan: [000-plan.md](000-plan.md)
