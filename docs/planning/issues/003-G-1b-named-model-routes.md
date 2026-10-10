---
title: "G-1b: Named model routes, swapped in router config only"
labels: ["story", "priority:P0", "phase:1-router", "area:router", "size:S", "layer:platform"]
milestone: "W1 Router and baseline"
index: 3
epic_id: G-1b
depends_on: ["002 G-1"]
blocks: ["012 H-3", "029 S-2", "036 S-7", "091 R-11"]
epic_refs: [C.1, I]
---

## Why

Agents name a route, not a model, so any model is swapped by changing router config only. That is an epic acceptance criterion. The model port (012 H-3), the teacher for training data (029 S-2), and SLM serving (036 S-7) all call models by route name, so the routes must exist before them.

## What

- Named routes in the LiteLLM config, starting with `big-default` and `simplifier-slm` from C.1.
- A route for the open-weight teacher and judge: a large open Qwen3 or Gemma 3 model on vLLM (suggested name: `teacher-open`).
- `simplifier-slm` pointed at a stand-in model until the trained SLM is served (036 S-7).
- Each route declares whether it supports native tool calls, so the harness can pick the right tool mode later (B.3).
- Each route has a version, so every response can report the model route version it used (B.2).
- Swapping a model means editing the route's target in router config and reloading the router. Clients do not change.
- Optional: each service's virtual key lists the routes that service may use, and a call to any other route is refused ([ADR-001](../adr/001-chassis-delivery-model.md) item 6).
- One table that documents every route: name, current model, owner, tool-call support, and version.
- Status after PoC-1: `big-default` and `local-small` answer by name. Switching `spec.model.route` is a config change and a restart (`pocs/poc-01-walking-skeleton/demo/2026-09-29-demo-fake-variant.md`). This issue names the second route `simplifier-slm`. Open decision: rename `local-small` to `simplifier-slm`, or keep it as a third route. Not yet: tool-call declaration per route, route versions in config history, and the clear error for an unknown route.
- Status after PoC-6: PoC-6a and 6b ran on one route name, `big-default`, with the fake model server offline. The hosted route and the `local-small` route to the llama.cpp server are configured for the Mac run (`deploy/compose/litellm/`, `pocs/poc-06c-pretrained-slm/tests/test_poc06c_mac_static.py`); nothing has run on them yet. The name `local-small` stays.

## Out of scope

- The harness model port that calls routes by name (012 H-3).
- Retry and fallback between routes in the harness (017 H-4).
- Serving the trained SLM behind `simplifier-slm` (036 S-7).

## Acceptance criteria

- [ ] `big-default` and `simplifier-slm` answer through the router by name.
- [ ] Changing the model behind `big-default` in router config switches the model; a test call shows the new model answers, and no client code changed.
- [ ] Every route declares native tool-call support, yes or no.
- [ ] Every route has a version, and the router config history shows each change.
- [ ] A call to an unknown route name returns a clear error, not a default model.
- [ ] The route table is published and linked from the router guide.

## Dependencies

- Depends on: [002 G-1](002-G-1-litellm-router.md)
- Blocks: [012 H-3](012-H-3-model-port.md), [029 S-2](029-S-2-training-data-generation.md), [036 S-7](036-S-7-serve-with-vllm.md), [091 R-11](091-R-11-hybrid-search.md)

## References

- Epic story: [G-1b in Phase 1](../slm-agent-platform-epic-v3.md#phase-1-baseline-and-llm-router)
- Epic context: [C.1](../slm-agent-platform-epic-v3.md#c1) · [I](../slm-agent-platform-epic-v3.md#app-i)
- Backlog plan: [000-plan.md](000-plan.md)
