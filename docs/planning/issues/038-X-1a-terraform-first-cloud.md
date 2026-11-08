---
title: "X-1a: Terraform module for the first cloud (AWS or GCP)"
labels: ["story", "priority:P0", "phase:8-production", "area:infra", "size:L", "layer:platform"]
milestone: "W3 Simplifier SLM"
index: 38
epic_id: X-1a
depends_on: ["001 DEC-1", "025 H-10", "026 CH-4"]
blocks: ["039 X-2", "056 CH-7", "116 X-1b", "121 X-4"]
epic_refs: [G.4, H]
---

## Why

Split from X-1. The first cloud must give the chassis the same cluster contract that PoC-5 proved on kind: a gVisor RuntimeClass, a CNI that enforces NetworkPolicy, the admission policy, Kubernetes 1.33 or later, and keys provisioned from code. This issue builds dev, staging, and prod on the first cloud chosen in 001 DEC-1, all in Terraform. 116 X-1b adds the second cloud with the same inputs and outputs.

## What

- One Terraform module for the first cloud, per DEC-1 (AWS or GCP): a Kubernetes cluster, networking, IAM, and object storage buckets for the config store and datasets.
- Kubernetes 1.33 or later, for native sidecars: the workload runs as one next to the chassis ([ADR-001](../adr/001-chassis-delivery-model.md), [ADR-012](../adr/012-drain-order-on-rollout.md)).
- Cloud-neutral module inputs and outputs (for example cluster name, region, node pools, and bucket names in; cluster endpoint and bucket URLs out), so 116 X-1b can match them.
- The environments dev, staging, and prod, each with its own remote state and locking.
- Cluster add-ons installed through Argo CD: CloudNativePG (Postgres 17 with pgvector), Vault with External Secrets, cert-manager, and the observability stack. Admission is the ValidatingAdmissionPolicy and its parameter file (`deploy/kind/poc05/admission/`), not Kyverno.
- What the chassis lanes need from the cluster:
  - a gVisor RuntimeClass for `remote` pods: GKE Sandbox on GCP, or gVisor or Kata on EKS nodes;
  - a CNI that enforces NetworkPolicy, so default-deny egress holds.
- Decided in [ADR-005](../adr/005-remote-lane-auth-and-trust-admission.md) (items 1, 4) and [ADR-006](../adr/006-agent-engines-default-supported-lanes.md): one bearer token per remote, a ValidatingAdmissionPolicy for the trust rule, and no managed runtime, so no cloud auth adapter.
- One LiteLLM virtual key per service, with its MCP gateway grants, provisioned from code with no manual steps (suggested: a Terraform provider or a bootstrap job run by Argo CD).
- A versioned config store bucket, and the router and broker deployed to staging from their Helm charts. The echo agent is deployed through its chart, which includes the shared library chart (024 CH-3).

## Reuse

- **Use:** Kubernetes 1.33 or later, a gVisor RuntimeClass (GKE Sandbox on GCP; gVisor or Kata on EKS nodes), a CNI that enforces NetworkPolicy, and Workload Identity or IRSA for the remote-lane auth adapter.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- GPU Operator and model weight storage (039 X-2).
- A GPU node pool (spot or preemptible, minimum zero) and the MLflow artifacts bucket: parked with the SLM track.
- KEDA agent pools (104 O-3).
- The second cloud (116 X-1b).
- A fake `remote` workload running in the cloud, and the admission check on `spec.trust` (055 CH-6).
- SLOs and alerts (117 X-3), and backups with a restore test (121 X-4).

## Acceptance criteria

- [ ] `terraform apply` builds staging from scratch with no manual steps, and `terraform destroy` removes it.
- [ ] The module's inputs and outputs are documented as the contract for the second cloud.
- [ ] Argo CD deploys the echo agent, the router, and the broker to staging, and the echo agent reads its config from the versioned config store bucket.
- [ ] Postgres runs on CloudNativePG with pgvector enabled.
- [ ] Secrets come from Vault through External Secrets; no secret value is in Terraform code or variables.
- [ ] The cluster refuses unsigned images (the half of a 025 H-10 box moved here on 2026-11-06).
- [x] A test pod with the gVisor RuntimeClass runs and reports the gVisor kernel (on kind; the cloud rerun is part of box 1). · Delivered in PoC-5: `pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_controls.py::test_h28_remote_runs_on_gvisor_next_to_a_runc_pod`; `pocs/poc-05-sandboxed/demo/2026-10-09-demo-sandboxed.md` sections 2 and 6; `pocs/poc-05-sandboxed/notes/2026-10-08-remote-suite.md`
- [x] Default-deny egress holds on kind: a remote-lane pod reaches no public address and no other pod. · Delivered in PoC-5: `pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_controls.py::test_h19_remote_reaches_no_outside_address` and `::test_h30_remote_reaches_no_other_pod`; `pocs/poc-05-sandboxed/tests/test_poc05_netpol_static.py::test_poc05_netpol_default_deny_in_every_namespace`; `pocs/poc-05-sandboxed/notes/2026-10-08-remote-suite.md`
- [ ] Argo CD deploys the echo agent from the library chart: its pod runs two containers, the chassis and the workload, and it answers on the native, OpenAI, and Anthropic APIs (moved in part from 025 H-10 on 2026-11-06). Open: `pocs/poc-09-agent-mvp-template/tests/test_poc09_chart.py` renders the chart only; the proof is a kind test that installs `deploy/helm/example-agent` and reads two ready containers (suggested name: `test_the_chart_pod_runs_chassis_and_workload`).
- [x] Each service's virtual key and tool grants are provisioned from code with no manual steps (kind: `seed.sh`). · Delivered in PoC-5: `deploy/kind/poc05/platform/seed.sh`; `pocs/poc-05-sandboxed/tests/test_poc05_seed_static.py::test_no_litellm_config_grants_an_mcp_server_to_every_key` and `::test_seed_is_idempotent_and_rotates_on_request`; `pocs/poc-05-sandboxed/tests/test_poc05_kind_tool_gateway.py::test_a_key_the_gateway_did_not_issue_gets_no_tool`; `pocs/poc-05-sandboxed/demo/2026-10-09-demo-sandboxed.md`

## Dependencies

- Depends on: [001 DEC-1](001-DEC-1-resolve-open-decisions.md), [025 H-10](025-H-10-template-repo.md), [026 CH-4](026-CH-4-chassis-only-credentials-egress.md)
- Blocks: [039 X-2](039-X-2-gpu-setup.md), [056 CH-7](056-CH-7-chassis-release-rings.md), [116 X-1b](116-X-1b-terraform-second-cloud.md), [121 X-4](121-X-4-backups-restore.md)

## References

- Epic story: [X-1a in Phase 8](../slm-agent-platform-epic-v3.md#phase-8-production-readiness)
- Epic context: [G.4](../slm-agent-platform-epic-v3.md#g4) · [H](../slm-agent-platform-epic-v3.md#app-h)
- Backlog plan: [000-plan.md](000-plan.md)
