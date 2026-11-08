---
title: "CH-4: Chassis-only credentials for every internal service, and default-deny egress per workload pod"
labels: ["story", "priority:P0", "phase:0-harness", "area:harness", "size:L", "layer:chassis"]
milestone: "W2 Chassis MVP"
index: 26
epic_id: CH-4
depends_on: ["002 G-1", "024 CH-3"]
blocks: ["038 X-1a", "041 A-1", "055 CH-6", "132 CH-13", "139 CH-19"]
epic_refs: [G.3, G.4]
---

## Why

This is hard requirement 1 of [ADR-001](../adr/001-chassis-delivery-model.md). The workload shares the pod's network, so it can reach every address the chassis can reach. What stops it is that every internal service requires a credential that only the chassis holds. Without those credentials the workload gets nowhere, and it can use models and tools only through the chassis. The ADR makes this a release gate for every service, so it comes before the simplifier ships (041 A-1).

## What

- One set of credentials per service, never shared:
  - a LiteLLM virtual key (models, budget, rate limit). The 002 G-1 part this issue needs, a virtual key per service minted at deploy, is met on kind (PoC-5);
  - MCP gateway tool grants on that key;
  - a broker credential (its topic ACLs are 020 X-8's);
  - a Valkey user, limited to its own key prefix;
  - read access to its own path in the config store.
- LiteLLM, the MCP gateway, the broker, Valkey, and the config store refuse calls that carry no credential. The LiteLLM master key never leaves LiteLLM. The PoC-8 LiteLLM drill mints the chassis a virtual key, and the master key stays in LiteLLM (`deploy/compose/poc08-litellm/stack.sh`).
- All credentials are mounted only in the chassis container: from Vault through External Secrets in the cluster, and from `.env` locally.
- A NetworkPolicy per service pod. It denies all egress by default, and allows only what the chassis needs: LiteLLM, the MCP gateway, the broker, Valkey, the config store, the OpenTelemetry Collector, and DNS.
- Decided in the chart (`deploy/helm/agent-lib`, `_networkpolicy.tpl` and `_validate.tpl`): no `ipBlock`, only named peers, so the metadata service and the API server are never reachable. Every pod the chart renders is default deny both ways, every egress rule names a peer by namespace and pod labels and a port, and each namespace the chart renders pods into gets `<name>-default-deny`. The static check `pocs/poc-05-sandboxed/tests/test_poc05_netpol_static.py::test_poc05_netpol_no_rule_can_reach_metadata_or_api` is the evidence once it runs against `agent-lib`.
- The admission rules in the chart: `agent-lib.admission` renders the PoC-5 trust rule, with the same policy spec as `deploy/kind/poc05/admission/policy.yaml`, and rule 9, the minimum chassis version from the library's tag list, as its own policy `agent-chassis-version` (`deploy/kind/poc05/admission/chassis-version-policy.yaml`), so that its binding can warn first: a `ValidatingAdmissionPolicyBinding` carries one action list for the whole policy. `admission.chassisVersionAction` on the platform chart is `Deny` (the default) or `Warn`. `deploy/helm/agent-admission` installs both once per cluster. The chart's image defaults (`pullPolicy: IfNotPresent`, an empty digest) still fail rule 8.
- The chassis's public port binds to the pod IP, never to localhost. Localhost carries only the model and tool proxies, so the workload cannot call the service's own API under the chassis's credentials. The manifests and the chart's argument allow-list hold the bind (`pocs/poc-02-two-engines-one-contract/tests/test_compose_sidecar.py::test_chassis_public_port_leaves_localhost_to_the_proxies`).
- Network labels per service.
- A negative suite run from inside the workload container. It also probes the public interfaces: `/v1/mcp`, `/v1/chat/completions` and `/v1/messages` on the public port (not the model proxy), and `/v1/run`.
- A revoke step: revoking one service's key, tool grants, and network label cuts that service off alone.
- The trace id keys proxy calls to a run, but it comes from an inbound `traceparent`, so it is not a credential. A chassis-minted per-run credential is 001 DEC-1's (suggested). It is built for the `remote` listener (contract v8 D, [ADR-011](../adr/011-per-run-secret-and-v0-wire-bounds.md), PR #85). The loopback proxy still keys calls by trace id alone.
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
- The admission rules for the `remote` lane and for release rings. 055 CH-6 and 056 CH-7 extend the rules this issue puts in the chart.
- Running the negative suite before every chassis release (056 CH-7).
- The cloud metadata service on a real cloud (038 X-1a). On kind, PoC-5 refused a listener at 169.254.169.254 on the node.
- Egress on Compose: kind is the check. The Dapr ports: Dapr is in no profile ([ADR-004](../adr/004-events-through-a-broker-client.md)).
- The Mac command's random master key (`make poc06-mac`), a test-only stack ([debt note](../../../pocs/poc-06c-pretrained-slm/notes/2026-10-09-mac-command-debt.md)).
- `exfil-04-markdown-image`: the chassis output URL allow-list stops it (022 H-6).
- The CLI bind rule: P-13 in the [backlog review proposals](../../reviews/2026-11-06-backlog-review/proposals.md). `chassis serve --host 0.0.0.0` has no CLI rule today.

## Acceptance criteria

- [x] From the workload container, calls to LiteLLM, the MCP gateway, the broker, Valkey, and the config store all fail. · Delivered in PoC-5: `pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py::test_litellm_refuses_the_workload_without_the_chassis_key`, `::test_mcp_gateway_refuses_the_workload_without_the_chassis_key`, `::test_valkey_refuses_the_workload_without_the_chassis_password`, `::test_minio_refuses_the_workload_without_the_chassis_key`, `::test_kafka_refuses_the_workload_without_the_chassis_credential` (`pocs/poc-05-sandboxed/notes/2026-10-08-sidecar-suite.md` "4 passed", `pocs/poc-05-sandboxed/notes/2026-10-09-close-runs.md` "1 passed")
- [x] Through the chassis, the same calls succeed with the service's own credentials, MinIO included. · Delivered by: PR #92 and PR #100, `pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py::test_minio_refuses_the_workload_without_the_chassis_key` and the LiteLLM, MCP gateway, Valkey, and Kafka tests in the same file; remote-lane run 42503042654
- [ ] One service's credentials cannot use another service's models, tools, or cache keys.
- [ ] With `agent-lib`'s NetworkPolicy on a CNI that enforces it, these all fail from the workload container: any public address, any other service's pod, the cloud metadata service, the Kubernetes API, a DNS lookup used to leave the cluster (H20), and the chassis's public port on localhost.
- [ ] Revoking one service's key, grants, and label cuts it off, and the other services keep working.
- [x] The negative suite runs on the local kind cluster in `remote-lane.yml` on every push: the sidecar controls, hard requirement 1, the tool gateway, and the admission suites. · Delivered by: PR #79, `pocs/poc-05-sandboxed/tests/test_poc05_ci_wiring.py::test_poc05_test_remote_runs_the_whole_sidecar_hostile_suite`, `.github/workflows/remote-lane.yml` run 42318146945 on `main` at 273b6e0 (175 passed, 2 skipped, 2 xfailed, the broker pass included) Since 2026-11-08 `remote-lane.yml` runs by hand (`workflow_dispatch`): PoC-5 is finished and checked locally ([what CI runs](../../guides/testing.md#what-ci-runs)).
- [x] `agent-lib` carries the admission rules (untrusted must use `remote`; a minimum chassis version), and a render test plus a kind run refuse a pod that breaks them. · Delivered by: PR #79, `pocs/poc-09-agent-mvp-template/tests/test_poc09_chart_boundary.py::test_an_untrusted_sidecar_pod_is_refused_at_render_and_by_rule_2`, `::test_a_chassis_below_the_minimum_version_is_refused_at_render_and_by_rule_9`, `::test_the_admission_chart_renders_the_poc05_trust_rule_unchanged` (the chart's policy spec equals the kind file's); on kind, `pocs/poc-05-sandboxed/tests/test_poc05_kind_admission.py::test_rejected_fixture_is_refused_with_its_rule_message` with `deploy/kind/poc05/admission/fixtures/rule2-untrusted-sidecar` and `deploy/kind/poc05/admission/fixtures/rule9-chassis-version`, remote-lane run 42318146945
- [ ] The chart's defaults pass every PoC-5 admission rule, rule 8 (image by digest) included. Open: only `deploy/helm/example-agent/values-kind.yaml` passes today (`pocs/poc-09-agent-mvp-template/tests/test_poc09_chart_boundary.py::test_every_pod_the_chart_renders_passes_the_chart_admission_rules`).
- [ ] The LiteLLM service key has a `duration`, and a `seed.sh rekey` mints a new key and revokes the old one while the service keeps answering, on kind (gap review, 2026-11-07).
- [ ] A new minimum chassis version warns before it refuses, as ADR-001 item 9 decides: rule 9's binding runs with `validationActions: [Warn, Audit]` for one ring cycle (suggested), then `[Deny, Audit]`. A render test shows both modes, and a kind run shows the warning on a pod below the minimum. Added 2026-11-08 from the plan's phase A; the binding is `[Deny, Audit]` from the start today.

## Dependencies

- Depends on: [002 G-1](002-G-1-litellm-router.md), [024 CH-3](024-CH-3-helm-library-chart.md)
- Blocks: [038 X-1a](038-X-1a-terraform-first-cloud.md), [041 A-1](041-A-1-simplifier-core.md), [055 CH-6](055-CH-6-remote-lane-trust-rule.md), [132 CH-13](132-CH-13-owasp-agentic-map.md), [139 CH-19](139-CH-19-reference-stack.md)

## References

- Source: [ADR-001](../adr/001-chassis-delivery-model.md), not an epic story. Epic phase: [Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [G.3](../slm-agent-platform-epic-v3.md#g3) · [G.4](../slm-agent-platform-epic-v3.md#g4)
- Backlog plan: [000-plan.md](000-plan.md)
