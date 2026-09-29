---
title: "R-3: Docker watcher for local runs"
labels: ["story", "priority:P1", "phase:5-registry", "area:registry", "size:S", "layer:platform"]
milestone: "W7 Registry and governance gate"
index: 84
epic_id: R-3
depends_on: ["016 H-9", "076 R-1", "080 R-7"]
blocks: []
epic_refs: [G.4]
---

## Why

Developers run agents locally with Docker Compose (G.4), and they need the same registry behavior there. The Docker watcher is the local twin of the Kubernetes controller (083 R-2), so the Phase 5 done-when ("new agents register automatically on deploy, local and Kubernetes") also holds on a laptop.

## What

- A small watcher service in the local debug profile (016 H-9).
- Listens to Docker start and stop events for containers with the registry label (suggested: `platform.register=true`). Only the chassis container carries the label, never the workload container.
- On start, reads `GET /manifest` from the chassis container and registers through the registry API (076 R-1), with the same client code as the Kubernetes controller.
- On stop, marks the entry `inactive`; on the next start, `active` again. Nothing is deleted.
- Registers again when hot reload changes the agent's config version.
- When the watcher itself starts, it registers every labeled container already running, with no duplicates.
- Local images are not signed, so they would wait for approval (080 R-7). Suggested: a `local` trust policy that auto-approves watcher entries, on only in the Docker Compose profile. The governance gate (077 C-1) still applies.
- Reads the Docker socket read-only.

## Out of scope

- Registration in Kubernetes (083 R-2).
- Health checks and live metrics (086 R-10).

## Acceptance criteria

- [ ] After `docker compose up`, the echo agent is `active` in the registry within 10 seconds.
- [ ] Stopping the container marks the entry `inactive`, and starting it again marks it `active`; the row is never deleted.
- [ ] A container without the label is ignored.
- [ ] The entry comes from the chassis container's `/manifest`. The workload container is never read.
- [ ] Restarting the watcher creates no duplicate entries.
- [ ] Changing the agent's config version with hot reload registers the new version.
- [ ] The registry refuses to start when the `local` trust policy is set outside the local profile.

## Dependencies

- Depends on: [016 H-9](016-H-9-local-debug-profile.md), [076 R-1](076-R-1-registry-data-model-api.md), [080 R-7](080-R-7-trust-levels.md)
- Blocks: none

## References

- Epic story: [R-3 in Phase 5](../slm-agent-platform-epic-v3.md#phase-5-service-registry)
- Epic context: [G.4](../slm-agent-platform-epic-v3.md#g4)
- Backlog plan: [000-plan.md](000-plan.md)
