---
title: "R-16: Registry entries carry the governance block and serve as the AI system inventory"
labels: ["story", "priority:P1", "phase:5-registry", "area:registry", "size:S", "layer:platform"]
milestone: "W7 Registry and governance gate"
index: 78
epic_id: R-16
depends_on: ["077 C-1"]
blocks: ["123 C-4"]
epic_refs: [E.4, E.5]
---

## Why

The registry doubles as the AI system inventory that ISO/IEC 42001 asks for (A.3, A.4, and A.5 in E.5). Once the gate (077 C-1) checks the governance block, the registry should also store and show it, so the inventory comes from the same data that controls activation. The documentation pack (123 C-4) builds on this inventory.

## What

- Each agent entry stores its full `governance` block: `intended_purpose`, `risk_class`, `eu_ai_act_role`, `impact_assessment_ref`, `owner`, `human_oversight`, `ai_generated_marking`, `data_sources`, `log_retention_days`.
- The block is copied from the config version that passed the gate, and the entry records that config version.
- `owner` is required on every entry of every type (A.3 roles and responsibilities).
- List filters on `risk_class`, `eu_ai_act_role`, `owner`, and `human_oversight`.
- An inventory report (A.4 resources): each agent with its purpose, risk class, role, owner, oversight, model routes (`model.route`, `fallback_route`), data sources, and lane (`engine_connector`). For `remote` agents, it also shows the managed-runtime provider, if any (such as AWS Bedrock AgentCore or Vertex AI Agent Engine). Suggested: compute shown as the serving target of each model route (GPU or CPU).
- A registry export in JSON and CSV (suggested), for the whole inventory or one risk class.
- An "as of" date on the report and export, built from the kept entry history, so an auditor can see what was live on a past date.

## Out of scope

- Enforcing the rules on activation (077 C-1).
- Trust levels and approval of third-party entries (080 R-7).
- The documentation pack per agent version (123 C-4) and the control mapping report (124 C-9).

## Acceptance criteria

- [ ] Getting an active agent entry returns its full governance block and the config version it came from.
- [ ] An entry of any type without an `owner` is rejected.
- [ ] Filtering on `risk_class: high` returns only high-risk agents.
- [ ] The inventory report lists every active agent with all the fields above, checked against a fixture of at least 10 agents.
- [ ] The JSON and CSV exports hold the same rows as the report.
- [ ] A report "as of" a past date shows the entries and governance values that were live on that date, not today's values.
- [ ] A new config version with a changed governance block updates the entry and publishes `registry.entry.changed.v1`.

## Dependencies

- Depends on: [077 C-1](077-C-1-governance-enforcement.md)
- Blocks: [123 C-4](123-C-4-documentation-pack.md)

## References

- Epic story: [R-16 in Phase 5](../slm-agent-platform-epic-v3.md#phase-5-service-registry)
- Epic context: [E.4](../slm-agent-platform-epic-v3.md#e4) · [E.5](../slm-agent-platform-epic-v3.md#e5)
- Backlog plan: [000-plan.md](000-plan.md)
