---
title: "M-1: MLflow: experiments, model registry, links to data version and scores"
labels: ["story", "priority:P0", "phase:2-simplifier-slm", "area:lifecycle", "size:S", "layer:platform"]
milestone: "W3 Simplifier SLM"
index: 32
epic_id: M-1
depends_on: []
blocks: ["033 S-5", "037 M-2", "123 C-4"]
epic_refs: [E.5, H]
---

## Why

Every model must trace back to its data version, config, and scores. This is evidence for ISO 42001 A.6 (life cycle), and it later feeds the model card in the documentation pack (123 C-4). The training pipeline (033 S-5) and the eval gate (037 M-2) both write to MLflow, so it comes before them; it has no dependencies and can start any time.

## What

- An MLflow tracking server and model registry, with metadata in Postgres and artifacts in object storage (MinIO locally, S3 or GCS in the cloud).
- Runs in Docker Compose, with a Helm chart for Kubernetes.
- Conventions: one experiment per task (for example `simplifier`), and required run tags for data version, rules version, base model, training config, seed, and git commit.
- A small shared logging helper that sets the required tags and refuses a run without them.
- The model registry, with one registered model per task; each version links to the run that made it.
- Scores on the held-out test set logged per model version.
- Suggested: aliases such as `champion` and `challenger`, for the eval gate to use.
- A short guide to the conventions.

## Reuse

- **Use:** self-hosted MLflow 3: tracking, the model registry, and eval datasets.
- **Build:** the links from each model to its data version and scores.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- The training pipeline (033 S-5).
- Training compute logging and the GPAI check (034 C-8).
- The eval gate that decides which model ships (037 M-2).
- Model cards and the documentation pack (123 C-4).

## Acceptance criteria

- [ ] MLflow runs with `docker compose up`, with metadata in Postgres and artifacts in MinIO.
- [ ] A test run shows its parameters, metrics, and artifacts in the UI.
- [ ] The logging helper refuses to start a run that misses a required tag.
- [ ] A registered model version links to its run, its data version, and its scores.
- [ ] The Helm chart installs MLflow on a Kubernetes cluster with the same storage setup.
- [ ] The conventions guide is published.

## Dependencies

- Depends on: none
- Blocks: [033 S-5](033-S-5-training-pipeline.md), [037 M-2](037-M-2-eval-gate-ci.md), [123 C-4](123-C-4-documentation-pack.md)

## References

- Epic story: [M-1 in Phase 2](../slm-agent-platform-epic-v3.md#phase-2-simplifier-slm-and-model-lifecycle)
- Epic context: [E.5](../slm-agent-platform-epic-v3.md#e5) · [H](../slm-agent-platform-epic-v3.md#app-h)
- Backlog plan: [000-plan.md](000-plan.md)
