---
title: "X-1a: Terraform module for the first cloud (AWS or GCP)"
labels: ["story", "priority:P0", "phase:8-production", "area:infra", "size:L", "layer:platform"]
milestone: "W3 Simplifier SLM"
index: 38
epic_id: X-1a
depends_on: ["001 DEC-1", "025 H-10", "026 CH-4"]
blocks: ["039 X-2", "055 CH-6", "116 X-1b", "117 X-3", "121 X-4"]
epic_refs: [G.4, H]
---

## Why

Split from X-1 and pulled forward from Phase 8: the simplifier needs a real environment to show savings. This issue builds dev, staging, and prod on the first cloud chosen in 001 DEC-1, all in Terraform. 116 X-1b adds the second cloud with the same inputs and outputs.

## What

- One Terraform module for the first cloud, per DEC-1 (AWS or GCP): a Kubernetes cluster, networking, IAM, and object storage buckets for the config store, MLflow artifacts, and datasets.
- Kubernetes 1.33 or later, so the chassis runs as a native sidecar ([ADR-001](../adr/001-chassis-delivery-model.md)).
- Cloud-neutral module inputs and outputs (for example cluster name, region, node pools, and bucket names in; cluster endpoint and bucket URLs out), so 116 X-1b can match them.
- The environments dev, staging, and prod, each with its own remote state and locking.
- Cluster add-ons installed through Argo CD: CloudNativePG (Postgres 17 with pgvector), Vault with External Secrets, cert-manager, Kyverno, and the observability stack.
- What the chassis lanes need from the cluster:
  - a gVisor RuntimeClass for `remote` pods: GKE Sandbox on GCP, or gVisor or Kata on EKS nodes;
  - a CNI that enforces NetworkPolicy, so default-deny egress holds;
  - IAM for the `remote` lane's cloud auth adapter: Workload Identity on GCP, or IRSA on AWS.
- One LiteLLM virtual key per service, with its MCP gateway grants, provisioned from code with no manual steps (suggested: a Terraform provider or a bootstrap job run by Argo CD).
- A GPU node pool declared with spot or preemptible nodes and a minimum of zero. Drivers come in 039 X-2.
- A versioned config store bucket, and the router and broker deployed to staging from their Helm charts. The echo agent is deployed through its chart, which includes the shared library chart (024 CH-3).

## Reuse

- **Use:** Kubernetes 1.33 or later, a gVisor RuntimeClass (GKE Sandbox on GCP; gVisor or Kata on EKS nodes), a CNI that enforces NetworkPolicy, and Workload Identity or IRSA for the remote-lane auth adapter.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- GPU Operator and model weight storage (039 X-2).
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
- [ ] The cluster refuses unsigned images.
- [ ] A test pod with the gVisor RuntimeClass runs, and reports the gVisor kernel.
- [ ] Default-deny egress holds: a test pod with the service's NetworkPolicy cannot reach a public address or another service's pod.
- [ ] The echo agent's pod runs two containers, the chassis and the workload, from the library chart.
- [ ] Each service's virtual key and tool grants are provisioned with no manual steps.

## Dependencies

- Depends on: [001 DEC-1](001-DEC-1-resolve-open-decisions.md), [025 H-10](025-H-10-template-repo.md), [026 CH-4](026-CH-4-chassis-only-credentials-egress.md)
- Blocks: [039 X-2](039-X-2-gpu-setup.md), [055 CH-6](055-CH-6-remote-lane-trust-rule.md), [116 X-1b](116-X-1b-terraform-second-cloud.md), [117 X-3](117-X-3-slos-alerts.md), [121 X-4](121-X-4-backups-restore.md)

## References

- Epic story: [X-1a in Phase 8](../slm-agent-platform-epic-v3.md#phase-8-production-readiness)
- Epic context: [G.4](../slm-agent-platform-epic-v3.md#g4) · [H](../slm-agent-platform-epic-v3.md#app-h)
- Backlog plan: [000-plan.md](000-plan.md)
