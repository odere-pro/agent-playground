---
title: "CH-4: Chassis-only credentials for every internal service, and default-deny egress per workload pod"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:M", "layer:chassis"]
milestone: "W2 Chassis MVP"
index: 26
epic_id: CH-4
depends_on: ["002 G-1", "020 X-8", "022 H-6", "025 H-10"]
blocks: ["038 X-1a", "041 A-1", "055 CH-6"]
epic_refs: [G.3, G.4]
---

## Why

This is hard requirement 1 of [ADR-001](../adr/001-chassis-delivery-model.md). The workload shares the pod's network, so it can reach every address the chassis can reach. What stops it is that every internal service requires a credential that only the chassis holds. Without those credentials the workload gets nowhere, and it can use models and tools only through the chassis. The ADR makes this a release gate for every service, so it comes before the simplifier ships (041 A-1).

## What

- One set of credentials per service, never shared:
  - a LiteLLM virtual key (models, budget, rate limit);
  - MCP gateway tool grants on that key;
  - a broker credential, with ACLs for the topics in its `spec.events`;
  - a Valkey user, limited to its own key prefix;
  - read access to its own path in the config store.
- LiteLLM, the MCP gateway, the broker, Valkey, and the config store refuse calls that carry no credential. The LiteLLM master key never leaves LiteLLM.
- All credentials are mounted only in the chassis container: from Vault through External Secrets in the cluster, and from `.env` locally.
- A NetworkPolicy per service pod. It denies all egress by default, and allows only what the chassis needs: LiteLLM, the MCP gateway, the broker, Valkey, the config store, the OpenTelemetry Collector, and DNS. The cloud metadata service (169.254.169.254) and the Kubernetes API are named in the deny list, so they stay closed even if an allow rule is widened later.
- The chassis's public port binds to the pod IP, never to localhost. Localhost carries only the model and tool proxies, so the workload cannot call the service's own API under the chassis's credentials.
- Network labels per service.
- A negative test suite run from inside the workload container.
- A revoke step: revoking one service's key, tool grants, and network label cuts that service off alone.
- If the pod has a Dapr sidecar, its API needs a token that only the chassis holds (gap (c) in the backlog plan).

## Reuse

- **Use:** LiteLLM virtual keys, the LiteLLM MCP gateway per-key tool grants, broker ACLs, Valkey ACLs, bucket policies, and Kubernetes NetworkPolicy with a CNI that enforces it (for example Cilium or Calico).
- **Build:** the per-service credential set, the default-deny egress policy in the library chart, and the negative tests from the workload container.
- **Watch:** per-key guardrails in LiteLLM are Enterprise-only.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- The sandboxed pod and the `remote` lane (055 CH-6).
- mTLS between services (038 X-1a).
- The playbook for a compromised agent (119 X-6).
- Guardrails per key. LiteLLM makes them Enterprise-only (gap (d) in the backlog plan).

## Acceptance criteria

- [ ] From the workload container, calls to LiteLLM, the MCP gateway, the broker, Valkey, and the config store all fail.
- [ ] Through the chassis, the same calls succeed with the service's own credentials.
- [ ] One service's credentials cannot use another service's models, tools, topics, or cache keys.
- [ ] From the workload container, these all fail: any public address, any other service's pod, the cloud metadata service, the Kubernetes API, and the chassis's public port on localhost.
- [ ] Revoking one service's key, grants, and label cuts it off, and the other services keep working.
- [ ] The negative suite runs on the local cluster on every merge and before every chassis release.

## Dependencies

- Depends on: [002 G-1](002-G-1-litellm-router.md), [020 X-8](020-X-8-event-broker.md), [022 H-6](022-H-6-security-middleware.md), [025 H-10](025-H-10-template-repo.md)
- Blocks: [038 X-1a](038-X-1a-terraform-first-cloud.md), [041 A-1](041-A-1-simplifier-core.md), [055 CH-6](055-CH-6-remote-lane-trust-rule.md)

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.3](../slm-agent-platform-epic-v3.md#g3) · [G.4](../slm-agent-platform-epic-v3.md#g4)
- Backlog plan: [000-plan.md](000-plan.md)
