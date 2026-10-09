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
- Status after PoC-1: the workload rides in the chassis image, PoC-1 only, because the lane is `inprocess`. The chassis container is the only one with `LITELLM_API_KEY`; the fake model server has no `LITELLM_*` variable (checked live). Evidence: `pocs/poc-01-walking-skeleton/tests/test_compose.py::test_keys_stay_in_env_and_reach_one_service_each`. The negative suite for the workload container starts in PoC-2, when the sidecar arrives.
- Status after PoC-2: the negative suite for workload containers exists offline. No workload service, image, or source holds or reads a key, and the Compose overlay gives no workload an `env_file` or a port (`pocs/poc-02-two-engines-one-contract/tests/test_boundaries.py`, `tests/test_compose_sidecar.py`). The live check ran on 2026-10-01: `deploy/compose/demo-sidecar.sh` found no key-like variable in any of the four workload containers and could not reach the workload port from the host ([demo](../../../pocs/poc-02-two-engines-one-contract/demo/2026-10-01-demo-sidecar.md); `tests/test_compose_sidecar.py::test_demo_runs_every_workload_with_no_key`). The chassis's public port binds the container address and leaves localhost to the proxies (`tests/test_compose_sidecar.py::test_chassis_public_port_leaves_localhost_to_the_proxies`). Open, all due by PoC-5: `llama-cpp` serves the `local` variant with no key, so a sidecar workload can call a model directly (`deploy/compose/SECURITY.md`, section 6); the trace id that keys proxy calls to a run is not a credential (suggested: a chassis-minted per-run credential); uncorrelated model calls are served with no cap; the router key is not yet scoped per service. Two more cases for the PoC-5 hostile suite: the workload cannot reach a model server directly, and the workload cannot bind the chassis's ports. See [the PoC-2 debt note](../../../pocs/poc-02-two-engines-one-contract/notes/2026-10-01-debt.md).
- Status after PoC-3: the trace id now comes from an inbound `traceparent` on every public interface and is echoed in `x-trace-id`, so the per-run credential must not derive from it. The hostile suite must also probe the public interfaces from inside a workload: `/v1/mcp`, `POST /v1/chat/completions` on the public port (the OpenAI interface, not the model proxy), `/v1/messages`, and `/v1/run`. LiteLLM's MCP gateway runs in front of `/v1/mcp` in the PoC-3 demo with `allow_all_keys: true` (fake variant, no keys); the per-key or per-team tool grant, and the sign-off of that setting, are this issue's. See [the PoC-3 debt note](../../../pocs/poc-03-one-interface-every-client/notes/2026-10-01-debt.md).
- Status after PoC-4: the scale stack keeps every secret on the chassis (the Valkey password, its own read-only MinIO user, and in the Dapr variant the two Dapr tokens), generated per run into a git-ignored file; no workload env holds one (`pocs/poc-04-stateless-scalable/tests/test_read_only_compose.py`). Open for this issue: Kafka is PLAINTEXT with no credential (with 020 X-8); Valkey has one password, not a per-service user limited to its key prefix, and no TLS; the Valkey password shows on `valkey-server`'s command line; Compose egress is open (PoC-5 checks it in kind). If Dapr is ever kept ([ADR-004](../adr/004-events-through-a-broker-client.md) proposes not), its metrics port 9090 and its internal gRPC port bind every interface with no auth and mTLS off: Sentry mTLS, or both bound to 127.0.0.1, and metrics off.
- Status after PoC-5, on kind ([sidecar suite](../../../pocs/poc-05-sandboxed/notes/2026-10-08-sidecar-suite.md), [close runs](../../../pocs/poc-05-sandboxed/notes/2026-10-09-close-runs.md)): hard requirement 1 holds. From the sidecar workload, each service refuses a call without the chassis's credential, and the same call through the chassis works: LiteLLM with a master key and one virtual key per service (`allowed_routes` limited to `openai_routes` and `mcp_inference_routes`, no `allow_all_keys` in kind or Compose), Valkey with its `default` user off and an ACL user, MinIO key pairs, and Kafka with SASL/SCRAM and no PLAINTEXT listener. H11 passed: a login with no credential, an unknown user, and a guessed password each get the broker's error 58, and the chassis's publish lands on its topic (`test_poc05_kind_hardreq1.py::test_kafka_refuses_the_workload_without_the_chassis_credential`). The fake servers and the code runner are closed by NetworkPolicy only. H01 is refused against a listener at 169.254.169.254 on the node; a real cloud metadata service needs a cloud rerun (038 X-1a). Open: DNS in the sidecar lane (H20); `chassis serve --host 0.0.0.0` has no CLI rule, so the pod-IP bind is held by the manifests and their static test (B6); PoC-3's live MCP demo lists 0 tools now that `allow_all_keys` is gone (suggested: rerun it with a minted key). Fixed in PoC-5: LiteLLM's 401 body and its INFO log echo a refused key's last 4 characters and hash; the chassis's `_redact` now strips both, and the `kubectl logs` dumps in CI and `run.sh` are filtered.
- Status after PoC-6: the Mac command (`make poc06-mac`) generates one random key per run, which LiteLLM takes as its master key and the host chassis processes use. This is accepted debt: a chassis should hold a scoped virtual key (`POST /key/generate` with `models`, `duration`, `max_budget`). Mitigations: loopback only, random per run, never written to a file, two fixed routes, workloads get no key ([debt note](../../../pocs/poc-06c-pretrained-slm/notes/2026-10-09-mac-command-debt.md), `deploy/compose/SECURITY.md` section 1). The fix needs a database behind LiteLLM, as in the kind stack. Why: one scoped key per service is this issue's.

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
