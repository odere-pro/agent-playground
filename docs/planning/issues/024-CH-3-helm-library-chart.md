---
title: "CH-3: Shared Helm library chart: chassis sidecar with a pinned tag, secrets on the chassis only, Service on the chassis port"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:M", "layer:chassis"]
milestone: "W2 Chassis MVP"
index: 24
epic_id: CH-3
depends_on: ["009 CH-1", "016 H-9"]
blocks: ["025 H-10", "056 CH-7"]
epic_refs: [G.4, H]
---

## Why

[ADR-001](../adr/001-chassis-delivery-model.md) item 9: the chassis tag is set once, in a shared Helm library chart that every service includes. A release then reaches every service with no rebuild. The ADR also says the sidecar is added in plain sight, by the chart, not by a hidden cluster webhook. This issue was split out of 025 H-10, which was bigger than size L.

## What

- A Helm library chart that adds the chassis container to a service's pod (Kubernetes 1.33 or later, with native sidecars). Which container is the native sidecar follows the PoC-4 result. suggested: the workload is the native sidecar and the chassis the main container. Native sidecars start first and stop last, so SIGTERM reaches the chassis first and it drains before the workload exits.
- The chassis image tag, set in one place in the library chart.
- A per-service pin: one value that holds a service on the previous tag.
- The Service points at the chassis port only. The workload listens on localhost and is not in the Service.
- Secrets mounted into the chassis container only.
- No service account token in the workload: `automountServiceAccountToken: false` on the pod, or the token projected into the chassis container only.
- Probes on the chassis: `/health` and `/ready`. `/ready` fails when the workload does not answer.
- A liveness probe on the workload container (suggested: the A2A agent card GET), so a hung workload restarts the pod instead of leaving `/ready` false forever.
- Resource requests and limits per container. suggested: start from the ADR's estimate for the chassis (0.05–0.1 vCPU, 128–256 MiB).
- A security context for both containers: non-root, read-only root file system, all capabilities dropped.
- Shutdown order (gap (g) in the backlog plan): the container roles above, or, if PoC-4 shows they do not help, a short preStop delay on both containers. Either way the workload finishes its in-flight `handle` calls before it exits.
- Status after PoC-4, on kind ([container roles note](../../../pocs/poc-04-stateless-scalable/notes/2026-10-01-container-roles.md)): the native sidecar wins. The workload is the native sidecar and the chassis the main container. After the chassis drain fix (`Connection: close` on every public answer while draining), the native sidecar failed 0 calls in six rolling restarts (3 replicas, 20 clients, no retry, 90 s: `echo-python` 3 runs plus 1 through pytest, `echo-typescript` 2 runs). The preStop variant still lost calls on `echo-typescript` (5 of 51,734 over 4 runs from the host), because during the preStop sleep the chassis does not know it is draining. What the chart must carry, all suggested: `--drain-delay-s 10` on the chassis, longer than endpoint removal plus the client's idle keep-alive window (5 s for httpx); `terminationGracePeriodSeconds` of at least the drain delay plus the drain timeout plus a margin (50 = 10 + 30 + 10); the workload's startup and liveness probes `exec` a GET on the agent card, since the workload listens on 127.0.0.1 only, and the liveness probe has its own `terminationGracePeriodSeconds: 10`, so a hung process is killed without waiting for the pod's grace; a read-only root on both containers with an `emptyDir` at `/tmp`, a non-root user, no privilege escalation, no capability, and no service account token. A SIGSTOPped workload restarted the pod in every run (Ready again after 42 to 47 s); `/ready` went false after 9 to 11 s, at the 10 s bound, not under it. Valkey must be sized as rate × TTL × about 1.5 KB per idempotency entry: 256 MB with `noeviction` filled at 186,218 entries (about 175k to 186k), which at the 24 h default TTL allows a steady rate of only about 2 calls per second; then every keyed call is 503. The measured sidecar CPU and memory for the resource requests are in 122 X-5's status.

## Reuse

- **Use:** a Helm library chart, Kubernetes native sidecar containers (stable since 1.33), and Kubernetes resource requests sized from the load test (122 X-5).
- **Build:** the chart templates, the chassis tag set once, and the per-service pin.
- **Watch:** a cluster webhook could inject the chassis instead, but it is hidden and harder to debug (ADR-001).
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- The service template and its chart (025 H-10), which include this chart.
- Scoped credentials and default-deny egress (026 CH-4).
- Ring rollouts and the minimum-version rule (056 CH-7).
- Right-sizing from a load test (122 X-5).

## Acceptance criteria

- [ ] A chart that includes the library chart runs a pod with two containers: the chassis and the workload.
- [ ] Changing the chassis tag in the library chart rolls the new chassis out to every service that includes it, with no service image rebuilt.
- [ ] A service can pin the previous chassis tag with one value change.
- [ ] The workload port cannot be reached from another pod. Only the chassis port is in the Service.
- [ ] Secrets are mounted only into the chassis container, checked from inside the workload container.
- [ ] No service account token is mounted in the workload container, checked from inside it.
- [ ] A workload that stops answering its liveness probe gets the pod restarted.
- [ ] A rolling restart under load fails no request.
- [ ] The chart runs on a local kind or k3d cluster with native sidecars.

## Dependencies

- Depends on: [009 CH-1](009-CH-1-engine-connectors-a2a.md), [016 H-9](016-H-9-local-debug-profile.md)
- Blocks: [025 H-10](025-H-10-template-repo.md), [056 CH-7](056-CH-7-chassis-release-rings.md)

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.4](../slm-agent-platform-epic-v3.md#g4) · [H](../slm-agent-platform-epic-v3.md#app-h)
- Backlog plan: [000-plan.md](000-plan.md)
