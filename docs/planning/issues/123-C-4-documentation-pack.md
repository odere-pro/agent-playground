---
title: "C-4: Documentation pack generator: technical docs, instructions for use, model card per agent version"
labels: ["story", "priority:P1", "phase:9-governance", "area:governance", "size:M", "layer:platform"]
milestone: "W11 Compliance evidence"
index: 123
epic_id: C-4
depends_on: ["032 M-1", "051 H-13", "052 H-21", "078 R-16"]
blocks: ["124 C-9"]
epic_refs: [E.5, R12]
---

## Why

AI Act Art. 11 and Art. 13 and ISO 42001 A.8 ask for technical documentation and instructions for use, and the epic says a documentation pack must exist for every agent version. Docs written by hand drift from the code. This generator builds the pack from sources the platform already has: config, OpenAPI, AsyncAPI, MLflow, and the registry inventory.

## What

- A generator that builds one pack per agent version with three parts: technical documentation (Annex IV style), instructions for use, and a model card.
- Technical documentation: intended purpose and the `governance` block, architecture (with the lane, the `spec.trust` value, the chassis version, and, for `remote` agents, the managed-runtime provider), interfaces from the OpenAPI and AsyncAPI specs, model routes, the evaluator gate and threshold, the oversight setting, and logging and retention.
- Instructions for use from the manifest: intended purpose, limits, AI-generated marking and the disclosure flag, and how to call the agent.
- Model card from MLflow: base model, data version (lakeFS), golden set scores, and training compute (C-8). For an API model, the route and provider (suggested).
- Each section links to its source and version.
- The template CI builds the pack on every release and stores it in object storage with the agent version (suggested: Markdown and PDF).
- A missing required source fails the build.

## Reuse

- **Use:** the HF model card template, CycloneDX 1.7 ML-BOM for model and dataset bills of materials, and MLflow model registry metadata.
- **Build:** the generator that fills them from config, OpenAPI, AsyncAPI, and MLflow.
- **Watch:** Google Model Card Toolkit is archived.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- The control mapping report (124 C-9).
- The audit export (072 C-3).
- Legal classification of use cases.

## Acceptance criteria

- [ ] For the simplifier's current version, the generator builds all three parts.
- [ ] Every section links to the exact config, spec, and MLflow run versions it came from.
- [ ] An agent version with no `governance` block fails the build with a clear error.
- [ ] Running the generator twice on the same inputs gives the same content.
- [ ] A pack can be produced for any past agent version in the registry (Phase 9 done-when).
- [ ] The release CI builds and stores a pack for each new agent version.

## Dependencies

- Depends on: [032 M-1](032-M-1-mlflow.md), [051 H-13](051-H-13-openapi-mcp-tools.md), [052 H-21](052-H-21-asyncapi-spec.md), [078 R-16](078-R-16-ai-system-inventory.md)
- Blocks: [124 C-9](124-C-9-control-mapping-report.md)

## References

- Epic story: [C-4 in Phase 9](../slm-agent-platform-epic-v3.md#phase-9-governance-and-compliance)
- Epic context: [E.5](../slm-agent-platform-epic-v3.md#e5) · [R12](../slm-agent-platform-epic-v3.md#r12)
- Backlog plan: [000-plan.md](000-plan.md)
