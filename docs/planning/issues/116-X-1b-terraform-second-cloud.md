---
title: "X-1b: Terraform module for the second cloud, with the same inputs and outputs"
labels: ["story", "priority:P1", "phase:8-production", "area:infra", "size:L", "layer:platform"]
milestone: "W10 Production readiness"
index: 116
epic_id: X-1b
depends_on: ["038 X-1a", "055 CH-6"]
blocks: []
epic_refs: [G.4, H]
---

## Why

The epic says the platform deploys to AWS or GCP with the same Terraform inputs and outputs. X-1 is split because the Phase 8 done-when needs both clouds: the first cloud (038 X-1a) came early so the simplifier could ship, and this issue adds the second. Doing it now keeps the first module stable before it is mirrored.

## What

- A Terraform module for the cloud not picked first in DEC-1 (AWS or GCP), with the same input variables and outputs as the X-1a module.
- The same resources: a Kubernetes cluster, node pools (CPU, and GPU on spot or preemptible nodes), object storage buckets (config store, datasets, model weights, and the audit log with object lock), networking, and IAM.
- Postgres stays on Kubernetes with CloudNativePG, so the database is the same on both clouds.
- Cluster add-ons deploy the same way: Helm and Argo CD, Vault with External Secrets, cert-manager, Kyverno, KEDA, and the NVIDIA GPU Operator.
- What the chassis lanes need, as on the first cloud ([ADR-001](../adr/001-chassis-delivery-model.md)):
  - Kubernetes 1.33 or later, for native sidecars;
  - a gVisor RuntimeClass for `remote` pods: GKE Sandbox on GCP, or gVisor or Kata on EKS nodes;
  - a CNI that enforces NetworkPolicy, so default-deny egress holds;
  - the `remote` lane's auth adapter for this cloud's managed runtime (SigV4 or OAuth 2.0 for AWS Bedrock AgentCore, or Google auth for Vertex AI Agent Engine), and its IAM (IRSA or Workload Identity). 055 CH-6 built the first cloud's adapter.
- Dev, staging, and prod environments from the same module.
- A contract check in CI that fails if the two modules' inputs or outputs differ, or if the two clusters run different Kubernetes minor versions.

## Out of scope

- Changes to the first cloud's module, except to keep the contract the same (038 X-1a).
- SLOs and alerts (117 X-3).
- Backups and restore (121 X-4).

## Acceptance criteria

- [ ] `terraform apply` on a clean account creates a staging environment on the second cloud.
- [ ] Argo CD deploys the same platform charts there, with no cloud-specific values beyond the module outputs.
- [ ] A smoke test passes on both clouds: the echo agent and the simplifier answer through all APIs, and a fan-out workflow runs (Phase 8 done-when: deploys to both clouds from Terraform).
- [ ] The CI contract check shows the same inputs and outputs for both modules, and the same Kubernetes minor version on both clusters.
- [ ] On the second cloud, a test pod with the gVisor RuntimeClass runs, and default-deny egress holds: a test pod cannot reach a public address or another service's pod.
- [ ] A fake workload on the second cloud's managed runtime answers through the `remote` lane, with the new auth adapter.
- [ ] Audit log buckets have object lock on both clouds.
- [ ] `terraform destroy` removes the environment with nothing left behind.

## Dependencies

- Depends on: [038 X-1a](038-X-1a-terraform-first-cloud.md), [055 CH-6](055-CH-6-remote-lane-trust-rule.md)
- Blocks: none

## References

- Epic story: [X-1b in Phase 8](../slm-agent-platform-epic-v3.md#phase-8-production-readiness)
- Epic context: [G.4](../slm-agent-platform-epic-v3.md#g4) · [H](../slm-agent-platform-epic-v3.md#app-h)
- Backlog plan: [000-plan.md](000-plan.md)
