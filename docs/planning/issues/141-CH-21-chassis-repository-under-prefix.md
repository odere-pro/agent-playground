---
title: "CH-21: The chassis repository under the registry prefix, so admission finds the library chassis"
labels: ["decision", "priority:P2", "phase:0-harness", "area:harness", "size:S", "layer:chassis"]
milestone: "W10 Production readiness"
index: 141
epic_id: CH-21
depends_on: ["139 CH-19"]
blocks: ["142 CH-22"]
epic_refs: [G.4]
---

## Why

Rules 7b and 9 find the chassis image as `<registryPrefix>chassis`. That is the `chassisRepository` variable in both policies in `deploy/helm/agent-lib/templates/_admission.tpl`, in `deploy/kind/poc05/admission/policy.yaml` and `chassis-version-policy.yaml`, and in `poc_harness.admission_model`. A prefix ends in `/`. The library's repository, `ghcr.io/odere-pro/agent-chassis` (139 CH-19, decided by the user on 2026-11-15), can never match it.

So on a digest cluster a pod on the library's default chassis is not seen as the chassis. Every service must set `chassis.image.repository`, and then, under `agent-lib.validateImagePin`, its own digest too. On kind this does not show: `values-kind.yaml` sets `kind.local/agent-platform/chassis`, which matches the kind prefix. The digest-mode render test does not show it either, because it overrides the repository (`test_poc09_chart_boundary.py::test_in_digest_mode_rule_9_reads_the_chassis_digest_not_the_tag`). `chassis-architect` found it on 2026-11-16, reviewing 142 CH-22.

## What

The user picks one of two fixes.

- **(a)** suggested: publish the chassis as `ghcr.io/odere-pro/agent-platform/chassis`. The platform's `registryPrefix` on a cloud is then `ghcr.io/odere-pro/agent-platform/`, and no CEL changes. This reverses the repository name the user decided on 2026-11-15 in 139 CH-19, so it needs the user's OK. It changes `IMAGE` in `scripts/publish_chassis.sh`, `chassis.image.repository` in `agent-lib`, the `chassis.image` row in `deploy/helm/README.md`, and the tests that name the image. No image is published yet, so nothing on the registry moves.
- **(b)** a platform param for the chassis repository (suggested: `chassisRepository` in the admission params), defaulting to `<registryPrefix>chassis`. It changes the CEL in both copies, `agent-lib` and `deploy/kind/poc05/admission/`, and the Python model. It then needs a kind box. (b) also adds a dated ADR-005 Status line: rules 7b and 9 read the chassis repository from the params.

## Out of scope

- The tag-to-digest map (142 CH-22).
- Workload image repositories and their trust list (080 R-7).
- The first cloud's registry and its prefix (038 X-1a).

## Acceptance criteria

- [ ] The user's pick, (a) or (b), recorded with its date in this issue, and in 139 CH-19 if it is (a).
- [ ] A render test in `imageSource: digest` with no `chassis.image.repository` override, using the pick's params: under (a), `registryPrefix: ghcr.io/odere-pro/agent-platform/` with `trustedRepositories` and the workload image under it; under (b), the new param set to `ghcr.io/odere-pro/agent-chassis`. Rules 7b and 9 on `poc_harness.admission_model` find the library's chassis image as the chassis. `test_poc09_chart_boundary.py::test_in_digest_mode_rule_9_reads_the_chassis_digest_not_the_tag` is rewritten this way and passes.
- [ ] On (b) only: the chart's CEL and the kustomize set's stay equal (`test_poc09_chart_boundary.py::test_the_admission_chart_renders_the_poc05_trust_rule_unchanged` and `::test_the_admission_chart_renders_the_poc05_chassis_version_rule_unchanged`), and a digest-mode case on kind admits a chassis under the param's repository and refuses one outside it. On (a), this box is closed by a dated line that says no CEL changed.

## Dependencies

- Depends on: [139 CH-19](139-CH-19-reference-stack.md)
- Blocks: [142 CH-22](142-CH-22-chassis-digest-map.md)

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.4](../slm-agent-platform-epic-v3.md#g4)
- Backlog plan: [000-plan.md](000-plan.md)
