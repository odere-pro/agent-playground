---
title: "PoC-5: Sandboxed: the remote lane and the trust rule"
labels: ["poc", "priority:P0", "area:harness", "area:security"]
milestone: "Agent MVP"
index: 5
iteration: PoC-5
timebox: "3 weeks"
depends_on: ["PoC-2"]
backlog_refs: ["022 H-6", "054 H-16", "026 CH-4", "055 CH-6"]
---

## Question

Can untrusted code, including code that a framework writes and runs, run in the `remote` lane without reaching secrets, the internet, the disk, or tools it is not allowed to use? And does a workload in the `sidecar` lane reach internal services only through the chassis?

## Why

If any business logic can go in, the chassis must assume some of it is buggy or hostile. [ADR-001](../adr/001-chassis-delivery-model.md) sets where hostile code runs. The trust rule sends it to the `remote` lane, in its own sandboxed pod, and the chassis pod stays outside the sandbox. The hard limits live in shared services, so they hold even if the chassis's pipeline is bypassed. This iteration proves both lanes against a hostile workload, and meets hard requirement 1 on a real cluster. Doing it before the bake-off tells each framework which lane it gets.

## Scope

- [ ] Container hardening for both containers and the remote pod: non-root user, read-only root file system, all capabilities dropped, default seccomp profile, CPU, memory, and process limits. No service account token in the workload: `automountServiceAccountToken: false`, or the token projected into the chassis container only.
- [ ] A local kind cluster with native sidecars, a CNI that enforces NetworkPolicy, and a gVisor RuntimeClass.
- [ ] The `remote` connector: the a2a-sdk client from PoC-2, with its URL and auth from config.
- [ ] The proxies for the `remote` lane: the chassis's model and tool proxies also listen on the pod IP, and each remote authenticates (suggested: mTLS through cert-manager, or a per-remote token from the chassis config). Only the remote pod's label may reach them. A managed runtime that cannot be pointed at the proxies holds its own scoped key, the one relaxation of hard requirement 1 (PoC-6b).
- [ ] A remote workload pod on gVisor (`runsc`), through `kubernetes-sigs/agent-sandbox`: no secrets mounted, default-deny egress, and only the chassis pod may call it. It reaches only the chassis's model and tool proxies, with its own credential. The chassis pod stays outside the sandbox.
- [ ] Hard requirement 1 on the kind cluster: LiteLLM, the MCP gateway, the broker, Valkey, and the config store refuse calls without the chassis's credential.
- [ ] One scoped LiteLLM virtual key per service, and an MCP gateway tool allow-list per key. The credentials are mounted in the chassis container only.
- [ ] A default-deny NetworkPolicy per service pod. It allows only what the chassis needs, and it names the cloud metadata service and the Kubernetes API in the deny list. The chassis's public port binds to the pod IP, so localhost carries only the proxies.
- [ ] `spec.trust: trusted | untrusted` in the agent config, and an admission check (Kyverno or ValidatingAdmissionPolicy). It blocks an `untrusted` workload, or an image not from our registry, in the `sidecar` lane.
- [ ] `ToolPort` is defined here, in the same shape as the PoC-1 ports, with its fake and contract suite. Then the real adapter (MCP through LiteLLM's MCP gateway), passing the same suite as the fake tools. `mode: write` tools need the `idempotency_key`.
- [ ] Generated code runs through the code-execution tool behind `ToolPort` (agent-sandbox, or E2B if needed), so it does not make an agent untrusted.
- [ ] A hostile suite for the `sidecar` lane: the workload cannot reach LiteLLM, the MCP gateway, the broker, Valkey, the config store, or the internet on its own. It holds no service account token, and cannot reach the cloud metadata service, the Kubernetes API, or the chassis's public port on localhost. From PoC-2 (`deploy/compose/SECURITY.md`, section 6): it cannot reach a model server directly (in the Compose `local` variant, `llama-cpp` has no key), and it cannot bind the chassis's ports (it shares the namespace, and the chassis waits for the sidecar before it binds `127.0.0.1:8090`).
- [ ] A hostile suite for the `remote` lane: the workload cannot read secrets, reach the internet, write to disk, fork-bomb, call a tool that is not allow-listed, or use the chassis's proxies without its own credential.
- [ ] CI runs a fake workload through the `remote` lane on every commit.
- [ ] Optional: Envoy as an egress proxy for other allow-listed hosts. The chassis proxies already add the credentials for models and tools, so Envoy is not needed for them.

## Reuse

- **Use:** gVisor through kubernetes-sigs/agent-sandbox, a kind cluster, NetworkPolicy with a CNI that enforces it (for example Cilium or Calico), Kyverno or ValidatingAdmissionPolicy, LiteLLM virtual keys and the MCP gateway's per-key tool grants, and hardened Docker settings locally. E2B only for generated code, if agent-sandbox does not fit. Optional: Envoy for egress to other allow-listed hosts.
- **Build:** the `remote` connector, the proxies' `remote`-lane listener and its auth, the per-service credential set, the admission policy, the hostile suites for both lanes, and the remote-lane CI job.
- **Watch:** agent-sandbox reached v1.0 in 2026, so pin it. On EKS, gVisor or Kata must be installed on the nodes.
- Details: [reuse analysis](010-reuse-analysis.md)

## Out of scope

- Auth, scopes, rate limits, and PII redaction (PoC-7 and PoC-8).
- Prompt-injection detection (PoC-7).
- The managed runtime and its cloud auth adapter (PoC-6).
- The minimum-version admission rule (backlog [056 CH-7](../issues/056-CH-7-chassis-release-rings.md)).
- Signed images (backlog [025 H-10](../issues/025-H-10-template-repo.md)).

## Demo

On the kind cluster, a hostile workload in the `remote` lane tries to read secrets, call a public URL, write to disk, fork-bomb, and call a tool that is not on its allow-list. Each attempt is blocked and logged, while a normal request through the chassis still works. A hostile workload in the `sidecar` lane calls LiteLLM and the MCP gateway directly, and is refused. Deploying an `untrusted` workload in the `sidecar` lane is rejected by the admission check.

## Exit criteria

- [ ] The hostile suites run in CI; the parts that need no cluster run offline on every commit.
- [ ] CI runs a fake workload through the `remote` lane on every commit, and it passes the same contract suite as the `sidecar` lane.
- [ ] From the workload container, every internal service refuses a call without the chassis's credential. Through the chassis, the same calls succeed.
- [ ] No provider key or internal credential is visible in any workload container or remote pod, and neither holds a service account token.
- [ ] From the workload container, the cloud metadata service, the Kubernetes API, and the chassis's public port on localhost are unreachable.
- [ ] The remote pod reaches only the chassis's model and tool proxies, and only with its own credential.
- [ ] The admission check rejects an `untrusted` workload, and a third-party image, in the `sidecar` lane.
- [ ] The hostile suites pass in both lanes.
- [ ] gVisor works for every engine that runs in the `remote` lane (or the exceptions are listed), and its latency and memory overhead are measured.
- [ ] What the chassis cannot see or control in the `remote` lane is listed.

## Links

- Plan: [000-plan.md](000-plan.md) · Previous: [PoC-4](004-PoC-4-stateless-scalable.md) (can run in parallel) · Next: [PoC-6](006-PoC-6-framework-bake-off.md)
- Decision: [ADR-001](../adr/001-chassis-delivery-model.md) (items 4 to 6, hard requirements 1 and 2) · [ADR-005](../adr/005-remote-lane-auth-and-trust-admission.md), the remote lane's credential and the trust rule's admission check (accepted 2026-10-09) · Contract: [contract v4](../../contracts/contract-v4.md) · Design: [PoC-5 plan](../../plans/2026-10-02-poc-05-sandboxed.md), [per-call code sandbox](../../plans/2026-10-09-poc-05-per-call-sandbox.md)
- Notes: [threat model](../../../pocs/poc-05-sandboxed/notes/2026-10-02-threat-model.md) · [bring-up](../../../pocs/poc-05-sandboxed/notes/2026-10-02-bring-up.md) · [sidecar suite](../../../pocs/poc-05-sandboxed/notes/2026-10-08-sidecar-suite.md) · [remote suite](../../../pocs/poc-05-sandboxed/notes/2026-10-08-remote-suite.md) · [close runs](../../../pocs/poc-05-sandboxed/notes/2026-10-09-close-runs.md) · [gVisor overhead](../../../pocs/poc-05-sandboxed/notes/2026-10-09-gvisor-overhead.md) · [blind spots](../../../pocs/poc-05-sandboxed/notes/2026-10-02-blind-spots.md) · [T10 exception](../../../pocs/poc-05-sandboxed/notes/2026-10-09-t10-probe-exception.md) · [backlog changes](../../../pocs/poc-05-sandboxed/notes/backlog-changes.md)
- Backlog issues this previews: [022 H-6](../issues/022-H-6-security-middleware.md) (in part), [054 H-16](../issues/054-H-16-tool-port.md) (allow-list and write mode), [026 CH-4](../issues/026-CH-4-chassis-only-credentials-egress.md), [055 CH-6](../issues/055-CH-6-remote-lane-trust-rule.md) (without the cloud auth adapter)
- Epic: [B.3](../slm-agent-platform-epic-v3.md#b3), [G.3](../slm-agent-platform-epic-v3.md#g3), [R5](../slm-agent-platform-epic-v3.md#r5)
