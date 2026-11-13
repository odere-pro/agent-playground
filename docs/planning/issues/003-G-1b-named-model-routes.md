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
- [x] Changing the model behind `big-default` in router config switches the model; a test call shows the new model answers, and no client code changed. · Delivered by: `pocs/poc-08-cross-cutting-build/tests/test_poc08_router_live.py::test_changing_the_model_behind_big_default_switches_the_model`, `pocs/poc-08-cross-cutting-build/notes/2026-11-13-router-live.md`
- [x] Every route declares native tool-call support, yes or no. · Delivered by: the G-1b routes `big-default` and `local-small` only (PoC-5's `fake-chat` test route is out of scope); `model_info.supports_function_calling` on every `big-default` and `local-small` route in `deploy/compose/litellm/config.yaml`, `deploy/compose/litellm/config.local.yaml`, `deploy/compose/poc06/litellm.config.yaml`, `deploy/compose/poc08-litellm/litellm.yaml`, and `deploy/kind/poc06/hosted/litellm-config.yaml` (`big-default` yes, `local-small` no, suggested: llama.cpp runs without `--jinja`); `packages/chassis/tests/test_litellm_named_routes.py::test_every_route_declares_native_tool_calls_yes_or_no`, `::test_the_variants_declare_each_route_the_same_way`, `::test_every_other_config_declares_its_named_routes_as_the_variants_do`, `::test_the_scan_finds_every_config_that_serves_a_named_route`
- [x] Every route has a version, and the router config history shows each change. · Delivered by: `model_info.route_version` (suggested: a positive integer, bumped on every change to the route's target) and `owner` (suggested: `platform`) on every `big-default` and `local-small` route in `deploy/compose/litellm/config.yaml`, `deploy/compose/litellm/config.local.yaml`, `deploy/compose/poc06/litellm.config.yaml`, `deploy/compose/poc08-litellm/litellm.yaml`, and `deploy/kind/poc06/hosted/litellm-config.yaml`; `packages/chassis/tests/test_litellm_named_routes.py::test_every_route_has_a_version_and_an_owner`, `::test_every_other_config_declares_its_named_routes_as_the_variants_do`; the history is pinned in `HISTORY` (version to target per variant), so a target change without a version bump fails `::test_every_route_version_pins_its_targets_in_the_history` and `::test_the_history_has_every_version_up_to_the_current_one`; the swap rule is in `deploy/compose/README.md`, "Model routes (G-1b)"
- [x] A call to an unknown route name returns a clear error, not a default model. · Delivered by: `pocs/poc-08-cross-cutting-build/tests/test_poc08_router_live.py::test_an_unknown_route_returns_a_clear_error_not_a_default_model`, `pocs/poc-08-cross-cutting-build/notes/2026-11-13-router-live.md`
- [x] The route table is published and linked from the router guide. · Delivered by: `deploy/compose/README.md`, "Model routes (G-1b)" (route, model per variant, owner, native tool calls, version), linked from `docs/guides/point-a-client-at-the-router.md`, "Routes"; `packages/chassis/tests/test_litellm_named_routes.py::test_the_route_table_matches_the_config`, `::test_the_router_guide_links_the_route_table`

## Dependencies

- Depends on: [002 G-1](002-G-1-litellm-router.md)
- Blocks: [012 H-3](012-H-3-model-port.md), [029 S-2](029-S-2-training-data-generation.md), [036 S-7](036-S-7-serve-with-vllm.md), [091 R-11](091-R-11-hybrid-search.md), [136 CH-16](136-CH-16-model-proxy-failures.md)

## References

- Epic story: [G-1b in Phase 1](../slm-agent-platform-epic-v3.md#phase-1-baseline-and-llm-router)
- Epic context: [C.1](../slm-agent-platform-epic-v3.md#c1) · [I](../slm-agent-platform-epic-v3.md#app-i)
- Backlog plan: [000-plan.md](000-plan.md)
