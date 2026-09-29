---
title: "R-2: Kubernetes controller (kopf): watches labeled deployments and reads /manifest"
labels: ["story", "priority:P1", "phase:5-registry", "area:registry", "size:M", "layer:platform"]
milestone: "W7 Registry and governance gate"
index: 83
epic_id: R-2
depends_on: ["025 H-10", "076 R-1", "080 R-7", "056 CH-7"]
blocks: ["086 R-10"]
epic_refs: [F.3, G.4]
---

## Why

New agents must register on deploy with no hand steps (Phase 5 done-when). A kopf controller watches labeled deployments and reads each agent's `/manifest`. It replaces the registration stub in the template CI (025 H-10), so the registry follows what really runs in the cluster, not what CI thinks it deployed.

## What

- A kopf operator (Python), deployed with a Helm chart through Argo CD.
- Watches Deployments with a registry label (suggested: `platform/register: "true"`).
- When a deployment is ready, reads `GET /manifest` from the chassis port of its Service and registers the entry through the registry API (076 R-1). The manifest carries the chassis version, the lane, and the trust value.
- Passes both image digests, the chassis's and the workload's. The registry checks the workload's cosign signature to set `internal-signed` (080 R-7), and tracks the chassis image on its own.
- A new workload image or config version registers a new agent version. After the rollout, the old version is marked `inactive`.
- A chassis ring rollout (056 CH-7) restarts every pod. A chassis-only change updates `chassis_version` on the entry. It does not create a new agent version.
- `remote` lane: registers the chassis-fronted Deployment, never the remote workload's private address.
- Deleting a deployment marks its entry `inactive`, never deleted. Scaling to zero (KEDA) is not a delete, so the entry stays `active`.
- Registration is an idempotent upsert, with retries and backoff when the registry is down.
- A service account with only the RBAC rights it needs: watch deployments and read services.
- Removes the registration stub from the template CI (025 H-10).

## Out of scope

- Local Docker runs (084 R-3).
- Health checks for registered entries (086 R-10).
- Agent pools and their KEDA setup (104 O-3).

## Acceptance criteria

- [ ] Deploying the echo agent with the label to the dev cluster creates an `active`, `internal-signed` entry within 60 seconds.
- [ ] A deployment without the label is ignored.
- [ ] Rolling out a new version registers it, and the old version becomes `inactive` after the rollout.
- [ ] Scaling the agent to zero keeps its entry `active`; deleting the deployment makes it `inactive`, and the row still exists.
- [ ] If either image is unsigned, the entry registers as `pending`, not `active`.
- [ ] A chassis ring rollout creates no new agent version, and the entry's `chassis_version` shows the new tag.
- [ ] A `remote` agent's entry points at its chassis Service, not at the remote workload.
- [ ] Restarting the controller creates no duplicate entries.
- [ ] The template CI no longer has the registration stub, and a new agent from the template registers through the controller only.

## Dependencies

- Depends on: [025 H-10](025-H-10-template-repo.md), [076 R-1](076-R-1-registry-data-model-api.md), [080 R-7](080-R-7-trust-levels.md), [056 CH-7](056-CH-7-chassis-release-rings.md)
- Blocks: [086 R-10](086-R-10-health-checks.md)

## References

- Epic story: [R-2 in Phase 5](../slm-agent-platform-epic-v3.md#phase-5-service-registry)
- Epic context: [F.3](../slm-agent-platform-epic-v3.md#f3) · [G.4](../slm-agent-platform-epic-v3.md#g4)
- Backlog plan: [000-plan.md](000-plan.md)
