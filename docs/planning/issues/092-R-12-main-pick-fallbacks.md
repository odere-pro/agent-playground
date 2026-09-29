---
title: "R-12: Search results return the main pick plus ranked fallbacks with difference metadata"
labels: ["story", "priority:P1", "phase:5-registry", "area:registry", "size:M", "layer:platform"]
milestone: "W7 Registry and governance gate"
index: 92
epic_id: R-12
depends_on: ["091 R-11"]
blocks: ["093 R-14", "099 O-1"]
epic_refs: [F.3]
---

## Why

The orchestrator needs more than a ranked list. It needs one main pick and a clear view of its fallbacks, so it can switch when the main pick fails or costs too much. F.3 defines `fallback_of` links with difference metadata; this issue puts them in search results. The orchestrator (099 O-1) and the registry MCP server (093 R-14) use this result shape.

## What

- Each search result has a `main` entry and a ranked `fallbacks` list, as in the F.3 example.
- Fallbacks come first from the main entry's `fallback_of` links, then from other matches for the same task (suggested).
- Each fallback carries `differences`: cost, speed, quality, limits, and notes. Numbers come from live metrics (086 R-10), relative to the main pick; notes come from the link.
- If the main pick is not usable (inactive or below minimum trust), the first usable fallback becomes the main pick.
- The number of fallbacks follows `top_k` from the caller (for example `registry.top_k: 5` in C.2).
- Results stay short: only the fields a caller needs to pick and call an entry. Full entries come from a get call.
- Suggested: each result has a score and the fields that matched.

## Out of scope

- Ranking itself (091 R-11).
- How the orchestrator uses fallbacks during a run (099 O-1).

## Acceptance criteria

- [ ] A search for `simplify_text` returns `simplifier` as the main pick and `simplifier-big-model` as a fallback with its differences note, as in the F.3 example.
- [ ] Fallbacks are in rank order, and their count follows `top_k`.
- [ ] Cost and speed differences match the live metrics of both entries.
- [ ] When the main entry is inactive, the first active fallback is returned as the main pick.
- [ ] A result with `top_k: 5` stays under 1,000 tokens (suggested limit).
- [ ] The result shape is in the OpenAPI spec and covered by a contract test.
- [ ] Top-3 accuracy on the 090 R-13 held-out queries stays at or above 90%, counting the main pick and fallbacks in rank order.

## Dependencies

- Depends on: [091 R-11](091-R-11-hybrid-search.md)
- Blocks: [093 R-14](093-R-14-registry-mcp-server.md), [099 O-1](099-O-1-orchestrator-agent.md)

## References

- Epic story: [R-12 in Phase 5](../slm-agent-platform-epic-v3.md#phase-5-service-registry)
- Epic context: [F.3](../slm-agent-platform-epic-v3.md#f3)
- Backlog plan: [000-plan.md](000-plan.md)
