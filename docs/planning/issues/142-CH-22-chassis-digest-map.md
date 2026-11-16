---
title: "CH-22: One tag-to-digest map in agent-lib: written on publish, read by admission rule 9"
labels: ["story", "priority:P2", "phase:0-harness", "area:harness", "size:M", "layer:chassis"]
milestone: "W10 Production readiness"
index: 142
epic_id: CH-22
depends_on: ["056 CH-7", "139 CH-19", "141 CH-21"]
blocks: []
epic_refs: [G.4]
---

## Why

The chart pins one chassis digest today: `chassis.image.digest`, for the library's tag only (139 CH-19). A service that runs another tag, by its own pin or by its ring's hold (`ring.chassisTag`, 056 CH-7), must set `chassis.image.digest` too, or the render fails (`agent-lib.validateImagePin`). So a rollback pin is two values, the tag and its digest. A ring hold is worse: the overlay holds the tag, but the digest has to sit in each service's own values.

Rule 9 has the same split. On a cloud (`imageSource: digest`) it admits only the digests in `admission.params.supportedChassisDigests`, a list the platform keeps by hand in `deploy/helm/agent-admission`. Nothing ties that list to `chassis.image.supportedTags`, which the chart renders as `supportedChassisTags`. The two can drift: a digest stays admitted after its tag leaves the supported set, or a new release is refused.

There is a second gap. Rules 7b and 9 find the chassis image as `<registryPrefix>chassis`: the `chassisRepository` variable in both policies in `deploy/helm/agent-lib/templates/_admission.tpl`, and `poc_harness.admission_model`. A prefix ends in `/`, so the library's repository, `ghcr.io/odere-pro/agent-chassis`, can never match it. On a digest cluster every service must then set `chassis.image.repository`, and so, under `agent-lib.validateImagePin`, its own digest too: two values again. The digest-mode render test hides this, because it overrides the repository (`test_poc09_chart_boundary.py::test_in_digest_mode_rule_9_reads_the_chassis_digest_not_the_tag`). 141 CH-21 fixes the repository; this story needs it.

`chassis-architect` recommended one map on 2026-11-16, after the rings ran on kind (PR 240, `pocs/poc-09-agent-mvp-template/notes/2026-11-16-056-rings-kind-poc05.md`). The kind run pinned by tag only (`imageSource: pull-never`), so it did not show either gap.

## What

- A tag-to-digest map in the `agent-lib` defaults, one entry per published supported tag. suggested: `chassis.image.digests`, keyed by tag. A kind-only tag (`poc09`) has no entry. The map replaces `chassis.image.digest` and its all-zero placeholder. Before the first push the map is empty, and a render that pins by digest fails with today's "no chassis is published yet" message. The library pins by digest unless the service sets `chassis.image.digest: ""`.
- `scripts/publish_chassis.sh` writes the map. `publish` (through `sign` and `pin`) adds the pushed digest under its tag and keeps the entries of the other supported tags. It refuses a tag that is not in `supportedTags`, and a different digest for a tag that has one. The same digest again is a no-op, since `sign` is the recovery path and may run twice. suggested: `pin` drops entries for tags no longer in `supportedTags`.
- The chart picks a pod's digest from the map, by the tag in effect: the service's pin, then the ring's `ring.chassisTag`, then the library's tag. A pin or a ring hold is then one value, the tag. `agent-lib.validateImagePin` fails a render when the library pins by digest, the service sets no `chassis.image.digest`, and either the repository is not the library's or the tag in effect has no entry in the map. A service's own `chassis.image.digest` stays. `""` pins by tag (kind, `values-kind.yaml`), and a digest pins an image the map does not hold.
- Rule 9 reads the map. `agent-lib.admissionParams` renders `supportedChassisDigests` from the map's entries for `supportedTags`, the way it renders `supportedChassisTags` today. The hand-kept `admission.params.supportedChassisDigests` in `agent-admission` goes, and the chart refuses a value set there. The rule's CEL does not change.
- What reads today's `digest:` line moves with the map: `packages/agentctl/tests/test_agentctl_chart.py` (the `_LIBRARY_DIGEST` read, lines 31 to 50), `pocs/poc-09-agent-mvp-template/tests/chart_render.py` and the `test_poc09_chart*.py` files, `scripts/tests/test_publish_chassis.py`, the `chassis.image` row in `deploy/helm/README.md`, and the comments in `deploy/helm/agent-lib/templates/_values.tpl` (its header) and `deploy/helm/agent-admission/values.yaml`.
- `deploy/rings/README.md` says a pin, a hold, and a rollback each set the tag only.

## Out of scope

- Checking the image's signature at admission (the 056 CH-7 box, parked on the ADR-005 amendment, `docs/reviews/2026-11-16-adr-005-image-verify-comparison.md`).
- Workload image digests (080 R-7).
- PoC-5's kustomize set, `deploy/kind/poc05/admission/`. It runs `pull-never` and lists no digest.
- The rings on a cloud's clusters (038 X-1a).
- Recording the map in ADR-005, as a dated Status line (the user's call). The story does not wait for it: no ADR names `supportedChassisDigests`.

## Acceptance criteria

- [ ] `scripts/publish_chassis.sh pin DIGEST`, on a copy of the chart (`CHASSIS_VALUES_TPL`), writes DIGEST under the chart's tag in the map and keeps the other supported tags' entries. It refuses a tag outside `supportedTags`, and a different digest for a tag that has one. The same digest again is a no-op, since `sign` is the recovery path and may run twice. A test in `scripts/tests/test_publish_chassis.py`, no Docker.
- [ ] A render test: a service pinned to the previous tag, and a ring overlay that holds it, each set one value, the tag. The pod's chassis image is `repository:tag@<that tag's digest in the map>`. A tag with no entry fails the render while the library pins by digest.
- [ ] A render test: the admission params' `supportedChassisDigests` equals the map's digests for `supportedTags`, in order. `agent-admission` with a hand-set `supportedChassisDigests` fails the render.
- [ ] Rule 9 on the Python model (`poc_harness.admission_model`), in `imageSource: digest`, fed the pod and the params rendered from the charts, with no `chassis.image.repository` override: a pod on each mapped digest is admitted, and a pod on a digest outside the map is refused.

No kind box: the CEL is unchanged, held equal to `deploy/kind/poc05/admission/chassis-version-policy.yaml`, and `test_poc05_kind_admission.py::test_digest_mode_refuses_a_tag_and_admits_its_digest_twin` runs digest mode on the API server.

## Dependencies

- Depends on: [056 CH-7](056-CH-7-chassis-release-rings.md), [139 CH-19](139-CH-19-reference-stack.md), [141 CH-21](141-CH-21-chassis-repository-under-prefix.md)
- Blocks: none

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.4](../slm-agent-platform-epic-v3.md#g4)
- Backlog plan: [000-plan.md](000-plan.md)
