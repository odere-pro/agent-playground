---
title: "X-2: GPU setup on Kubernetes (NVIDIA GPU Operator) and model weight storage with fast cold start"
labels: ["story", "priority:P1", "phase:8-production", "area:infra", "size:M", "layer:platform"]
milestone: "W3 Simplifier SLM"
index: 39
epic_id: X-2
depends_on: ["036 S-7", "038 X-1a"]
blocks: []
epic_refs: [G.4, H]
---

## Why

This is pulled forward from Phase 8 so the simplifier SLM can be served in the cloud, inside the first cloud's cluster. It is P1, not P0, because a rented GPU host can serve the first version. Fast cold start matters because idle GPU pools scale to zero to save cost (J).

## What

- NVIDIA GPU Operator on the GPU node pool from 038 X-1a: drivers, device plugin, and GPU metrics.
- Spot or preemptible GPU nodes where possible, scaled to zero when no GPU pod runs.
- Model weights stored in object storage by MLflow model version.
- Fast cold start: weights cached close to the node, so a new pod does not download them every time (suggested: a node-local cache or a shared volume).
- vLLM deployed with a Helm chart on the GPU nodes, serving the `simplifier-slm` model version, with health and ready probes.
- The cloud router's `simplifier-slm` route pointed at the in-cluster vLLM service.
- Cold start time measured, from zero pods to ready.

## Reuse

- **Use:** NVIDIA GPU Operator, per the epic, and KServe or llm-d for model serving on Kubernetes (pick one).
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Quantized CPU variants (049 M-5).
- KEDA autoscaling of agent pools (104 O-3).
- The second cloud (116 X-1b).

## Acceptance criteria

- [ ] A pod that asks for a GPU is scheduled on a GPU node and can use the GPU.
- [ ] vLLM serves the `simplifier-slm` model version from MLflow in staging, through the router.
- [ ] Cold start time is measured and documented, and a second start on the same node uses cached weights and is faster.
- [ ] GPU nodes scale down to zero when no GPU pod runs.
- [ ] GPU use and memory show in Prometheus.
- [ ] Held-out test scores through the in-cluster model match the MLflow scores.

## Dependencies

- Depends on: [036 S-7](036-S-7-serve-with-vllm.md), [038 X-1a](038-X-1a-terraform-first-cloud.md)
- Blocks: none

## References

- Epic story: [X-2 in Phase 8](../slm-agent-platform-epic-v3.md#phase-8-production-readiness)
- Epic context: [G.4](../slm-agent-platform-epic-v3.md#g4) · [H](../slm-agent-platform-epic-v3.md#app-h)
- Backlog plan: [000-plan.md](000-plan.md)
