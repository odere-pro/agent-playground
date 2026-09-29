---
title: "H-11: Chassis reuse in other projects: the stock image in front of any A2A workload"
labels: ["story", "priority:P2", "phase:0-harness", "area:harness", "size:S", "layer:chassis"]
milestone: "W5 Chassis completion"
index: 62
epic_id: H-11
depends_on: ["011 H-2", "012 H-3", "058 CH-8"]
blocks: []
epic_refs: [F.1]
---

## Why

Other projects face the same problem the chassis solves: exposing their code through native, OpenAI, Anthropic, and MCP APIs, with auth, limits, and telemetry, and calling any model through one port. The first plan published the adapters as their own Python package, with its own semantic versions and changelog. [ADR-001](../adr/001-chassis-delivery-model.md) replaces that: item 1 says one codebase, one image, and one version, and item 7 says there is no SDK. So other projects reuse the chassis by running the stock chassis image in front of their own A2A workload, set up by config only. It is P2, because no epic acceptance criterion depends on it.

## What

- The stock chassis image in front of any workload that serves A2A and emits the chassis JSON event schema, in any language. Nothing from the other project is built into the image.
- Config only: the other project writes an agent config (interfaces, `spec.engine.connector`, `spec.adapters`, security) and includes the shared library chart (024 CH-3), or runs the image with Docker Compose.
- A short guide: how to put the stock image in front of an existing A2A service, and what the chassis needs from it.
- The `chassis` package, published only so a service's tests can run `handle` in the `inprocess` lane. It is the same code and the same version as the chassis image, released by the same CI pipeline. It has no semantic versions or changelog of its own.
- Suggested: the package on the company's internal package index, and the image in the company's registry.
- A small example workload outside this repo, not in Python, fronted by the stock image. suggested: the TypeScript workload from 058 CH-8, in its own repo.

## Reuse

- **Scope change:** superseded in its first form by ADR-001: one codebase, one image, one version, and no SDK. Other projects reuse the stock chassis image in front of their own A2A workload.
- Details: [reuse analysis](../poc/010-reuse-analysis.md)

## Out of scope

- A per-language SDK, or a local API of our own. ADR-001 rules both out.
- Changes to the chassis for one project's needs. A missing protocol or connector is added to the chassis itself, in its own issue.
- An OpenAI-compatible connector for workloads that cannot speak A2A. It is added only when such a workload exists.
- The template repo and the agent factory (025 H-10, 059 H-15).
- A public release.

## Acceptance criteria

- [ ] A non-Python example workload outside this repo is fronted by the stock chassis image, with config only, and passes the contract suite.
- [ ] The example answers through the native, OpenAI, and Anthropic APIs, with auth, limits, and traces, and its repo holds no chassis code.
- [ ] The published `chassis` package version equals the chassis image version, and both come from the same CI release.
- [ ] A service's tests import the package and run `handle` in the `inprocess` lane, offline, with no keys.

## Dependencies

- Depends on: [011 H-2](011-H-2-inbound-adapters.md), [012 H-3](012-H-3-model-port.md), [058 CH-8](058-CH-8-framework-event-mappings.md)
- Blocks: none

## References

- Epic story: [H-11 in Phase 0](../slm-agent-platform-epic-v3.md#phase-0-agent-harness)
- Epic context: [F.1](../slm-agent-platform-epic-v3.md#f1)
- Backlog plan: [000-plan.md](000-plan.md)
