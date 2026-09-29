---
title: "C-1: Governance block enforced by the registry on activation"
labels: ["story", "priority:P1", "phase:9-governance", "area:governance", "size:S", "layer:platform"]
milestone: "W7 Registry and governance gate"
index: 77
epic_id: C-1
depends_on: ["010 H-12", "076 R-1", "055 CH-6", "056 CH-7"]
blocks: ["078 R-16", "079 C-2"]
epic_refs: [E.4]
---

## Why

No agent may go live without a governance block: purpose, risk class, owner, and oversight. This issue is pulled forward from Phase 9, because the gate must exist before agents register automatically (083 R-2, 084 R-3). The config loader (010 H-12) already puts the governance fields in the config JSON Schema; this issue makes the registry enforce them on activation.

## What

- A governance check in the registry's activation step (076 R-1), for `agent` entries.
- The registry reads the agent's config version from the config store with the config loader (010 H-12) and checks its `governance` block against the JSON Schema.
- Rule: no `governance` block, no activation.
- Rule: a `high` agent needs an `impact_assessment_ref`, `human_oversight` other than `none`, and `log_retention_days` of at least six months (suggested: 183 days, a registry config value, to confirm with legal).
- Rule: `limited` and `high` agents need `ai_generated_marking: true`, and `prohibited_check_failed` is never activated.
- Rule: an `untrusted` agent (`spec.trust`) must use `spec.engine.connector: remote`.
- Rule: a `trusted` agent must pass the [ADR-001](../adr/001-chassis-delivery-model.md) source test: the owning team wrote and reviewed it, and its workload image is built in our registry. suggested: checked from the image's registry path and signature.
- Rule: the entry's `chassis_version` is at or above the minimum chassis version (056 CH-7).
- These rules are the registry-side gate. The admission checks (055 CH-6, 056 CH-7) are the deploy-time gate for the same rules.
- A failed check leaves the entry `pending` and returns every failed rule, not only the first.
- Each decision is published in `registry.entry.changed.v1` with the rules checked, so the audit log (072 C-3) keeps it.
- A new config version of an active agent is checked again before it goes live.

## Out of scope

- The prohibited-practice checklist that sets `prohibited_check_failed` (079 C-2).
- Showing the governance block on entries and in the inventory (078 R-16).
- Marking outputs as AI-generated at run time (023 C-5).

## Acceptance criteria

- [ ] An agent with no `governance` block stays `pending`, and the error names the missing block.
- [ ] A `high` agent missing `impact_assessment_ref`, or with `human_oversight: none`, or with `log_retention_days` below the minimum, is not activated; each case has a test.
- [ ] A `high` agent that meets all three rules is activated.
- [ ] A `limited` agent with `ai_generated_marking: false` is not activated.
- [ ] An agent with `risk_class: prohibited_check_failed` is never activated, even by a direct status change call.
- [ ] An `untrusted` agent in the `sidecar` lane is not activated.
- [ ] An agent whose chassis version is below the minimum is not activated.
- [ ] Each decision is published as `registry.entry.changed.v1` with the result and the failed rules.
- [ ] Agent config cannot switch the gate off or lower the retention minimum.

## Dependencies

- Depends on: [010 H-12](010-H-12-config-loader.md), [076 R-1](076-R-1-registry-data-model-api.md), [055 CH-6](055-CH-6-remote-lane-trust-rule.md), [056 CH-7](056-CH-7-chassis-release-rings.md)
- Blocks: [078 R-16](078-R-16-ai-system-inventory.md), [079 C-2](079-C-2-prohibited-practice-checklist.md)

## References

- Epic story: [C-1 in Phase 9](../slm-agent-platform-epic-v3.md#phase-9-governance-and-compliance)
- Epic context: [E.4](../slm-agent-platform-epic-v3.md#e4)
- Backlog plan: [000-plan.md](000-plan.md)
