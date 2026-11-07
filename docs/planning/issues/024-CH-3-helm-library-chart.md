---
title: "CH-3: Shared Helm library chart: chassis sidecar with a pinned tag, secrets on the chassis only, Service on the chassis port"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:L", "layer:chassis"]
milestone: "W2 Chassis MVP"
index: 24
epic_id: CH-3
depends_on: ["009 CH-1", "016 H-9"]
blocks: ["025 H-10", "026 CH-4", "055 CH-6", "056 CH-7", "059 H-15"]
epic_refs: [G.4, H]
---

## Why

[ADR-001](../adr/001-chassis-delivery-model.md) item 9: the chassis tag is set once, in a shared Helm library chart that every service includes. A release then reaches every service with no rebuild. The ADR also says the sidecar is added in plain sight, by the chart, not by a hidden cluster webhook. This issue was split out of 025 H-10, which was bigger than size L.

## What

- A Helm library chart that adds the chassis container to a service's pod (Kubernetes 1.33 or later, with native sidecars). Decided in [contract v4](../../contracts/contract-v4.md) (Bind order): the chassis is the native sidecar (an init container with `restartPolicy: Always`) and the workload the main container, because the proxies must bind before the workload starts.
- The chassis image tag, set in one place in the library chart.
- A per-service pin: one value that holds a service on the previous tag.
- The Service points at the chassis port only. The workload listens on localhost and is not in the Service. The chassis arguments are allow-listed, because a flag can move a listener off loopback.
- Secrets mounted into the chassis container only.
- No service account token in the workload: `automountServiceAccountToken: false` on the pod, or the token projected into the chassis container only.
- Probes on the chassis: `/health` and `/ready`. `/ready` fails when the workload does not answer.
- Startup and liveness probes on the workload container, so a hung workload restarts the pod instead of leaving `/ready` false forever. suggested: an `exec` GET on the A2A agent card, since the workload listens on 127.0.0.1 only, with the liveness probe's own `terminationGracePeriodSeconds: 10`.
- Resource requests and limits per container. suggested: start from the ADR's estimate for the chassis (0.05–0.1 vCPU, 128–256 MiB).
- A security context for both containers: non-root, read-only root file system with an `emptyDir` at `/tmp`, no privilege escalation, all capabilities dropped.
- Shutdown order (gap (g) in the backlog plan): the workload finishes its in-flight `handle` calls before it exits. The drain values come from the [container roles note](../../../pocs/poc-04-stateless-scalable/notes/2026-10-01-container-roles.md), all suggested: `--drain-delay-s 10` on the chassis, longer than endpoint removal plus the client's idle keep-alive window (5 s for httpx); `terminationGracePeriodSeconds` of at least the drain delay plus the drain timeout plus a margin (50 = 10 + 30 + 10). That note measured the reverse container order, with the workload as the native sidecar, so the liveness and rolling-restart boxes stay open until they run on this chart on kind.
- Built in PoC-9: `deploy/helm/agent-lib` and `deploy/helm/example-agent`, rendered in `poc09-helm` and run on kind on 2026-11-05 ([kind drill](../../../pocs/poc-09-agent-mvp-template/notes/2026-11-05-kind-hpa-drill.md)).

## Reuse

- **Use:** a Helm library chart, Kubernetes native sidecar containers (stable since 1.33), and Kubernetes resource requests sized from the load test (122 X-5).
- **Build:** the chart templates, the chassis tag set once, and the per-service pin.
- **Watch:** a cluster webhook could inject the chassis instead, but it is hidden and harder to debug (ADR-001).
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- The service template and its chart (025 H-10), which include this chart.
- Scoped credentials, default-deny egress, and the admission rules in the chart (026 CH-4).
- Ring rollouts and the minimum-version rule (056 CH-7).
- Right-sizing from a load test, and Valkey sizing per idempotency entry (122 X-5).
- `podPidsLimit`, a node setting (038 X-1a).
- The deployer's pod-create right in the agents namespace (055 CH-6, [ADR-005](../adr/005-remote-lane-auth-and-trust-admission.md) Consequences).

## Acceptance criteria

- [x] A chart that includes the library chart runs a pod with two containers: the chassis and the workload. · Delivered in PoC-9: `pocs/poc-09-agent-mvp-template/tests/test_poc09_chart.py::test_sidecar_lane_renders_one_pod_with_the_chassis_and_the_workload`, `pocs/poc-09-agent-mvp-template/notes/2026-11-05-kind-hpa-drill.md` (two pods, each 2/2 Running)
- [ ] Changing the chassis tag in the library chart rolls the new chassis out to every service that includes it, with no service image rebuilt.
- [ ] A service can pin the previous chassis tag with one value change.
- [ ] The workload port cannot be reached from another pod, on a CNI that enforces NetworkPolicy. Only the chassis port is in the Service. Today only the bind refusal is tested (`packages/workload-a2a/tests/test_workload_a2a_server.py::test_non_loopback_host_is_refused`).
- [ ] Secrets are mounted only into the chassis container, checked from inside the workload container.
- [x] No service account token is mounted in the workload container, checked from inside it. · Delivered in PoC-5, PoC-9: `pocs/poc-09-agent-mvp-template/notes/2026-11-05-kind-hpa-drill.md` ("In the workload container: no service account token"), `pocs/poc-05-sandboxed/tests/test_poc05_kind_sidecar_controls.py::test_h02_no_service_account_token_in_the_workload`
- [ ] A workload that stops answering its liveness probe gets the pod restarted, on this chart on kind.
- [ ] A rolling restart under load fails no request, on this chart on kind.
- [x] The chart runs on a local kind or k3d cluster with native sidecars. · Delivered in PoC-9: `pocs/poc-09-agent-mvp-template/notes/2026-11-05-kind-hpa-drill.md` (sidecar lane, chassis as the native sidecar)

## Dependencies

- Depends on: [009 CH-1](009-CH-1-engine-connectors-a2a.md), [016 H-9](016-H-9-local-debug-profile.md)
- Blocks: [025 H-10](025-H-10-template-repo.md), [026 CH-4](026-CH-4-chassis-only-credentials-egress.md), [055 CH-6](055-CH-6-remote-lane-trust-rule.md), [056 CH-7](056-CH-7-chassis-release-rings.md), [059 H-15](059-H-15-agent-factory-cli.md)

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.4](../slm-agent-platform-epic-v3.md#g4) · [H](../slm-agent-platform-epic-v3.md#app-h)
- Backlog plan: [000-plan.md](000-plan.md)
