---
title: "S-5: Reproducible training pipeline (config, data version, seed) with Unsloth or TRL"
labels: ["story", "priority:P0", "phase:2-simplifier-slm", "area:slm", "size:M", "layer:platform"]
milestone: "W3 Simplifier SLM"
index: 33
epic_id: S-5
depends_on: ["030 S-4", "032 M-1"]
blocks: ["034 C-8", "035 S-6", "063 S-8", "073 E-1"]
epic_refs: [E.3, H]
---

## Why

Every model must be rebuilt from its config, data version, and seed, so results can be checked and audited (E.3, ISO 42001 A.6). One pipeline serves the simplifier now, and the tool-call SLM (063 S-8) and the evaluator SLM (073 E-1) later. It needs the reviewed data and test set (030 S-4) and MLflow (032 M-1).

## What

- A training config file: base model, LoRA settings, hyperparameters, data version, rules version, and seed.
- Unsloth (LoRA) by default, with Hugging Face TRL as the alternative, picked by config.
- The training split read by data version ID. The held-out test set is never read in training.
- Fixed seeds and pinned library versions (the uv lock file) in a container image for the job.
- Everything logged to MLflow through the 032 M-1 helper: config, data version, seed, git commit, metrics, and the trained weights.
- At the end of every run, S-3 scores on the held-out test set logged to MLflow.
- Runs on one GPU: local, rented, or cloud.
- One command to train, and one command to re-run a past MLflow run.

## Reuse

- **Use:** Unsloth (Apache 2.0 core; do not ship its AGPL Studio UI) or TRL 1.x (SFT, DistillationTrainer), with MLflow autologging.
- **Build:** the pipeline config, seeds, and the data-version wiring.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Training compute logging and the GPAI check (034 C-8).
- Training the candidates and picking the best (035 S-6).
- The eval gate (037 M-2) and GPU setup on Kubernetes (039 X-2).

## Acceptance criteria

- [ ] One command trains a LoRA model from a config file and logs the run in MLflow.
- [ ] Re-running a logged run from its config, data version, and seed gives held-out scores within a set tolerance (suggested: ±0.01 `facts_kept`).
- [ ] A run without base model, data version, seed, config, or git commit fails to start.
- [ ] A run fails if any held-out test ID is in its training data.
- [ ] Every run ends with S-3 scores on the held-out test set in MLflow.
- [ ] A small smoke run works with both Unsloth and TRL.

## Dependencies

- Depends on: [030 S-4](030-S-4-hand-review-test-set.md), [032 M-1](032-M-1-mlflow.md)
- Blocks: [034 C-8](034-C-8-training-compute-log.md), [035 S-6](035-S-6-train-candidates.md), [063 S-8](063-S-8-tool-call-finetune.md), [073 E-1](073-E-1-evaluator-slm.md)

## References

- Epic story: [S-5 in Phase 2](../slm-agent-platform-epic-v3.md#phase-2-simplifier-slm-and-model-lifecycle)
- Epic context: [E.3](../slm-agent-platform-epic-v3.md#e3) · [H](../slm-agent-platform-epic-v3.md#app-h)
- Backlog plan: [000-plan.md](000-plan.md)
