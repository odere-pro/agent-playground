---
title: "S-7: Serve the simplifier SLM with vLLM, with guided decoding for JSON"
labels: ["story", "priority:P0", "phase:2-simplifier-slm", "area:slm", "size:S", "layer:platform"]
milestone: "W3 Simplifier SLM"
index: 36
epic_id: S-7
depends_on: ["003 G-1b", "035 S-6"]
blocks: ["039 X-2", "041 A-1", "091 R-11"]
epic_refs: [I, R2]
---

## Why

The trained SLM must answer through the router like any other model, so agents call it by the `simplifier-slm` route and never by its address. vLLM serves the OpenAI and Anthropic APIs and supports guided decoding, which forces JSON output to match a schema [R2]. The simplifier core (041 A-1) needs this route live.

## What

- vLLM serving the chosen model version from the MLflow registry (merged weights or a LoRA adapter).
- OpenAI-compatible and Anthropic-compatible APIs from vLLM online serving.
- Guided decoding for JSON output: a request with a JSON Schema always gets output that matches it.
- The `simplifier-slm` route in the router pointed at the vLLM server, replacing the stand-in model. The route version is the model version.
- Temperature 0 by default.
- Runs in the Docker Compose GPU profile, and on a rented GPU host for the first version.
- vLLM metrics scraped by Prometheus, and SLM calls counted in router usage.
- A latency and throughput test with typical inputs.
- Status after PoC-6: PoC-6c serves Qwen3-1.7B with llama.cpp on the host behind LiteLLM's `local-small` route, not vLLM. Configured and tested offline only; the SLM numbers wait on the Mac run. vLLM on a GPU stays here.

## Reuse

- **Use:** vLLM 0.30 with multi-LoRA (many fine-tuned adapters on one base model) and guided decoding. LoRA resolver plugins load adapters from local disk or S3.
- **Watch:** vLLM's docs call runtime LoRA loading a risk outside fully trusted setups. Load adapters only from our own storage.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- GPU setup on Kubernetes and fast cold start (039 X-2).
- The simplifier agent core (041 A-1).
- Shadow mode, canary, and rollback (047 M-3, 048 M-4).
- Quantized variants (049 M-5).

## Acceptance criteria

- [ ] The chosen model answers through the router on `simplifier-slm`, with no change on the client side.
- [ ] Requests with a JSON Schema return output that parses and matches the schema (suggested test: 100 requests, 100 valid).
- [ ] Held-out test scores through the served model match the MLflow scores within the set tolerance.
- [ ] p95 latency for a typical request is measured and within the epic's range of milliseconds to a few hundred milliseconds, or the gap is reported.
- [ ] vLLM metrics show in Prometheus, and SLM calls show in router usage.
- [ ] The served model version is pinned and shows as the route version.

## Dependencies

- Depends on: [003 G-1b](003-G-1b-named-model-routes.md), [035 S-6](035-S-6-train-candidates.md)
- Blocks: [039 X-2](039-X-2-gpu-setup.md), [041 A-1](041-A-1-simplifier-core.md), [091 R-11](091-R-11-hybrid-search.md)

## References

- Epic story: [S-7 in Phase 2](../slm-agent-platform-epic-v3.md#phase-2-simplifier-slm-and-model-lifecycle)
- Epic context: [I](../slm-agent-platform-epic-v3.md#app-i) · [R2](../slm-agent-platform-epic-v3.md#r2)
- Backlog plan: [000-plan.md](000-plan.md)
