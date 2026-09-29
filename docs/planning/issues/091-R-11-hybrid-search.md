---
title: "R-11: Hybrid search: SQL filters, keyword, vector, reciprocal rank fusion, reranker"
labels: ["story", "priority:P1", "phase:5-registry", "area:registry", "size:L", "layer:platform"]
milestone: "W7 Registry and governance gate"
index: 91
epic_id: R-11
depends_on: ["076 R-1", "090 R-13", "003 G-1b", "036 S-7"]
blocks: ["092 R-12", "122 X-5"]
epic_refs: [H, I, R6, R9]
---

## Why

With thousands of entries, the orchestrator must find the right tool from a short query and see only the top matches, which keeps its prompt small (F.6). Hybrid search combines filters, keyword search, and vector search, then reranks, to meet the target: correct main tool in the top 3 for at least 90% of queries. It is built and tuned against the test set from 090 R-13.

## What

- SQL filters first: `status: active`, minimum trust, type, task, tags, owner, and risk class.
- Keyword search with BGE-M3 sparse vectors in pgvector (no extra extension).
- Vector search with dense embeddings in pgvector. Default model Qwen3-Embedding-0.6B; the final pick is tested on the registry's own queries [R9].
- Reciprocal rank fusion of the keyword and vector lists [R6], then Qwen3-Reranker-0.6B on the top results.
- Embedding and reranker models served on vLLM and called through named routes in the LLM router (suggested: `registry-embed`, `registry-rerank`).
- Embedded text (suggested): name, task, description, and schema field names, embedded again when any of them change.
- The embedding model name is stored with every vector. A model change triggers a full re-index, and search never mixes vectors from two models.
- A search endpoint with query, filters, and `top_k`; each stage can be switched off by config.

## Reuse

- **Use:** pgvector (dense vectors plus BGE-M3 sparse vectors), and Qwen3-Embedding and Qwen3-Reranker served by vLLM, all per the epic. ToolHive's `find_tool` search is a reference.
- **Build:** reciprocal rank fusion and the reranking step.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Main pick and fallbacks in the result (092 R-12).
- Search over MCP (093 R-14).
- Load tests (122 X-5).

## Acceptance criteria

- [ ] The correct main entry is in the top 3 for at least 90% of the held-out queries from 090 R-13.
- [ ] Scores for keyword only, vector only, fused, and fused plus reranker are recorded, and the full pipeline scores best.
- [ ] At least three embedding models from Appendix I are compared on the test set, and the pick is recorded with its scores.
- [ ] `pending`, `inactive`, and below-minimum-trust entries never appear in results.
- [ ] Changing the embedding model re-indexes every entry, and every vector row names its model.
- [ ] An entry with a new description is found by its new text within one minute (suggested).
- [ ] p95 search latency on the fixture catalog is measured and recorded.

## Dependencies

- Depends on: [076 R-1](076-R-1-registry-data-model-api.md), [090 R-13](090-R-13-search-test-set.md), [003 G-1b](003-G-1b-named-model-routes.md), [036 S-7](036-S-7-serve-with-vllm.md)
- Blocks: [092 R-12](092-R-12-main-pick-fallbacks.md), [122 X-5](122-X-5-load-tests.md)

## References

- Epic story: [R-11 in Phase 5](../slm-agent-platform-epic-v3.md#phase-5-service-registry)
- Epic context: [H](../slm-agent-platform-epic-v3.md#app-h) · [I](../slm-agent-platform-epic-v3.md#app-i) · [R6](../slm-agent-platform-epic-v3.md#r6) · [R9](../slm-agent-platform-epic-v3.md#r9)
- Backlog plan: [000-plan.md](000-plan.md)
