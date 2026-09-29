---
title: "CH-6: Remote lane and the trust rule: sandboxed pod or managed runtime over A2A, cloud auth adapter, admission check"
labels: ["story", "priority:P1", "phase:0-harness", "area:harness", "size:L", "layer:chassis"]
milestone: "W5 Chassis completion"
index: 55
epic_id: CH-6
depends_on: ["009 CH-1", "026 CH-4", "010 H-12", "038 X-1a"]
blocks: ["056 CH-7", "059 H-15", "077 C-1", "116 X-1b"]
epic_refs: [G.3, G.4, H]
---

## Why

[ADR-001](../adr/001-chassis-delivery-model.md) items 4, 5, and 8: untrusted workloads, third-party images, managed runtimes, and other teams' services use the `remote` lane. The sandbox runtime and the network policy are set per pod, not per container. So to sandbox the workload but not the chassis, they must run in different pods. The trust rule decides what must go to `remote`, and an admission check enforces it. Hard requirement 2 says a lane that CI does not test on every commit is not supported, so this issue also adds that CI job.

## What

- The `remote` connector: the same A2A client as 009 CH-1, with its URL from `spec.engine.url` and an auth adapter from `spec.engine.auth`.
- A sandboxed workload pod:
  - its own Deployment, with a gVisor RuntimeClass through agent-sandbox;
  - no secrets mounted;
  - default-deny egress: it reaches only the chassis's model and tool proxies;
  - only the chassis pod may call it.

  The chassis pod stays outside the sandbox.
- The proxies' `remote`-lane listener: the chassis's model and tool proxies (013 CH-2, 054 H-16) also listen on the pod IP for this lane, and each remote authenticates. suggested: mTLS through cert-manager, or a per-remote token set in the chassis config and mounted into the remote pod as its only secret. Only the remote pod's label may reach the listener.
- One auth adapter for the first cloud's managed runtime, as decided in 001 DEC-1: SigV4 or OAuth 2.0 for AWS Bedrock AgentCore, or Google auth for Vertex AI Agent Engine.
- Managed runtimes are reached, not hosted. Their model and tool URLs point at the chassis's proxies, with the remote credential above. A managed runtime that cannot be pointed at the proxies holds its own scoped LiteLLM key and MCP gateway grant instead: the one relaxation of ADR-001 hard requirement 1, recorded in `spec.engine` and scoped like the chassis's own key.
- `spec.trust: trusted | untrusted`. A workload is trusted only if it passes both tests:
  - **Source:** the owning team wrote and reviewed it, and its image is built in our registry.
  - **Behavior:** it does not run code, shell commands, or file writes itself.
- An admission policy, in Kyverno or ValidatingAdmissionPolicy. It stops a workload marked `untrusted`, or an image not from our registry, from deploying in the `sidecar` lane.
- Generated code run through the code-execution tool behind `ToolPort` (054 H-16) does not make an agent untrusted.
- A list, per kind of remote, of what the chassis cannot see or control: the remote's own internal calls.
- A CI job that runs a fake workload through the `remote` lane on every commit.

## Reuse

- **Use:** the a2a-sdk client from 009 CH-1; `kubernetes-sigs/agent-sandbox` and a gVisor RuntimeClass for sandboxed pods; AWS SigV4 or OAuth 2.0 for AgentCore and Google auth for Vertex AI Agent Engine, per the first cloud; Kyverno or ValidatingAdmissionPolicy for the admission check.
- **Build:** the `remote` connector, one cloud auth adapter, the admission policy, and the CI job with a fake remote workload.
- **Watch:** agent-sandbox reached v1.0 in 2026, so pin it. On EKS, gVisor or Kata must be installed on the nodes.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- The second cloud's auth adapter (116 X-1b).
- An OpenAI-compatible connector for workloads that cannot speak A2A. It is added only when such a workload exists.
- The minimum-version rule (056 CH-7), which joins the same policy set.
- The registry's trust levels (080 R-7). The backlog plan's gaps list says how they map to `spec.trust`.

## Acceptance criteria

- [ ] A fake workload runs through the `remote` lane in CI on every commit, and passes the same contract suite as the `sidecar` lane.
- [ ] A hostile workload in the sandboxed pod cannot read secrets, reach the internet, write outside its scratch space, or call a tool that is not allow-listed.
- [ ] A remote pod without its credential is refused by the proxies, and a remote pod with it still cannot reach LiteLLM or the MCP gateway directly.
- [ ] The admission check refuses an `untrusted` workload, and a third-party image, in the `sidecar` lane.
- [ ] One agent on the first cloud's managed runtime is called through the `remote` lane with its auth adapter, streaming and complete.
- [ ] Every pipeline stage runs on remote calls. What the chassis cannot control is listed per kind of remote.
- [ ] gVisor's latency and memory overhead are measured and recorded.

## Dependencies

- Depends on: [009 CH-1](009-CH-1-engine-connectors-a2a.md), [026 CH-4](026-CH-4-chassis-only-credentials-egress.md), [010 H-12](010-H-12-config-loader.md), [038 X-1a](038-X-1a-terraform-first-cloud.md)
- Blocks: [056 CH-7](056-CH-7-chassis-release-rings.md), [059 H-15](059-H-15-agent-factory-cli.md), [077 C-1](077-C-1-governance-enforcement.md), [116 X-1b](116-X-1b-terraform-second-cloud.md)

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.3](../slm-agent-platform-epic-v3.md#g3) · [G.4](../slm-agent-platform-epic-v3.md#g4) · [H](../slm-agent-platform-epic-v3.md#app-h)
- Backlog plan: [000-plan.md](000-plan.md)
