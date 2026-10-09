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
- Status after PoC-5, on kind ([remote suite](../../../pocs/poc-05-sandboxed/notes/2026-10-08-remote-suite.md), [close runs](../../../pocs/poc-05-sandboxed/notes/2026-10-09-close-runs.md)): the first build. The `remote` connector with one bearer token per remote, used both ways (mTLS is the next step, [ADR-005](../adr/005-remote-lane-auth-and-trust-admission.md), accepted 2026-10-09). The remote listener on the pod IP answers 401 `remote_unauthenticated` and 403 `run_required`. The remote pod runs on gVisor through agent-sandbox, with no DNS and one egress edge, to 8091. `spec.trust` and the `agent-trust-rule` ValidatingAdmissionPolicy, rules 0 to 8, run on the API server (`test_poc05_kind_admission.py`, each rejected fixture next to its admitted twin), plus rules for `SandboxTemplate` (T1 to T5) and `SandboxClaim` (C1 to C5). Criterion 1's kind half runs in CI: `.github/workflows/remote-lane.yml` runs `up test-remote` on push and pull request, with the gVisor, kind, and kubectl sums pinned; it passed on GitHub with gVisor `release-20260928.0` on x86_64 ([run 37942450871](https://github.com/odere-pro/agent-playground/actions/runs/37942450871)). The code runner is a dispatcher that claims a fresh gVisor sandbox per call from a warm pool and deletes the claim after; 10 of 10 runs of its kind file passed. gVisor's cost: start about 1 to 2 s slower, a request 0.3 to 0.5 ms, a `run_python` call about 74 ms, and 33 to 38 MiB per pod ([gVisor overhead](../../../pocs/poc-05-sandboxed/notes/2026-10-09-gvisor-overhead.md)). Recorded, not fixed: the dispatcher holds a Kubernetes token and the one `ipBlock`, to the API server on 6443 (a B4 exception); a stolen dispatcher token can delete other callers' claims (a denial of service); runsc ignores the pod's `/dev/shm` size cap, bounded by the pod's memory limit (accepted, this issue owns it; a runsc mount option is a lead). Any principal that may create pods in a namespace can read its Secrets, so the submitter lost pod create in `poc05-agents` and `poc05-tools`; the deployer stays inside the credential boundary, held by admission rules 6a to 6c (ADR-005, Consequences). The probe workload that would run a hostile workload inside the pod (T10) is a recorded exception, work in progress, owned by Oleksandr ([the T10 note](../../../pocs/poc-05-sandboxed/notes/2026-10-09-t10-probe-exception.md)); the criterion "a hostile workload in the sandboxed pod cannot ..." stays open until it runs. Open: the managed-runtime auth adapter and its CI job. What the chassis cannot see or control in the remote lane is in [the blind-spots note](../../../pocs/poc-05-sandboxed/notes/2026-10-02-blind-spots.md).
- Status after PoC-6 ([engines on kind](../../../pocs/poc-06b-bake-off-remote-lane/notes/2026-10-09-lanes-b-kind.md), [kagent probe](../../../pocs/poc-06b-bake-off-remote-lane/notes/2026-10-09-kagent-probe.md), [contract v5](../../contracts/contract-v5.md)): `spec.engine.protocol: a2a` reads a third-party agent's own A2A stream in the `remote` connector (opt-in, `remote` only, fixed error text, strict usage). Remotes on gVisor: smolagents, the Claude Agent SDK, the TypeScript agent, and `kagent-adk` (kagent's Python runtime, built from a pinned commit; full kagent on Kubernetes is a no-go). Admission: `trustedRepositories` gained `echo-openai-agents`; no other rule changed. What the chassis cannot see for `kagent-adk`: tool calls, state, and whether the runtime checks the bearer (it does not). The kind results wait on the first `poc06-kind.yml` run; no acceptance box is ticked from it yet.

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
