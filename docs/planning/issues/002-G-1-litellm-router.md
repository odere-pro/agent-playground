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
- Both call formats: OpenAI (`/v1/chat/completions`) and Anthropic (`/v1/messages` passthrough) [R3], streaming and complete.
- Provider API keys as secret references (Vault through External Secrets in the cloud), never written in the router config.
- Each service calls the router only with its own scoped virtual key ([ADR-001](../adr/001-chassis-delivery-model.md) item 6). The master key never goes to a service. A call with no key is refused.
- The router config as a versioned file, reviewed like code.
- A container that runs in Docker Compose, and on a host the current services can reach (suggested: the existing infrastructure, until 038 X-1a is up).
- The existing services that call big models listed and pointed at the router, so the baseline counts real traffic.
- Status after PoC-1: LiteLLM runs in Compose, pinned by digest, with two routes. It starts with `docker compose up --wait` and a health check. The `fake` variant runs with no master key at all, because an empty `master_key` in LiteLLM 1.103.0 turns auth on and 401s every call. Only the OpenAI format is served today; `/v1/messages`, Claude, and Gemini are untested. See `pocs/poc-01-walking-skeleton/notes/2026-09-29-compose-key-debt.md`.

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
- Debt from PoC-1: in the `local` Compose variant `LITELLM_API_KEY` equals `LITELLM_MASTER_KEY`, which fails the criterion "no service config holds the master key". Deadline: PoC-5 (one scoped key per service). It blocks PoC-7 budgets. See `pocs/poc-01-walking-skeleton/notes/2026-09-29-compose-key-debt.md`.

## Acceptance criteria

- [x] The router starts with `docker compose up` and passes its health check. Delivered in PoC-1: `pocs/poc-01-walking-skeleton/tests/test_compose.py::test_chassis_waits_for_a_healthy_router`; live run in `pocs/poc-01-walking-skeleton/demo/2026-09-29-demo-fake-variant.md`.
- [ ] A call in OpenAI format to `/v1/chat/completions` and a call in Anthropic format to `/v1/messages` both reach a big model, streaming and complete.
- [ ] Claude, Gemini, and OpenAI each answer a test call through the router.
- [ ] No provider API key appears in the router config, the repo, or the logs.
- [ ] A call with no key is refused, and no service config holds the master key.
- [ ] Every existing service that calls a big model is listed, and each one sends its calls through the router with its own key.
- [ ] A short guide shows how to point a client at the router.

## Dependencies

- Depends on: none
- Blocks: [003 G-1b](003-G-1b-named-model-routes.md), [004 G-2](004-G-2-token-cost-counting.md), [026 CH-4](026-CH-4-chassis-only-credentials-egress.md)

## References

- Epic story: [G-1 in Phase 1](../slm-agent-platform-epic-v3.md#phase-1-baseline-and-llm-router)
- Epic context: [Fig. 1](../slm-agent-platform-epic-v3.md#fig1) · [H](../slm-agent-platform-epic-v3.md#app-h) · [R3](../slm-agent-platform-epic-v3.md#r3)
- Backlog plan: [000-plan.md](000-plan.md)
