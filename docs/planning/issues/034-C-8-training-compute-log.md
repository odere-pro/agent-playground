---
title: "C-8: Training compute logged per fine-tune run, checked against the GPAI threshold"
labels: ["story", "priority:P1", "phase:9-governance", "area:governance", "size:S", "layer:platform"]
milestone: "W3 Simplifier SLM"
index: 34
epic_id: C-8
depends_on: ["033 S-5"]
blocks: []
epic_refs: [E.3, R11]
---

## Why

Under the Commission's GPAI guidelines, a company that modifies a model with more than one third of its original training compute becomes the provider of a new general-purpose model [R11]. LoRA fine-tunes of small models should stay far below that, but it must be checked per model and logged as evidence (E.3). This is pulled forward from Phase 9: it is cheapest to add while the training pipeline is being built.

## What

- Compute logged per run: GPU type, GPU count, GPU-hours, and estimated FLOPs, with the method used (suggested: 6 × parameters × training tokens as an upper bound, and GPU-hours × peak FLOPs as a cross-check).
- The base model's original training compute, with its source (model card or paper).
- The ratio of run compute to original compute, and a pass flag against the one-third threshold.
- Compute summed across every fine-tune in the same model lineage, because modifications add up.
- All values logged in MLflow as metrics and tags on every training run.
- The pipeline stops and names the compliance owner when the summed ratio is above the threshold.
- An export of compute and ratio per model version, kept as evidence.

## Out of scope

- Model cards and the documentation pack (123 C-4).
- The control mapping report (124 C-9).
- The legal decision on the provider role; the platform only records the evidence.

## Acceptance criteria

- [ ] Every training run logs GPU type, GPU-hours, estimated FLOPs, and the method, in MLflow.
- [ ] Every run logs the base model's original compute with its source, and the ratio.
- [ ] A test run with a fake original compute that puts the ratio above one third fails the pipeline and names the compliance owner.
- [ ] Compute is summed across fine-tunes of the same lineage.
- [ ] One export lists compute and ratio for every registered model version.

## Dependencies

- Depends on: [033 S-5](033-S-5-training-pipeline.md)
- Blocks: none

## References

- Epic story: [C-8 in Phase 9](../slm-agent-platform-epic-v3.md#phase-9-governance-and-compliance)
- Epic context: [E.3](../slm-agent-platform-epic-v3.md#e3) · [R11](../slm-agent-platform-epic-v3.md#r11)
- Backlog plan: [000-plan.md](000-plan.md)
