# PoC-1 walking skeleton: implementation plan

Status: done
Date: 2026-09-29
Source: `docs/planning/poc/001-PoC-1-walking-skeleton.md`. Tracking: `pocs/poc-01-walking-skeleton/README.md`.

Day 0 landed in the scaffold commit. This plan covers the eight open exit criteria.

## Streams

Three streams run in parallel. Stream C waits on the contract decision in stream A.

**A. The A2A lane (contract).** `chassis-architect` writes the mapping of chassis events to A2A task updates into `docs/contracts/contract-v0.md` and decides where the template A2A server lives. Then `developer` builds the template A2A server (a2a-sdk 1.x wrapping `handle`), the `inprocess` connector (A2A over an httpx ASGI transport, no socket), and the plain-Python simplifier workload in `packages/workloads/echo-python`. Registers `engine: inprocess` in `chassis.profiles.REGISTRY`.
Exit criteria: plug-in answers over A2A in memory and A2A messages validate; contract v0 written down.

**B. The HTTP surface.** `developer` builds `chassis serve` (FastAPI, uvicorn), `POST /v1/run` with `stream: true` (SSE) and `stream: false` (the collector), `GET /health`, `GET /ready`. The app is built from a config (profile plus `spec.adapters`) and holds no business logic. The response reports `versions`.
Exit criteria: `chassis serve` starts from config; streaming and complete carry the same output.

**C. The real model adapter and the router.** `developer` builds `chassis.adapters.litellm`: `ModelPort` over OpenAI-compatible HTTP with httpx. Offline it binds the contract suite against the fake model server over an ASGI transport. `platform-security` reviews the Compose stack: LiteLLM pinned by digest, routes `big-default` and `local-small`, key in the chassis only, tokens tagged with the agent name.
Exit criteria: real `ModelPort` passes the fake's suite; `docker compose up` with no manual steps; adapter or route switch is config only; token counts visible in the router.

## Close

Scenario tests in `pocs/poc-01-walking-skeleton/tests/`, one per criterion. Demo script and output in `demo/`. `notes/backlog-changes.md`. README boxes ticked with evidence. `make check` green.

## Out of scope

Frameworks, `sidecar`, the model proxy (PoC-2). OpenAI, Anthropic, MCP inbound (PoC-3). Any other port.
