---
title: "G-1b: Named model routes, swapped in router config only"
labels: ["story", "priority:P0", "phase:1-router", "area:router", "size:M", "layer:platform"]
milestone: "W1 Router and baseline"
index: 3
epic_id: G-1b
depends_on: ["002 G-1"]
blocks: ["012 H-3", "029 S-2", "036 S-7", "091 R-11", "136 CH-16"]
epic_refs: [C.1, I]
---

## Why

Agents name a route, not a model, so any model is swapped by changing router config only. That is an epic acceptance criterion. The model port (012 H-3), the teacher for training data (029 S-2), and SLM serving (036 S-7) all call models by route name, so the routes must exist before them.

## What

- Named routes in the LiteLLM config, starting with `big-default` and `local-small`. Both answer by name on the fake model (`pocs/poc-01-walking-skeleton/demo/2026-09-29-demo-fake-variant.md`). The name `local-small` stays (`pocs/poc-06c-pretrained-slm/notes/backlog-changes.md`).
- Each route declares whether it supports native tool calls, so the harness can pick the right tool mode later (B.3).
- Each route has a version, so every response can report the model route version it used (B.2).
- Swapping a model means editing the route's target in router config and reloading the router. Clients do not change. This is not the PoC-6c route switch (`pocs/poc-06c-pretrained-slm/tests/test_poc06c_route_switch.py::test_only_the_route_changes_and_every_task_passes_both_times`), which changes the chassis's route, not the model behind it.
- Optional: each service's virtual key lists the routes that service may use, and a call to any other route is refused ([ADR-001](../adr/001-chassis-delivery-model.md) item 6).
- One table that documents every route: name, current model, owner, tool-call support, and version.

## Out of scope

- The harness model port that calls routes by name (012 H-3).
- Retry and fallback between routes in the harness (017 H-4).
- The SLM routes `simplifier-slm` and `teacher-open`, and serving the trained SLM behind them (036 S-7). Parked with the SLM track.
- The Mac run that makes the first hosted call on `big-default`. It is tracked in [011-what-is-left.md](../poc/011-what-is-left.md) section 1, with the routes in `deploy/compose/litellm/`. Its result ticks the first box here.

## Acceptance criteria

- [ ] `big-default` and `local-small` answer through the router by name.
- [ ] Changing the model behind `big-default` in router config switches the model; a test call shows the new model answers, and no client code changed.
- [ ] Every route declares native tool-call support, yes or no.
- [ ] Every route has a version, and the router config history shows each change.
- [ ] A call to an unknown route name returns a clear error, not a default model.
- [ ] The route table is published and linked from the router guide.

## Dependencies

- Depends on: [002 G-1](002-G-1-litellm-router.md)
- Blocks: [012 H-3](012-H-3-model-port.md), [029 S-2](029-S-2-training-data-generation.md), [036 S-7](036-S-7-serve-with-vllm.md), [091 R-11](091-R-11-hybrid-search.md), [136 CH-16](136-CH-16-model-proxy-failures.md)

## References

- Epic story: [G-1b in Phase 1](../slm-agent-platform-epic-v3.md#phase-1-baseline-and-llm-router)
- Epic context: [C.1](../slm-agent-platform-epic-v3.md#c1) · [I](../slm-agent-platform-epic-v3.md#app-i)
- Backlog plan: [000-plan.md](000-plan.md)
