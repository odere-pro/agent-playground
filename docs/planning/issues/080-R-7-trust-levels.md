---
title: "R-7: Trust levels: internal signed entries go live, external and manual need approval"
labels: ["story", "priority:P1", "phase:5-registry", "area:registry", "size:M", "layer:platform"]
milestone: "W7 Registry and governance gate"
index: 80
epic_id: R-7
depends_on: ["076 R-1"]
blocks: ["081 R-8", "082 R-9", "083 R-2", "084 R-3", "085 R-6"]
epic_refs: [F.3, E.5, R5]
---

## Why

Poisoned tool descriptions are a top risk in the epic [R5]. Trust levels decide what goes live on its own and what a person must approve first: internal signed entries go live automatically, while external and manual entries wait for approval. This is also the ISO 42001 A.10 control for third parties (E.5), and every registration path after it (083 R-2, 084 R-3, 085 R-6) depends on it.

## What

- A `trust` level on every entry, in a fixed order. Suggested values: `internal-signed`, `internal`, `external`, `manual`.
- `internal-signed` means the entry's image signature checks out with cosign against the organization's key (image signing from 025 H-10).
- Only `internal-signed` entries that also pass the governance gate (077 C-1) go live on their own. All others stay `pending`.
- A written mapping from these levels to [ADR-001](../adr/001-chassis-delivery-model.md)'s `spec.trust`, because the two use the word "trust" differently. `external` and `manual` entries are third-party, so they are `untrusted` and run in the `remote` lane. `internal-signed` is evidence for the ADR's source test only, not for its behavior test. suggested: an unsigned `internal` entry counts as failing the source test.
- A pod runs two images. The workload image's signature sets the trust level. The chassis image is tracked on its own (`chassis_version`, 076 R-1) and does not change the entry's trust.
- An approval queue, and approve and reject calls (the API behind `registry_approve` in F.4), with approver, time, and reason recorded.
- Approval needs an approve scope (suggested: `registry:approve`), and the person who registered an entry cannot approve it.
- External entries need a supplier or model assessment reference before approval (ISO 42001 A.10).
- A minimum-trust filter on list calls, matching `registry.min_trust` in the orchestrator config (C.2).
- `pending` and `rejected` entries never show in normal list results.

## Out of scope

- Scanning descriptions for hidden instructions (081 R-8) and hash pinning (082 R-9).
- The registration UI and YAML import (085 R-6).
- Trust filters in search (091 R-11), which reuse the order defined here.

## Acceptance criteria

- [ ] An agent entry with a valid cosign signature from the organization's key, and a passing governance check, becomes `internal-signed` and `active` with no human step.
- [ ] An unsigned image, or one signed with another key, is not `internal-signed` and stays `pending`.
- [ ] External and manual entries stay `pending` until approved, and do not appear in list results.
- [ ] Approve and reject record approver, time, and reason, and publish `registry.entry.changed.v1`.
- [ ] A caller without the approve scope, or the entry's own registrant, gets a 403.
- [ ] An external entry cannot be approved without an assessment reference.
- [ ] Filtering by minimum trust returns only entries at or above that level.
- [ ] An entry marked `trusted` in `spec.trust` whose workload image fails the source test is refused.
- [ ] The signature check reads the workload image, not the chassis image.

## Dependencies

- Depends on: [076 R-1](076-R-1-registry-data-model-api.md)
- Blocks: [081 R-8](081-R-8-description-scanning.md), [082 R-9](082-R-9-description-hash-pinning.md), [083 R-2](083-R-2-kubernetes-controller.md), [084 R-3](084-R-3-docker-watcher.md), [085 R-6](085-R-6-manual-registration.md)

## References

- Epic story: [R-7 in Phase 5](../slm-agent-platform-epic-v3.md#phase-5-service-registry)
- Epic context: [F.3](../slm-agent-platform-epic-v3.md#f3) · [E.5](../slm-agent-platform-epic-v3.md#e5) · [R5](../slm-agent-platform-epic-v3.md#r5)
- Backlog plan: [000-plan.md](000-plan.md)
