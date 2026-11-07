---
title: "G-1: LiteLLM router in front of all model calls"
labels: ["story", "priority:P0", "phase:1-router", "area:router", "size:M", "layer:platform"]
milestone: "W1 Router and baseline"
index: 2
epic_id: G-1
depends_on: []
blocks: ["003 G-1b", "004 G-2", "026 CH-4"]
epic_refs: [Fig.1, H, R3]
---

## Why

Every model call must go through one LLM router, so models can be swapped by config and tokens can be counted in one place. This issue is first in the backlog because the baseline (005 G-3) needs at least two weeks of data, so the router must count tokens as early as possible. It has no dependencies and can start on day one, next to 001 DEC-1.

## What

- A LiteLLM proxy (with routing) as the one endpoint for all model calls, big models and SLMs.
- Backends for the big models in use today: Claude, Gemini (through its OpenAI-compatible endpoint), and OpenAI.
- Both call formats [R3], streaming and complete. OpenAI (`/v1/chat/completions`) reaches the router directly. In the product, the chassis serves Anthropic's `/v1/messages` and maps it onto `ModelPort`, which posts only to `/chat/completions` ([ADR-003](../adr/003-chat-formats-onto-the-canonical-request.md)). So an Anthropic-format call reaches the router as OpenAI format.
- Provider API keys as secret references (Vault through External Secrets in the cloud), never written in the router config.
- Each service calls the router only with its own scoped virtual key, minted at deploy ([ADR-001](../adr/001-chassis-delivery-model.md) item 6). The master key never goes to a service. A call with no key is refused.
- The router config as a versioned file, reviewed like code.
- A container that runs in Docker Compose, pinned by digest, with a health check.
- Offline tests keep provider keys out of the config and the repo. No test reads the router's logs yet. The proving test is a `network` test on the PoC-8 stack that makes a call and greps the LiteLLM container log for the key (suggested: `test_no_provider_key_in_the_router_logs`).
- The `local` Compose variant still gives the chassis the master key, because it has no database to mint a virtual key. The criterion on the master key stays open until proposal P-3 is decided ([proposals](../../reviews/2026-11-06-backlog-review/proposals.md#p-3-the-master-key-in-the-local-compose-variant)).

## Reuse

- **Use:** the LiteLLM proxy, as planned.
- **Watch:** pin LiteLLM by hash. PyPI releases 1.82.7 and 1.82.8 were malicious (2026-03-24). Some governance features are Enterprise-only.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- Named routes such as `simplifier-slm` and `big-default` (003 G-1b).
- Token and cost counting per request, user, and agent (004 G-2).
- The routes each key may use (003 G-1b).
- The chassis model port that calls the router (012 H-3).
- Serving an SLM behind the router (036 S-7).
- The `fake` Compose variant runs with no master key at all. That fact belongs in `deploy/compose/SECURITY.md`, not in a box.

## Acceptance criteria

- [x] The router starts with `docker compose up` and passes its health check. · Delivered in PoC-1: `pocs/poc-01-walking-skeleton/tests/test_compose.py::test_chassis_waits_for_a_healthy_router`; `pocs/poc-01-walking-skeleton/demo/2026-09-29-demo-fake-variant.md`
- [ ] An OpenAI-format call through the router reaches a big model, streaming and complete; the Anthropic format reaches the router as OpenAI format through the chassis.
- [ ] Claude, Gemini, and OpenAI each answer a test call through the router.
- [ ] No provider API key appears in the router config, the repo, or the logs.
- [ ] A call with no key is refused, and no service config holds the master key.
- [x] Every service calls the router with its own virtual key, minted at deploy. · Delivered in PoC-5: `pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py::test_litellm_refuses_the_workload_without_the_chassis_key`, run in `pocs/poc-05-sandboxed/demo/2026-10-09-demo-sandboxed.md`; PoC-8: `pocs/poc-08-cross-cutting-build/tests/test_poc08_litellm_static.py::test_the_master_key_reaches_litellm_only_and_the_virtual_key_the_chassis_only`
- [ ] A short guide shows how to point a client at the router.

## Dependencies

- Depends on: none
- Blocks: [003 G-1b](003-G-1b-named-model-routes.md), [004 G-2](004-G-2-token-cost-counting.md), [026 CH-4](026-CH-4-chassis-only-credentials-egress.md)

## References

- Epic story: [G-1 in Phase 1](../slm-agent-platform-epic-v3.md#phase-1-baseline-and-llm-router)
- Epic context: [Fig. 1](../slm-agent-platform-epic-v3.md#fig1) · [H](../slm-agent-platform-epic-v3.md#app-h) · [R3](../slm-agent-platform-epic-v3.md#r3)
- Backlog plan: [000-plan.md](000-plan.md)
