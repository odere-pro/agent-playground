---
title: "PoC-6: Framework bake-off, in two parts: which agent SDKs and frameworks to support"
labels: ["poc", "priority:P0", "area:harness", "type:decision"]
milestone: "Agent MVP"
index: 6
iteration: PoC-6
timebox: "2 weeks: part A 1 week after PoC-3, part B 1 week after PoC-5"
depends_on: ["PoC-3", "PoC-5"]
backlog_refs: ["008 H-14", "054 H-16", "063 S-8", "058 CH-8", "055 CH-6"]
---

## Question

Which agent SDKs and frameworks should the platform support, and which one is the default for new agents? Does the chassis front an agent in another language and a remote solution with no change to its core?

## Why

This is where the different agent approaches are tested side by side. The chassis, interfaces, scaling, and both lanes already exist, so every engine is judged on the same terms and with real numbers, not on reputation. The result decides what the template in PoC-9 offers. This iteration also adds the rest of the MVP's three engine kinds: a full agent in another language and a remote solution.

It runs in two parts, so the trusted frameworks do not wait on the cluster work in PoC-5. Part A needs only PoC-3 and runs in the `sidecar` lane. Part B needs PoC-5 and runs in the `remote` lane.

## Scope

**Part A, the `sidecar` lane (after PoC-3):**

- [ ] The trusted frameworks from the shortlist as workloads, each with its own event mapping: OpenAI Agents SDK, and Google ADK or another framework only if the team has a reason.
- [ ] The TypeScript echo from PoC-2 grown into a full agent that serves A2A itself, emits the chassis event schema, and runs both benchmark tasks.
- [ ] A draft of the bake-off ADR for the trusted engines.

**Part B, the `remote` lane (after PoC-5):**

- [ ] The untrusted frameworks as `remote` workloads: Claude Agent SDK with its shell and file tools on, and smolagents. The Claude Agent SDK writes to the home directory, so it needs a scratch volume under the read-only root file system; note what it needs.
- [ ] One remote solution through the `remote` lane, with its cloud auth adapter: AWS Bedrock AgentCore or Vertex AI Agent Engine, per the first cloud in [001 DEC-1](../issues/001-DEC-1-resolve-open-decisions.md). suggested: kagent on the kind cluster, if no cloud is chosen yet. It reaches models and tools through the chassis's proxies with its own credential, or, if it cannot be pointed at them, through its own scoped key (ADR-001 item 8).

**Both parts:**

- [ ] Two benchmark tasks for every engine: text in, text out (the simplifier), and a tool task (a lookup with two read-only tools).
- [ ] Run every task on a hosted big model, through the chassis model proxy and the router. The SLM runs are [PoC-6c](006c-PoC-6c-pretrained-slm.md) (user decision, 2026-10-09).
- [ ] Score each engine against the bake-off criteria in [000-plan.md](000-plan.md#bake-off-criteria): event mapping effort, streaming fidelity over A2A, tool support, model agnostic, token overhead, latency, footprint, statelessness, lane under the trust rule, observability hooks, durability, license and maturity.
- [ ] Check router compatibility per engine: does it need provider-only features (for example the Responses API or prompt caching) that the chassis model proxy or LiteLLM does not pass through?
- [ ] Rerun the PoC-3 contract suite, the PoC-4 load test, and the PoC-5 hostile suite on each new engine, in its lane.
- [ ] List what the chassis cannot see or control for the remote solution.
- [ ] Freeze the `handle` contract and the chassis event schema as v1.

## Reuse

- **Use:** OpenInference traces to see each engine's steps and token overhead, the a2a-sdk for JavaScript (or the chosen language) for the non-Python agent, and the contract, load, and hostile suites from PoC-3 to PoC-5.
- **Build:** one event mapping per new framework, the non-Python example, one cloud auth adapter, and the scorecard.
- **Note:** an engine with an OpenInference instrumentor and a Temporal integration saves work later. OpenAI Agents SDK has both; PydanticAI and LangGraph have both too (LangGraph's Temporal plugin is in preview).
- Details: [reuse analysis](010-reuse-analysis.md)

## Out of scope

- Multi-agent patterns and orchestration (backlog W9).
- Fine-tuning an SLM for tool calls (backlog [063 S-8](../issues/063-S-8-tool-call-finetune.md)).
- Security, observability, feedback, and evals beyond what the engines offer natively (PoC-7).
- The second cloud's auth adapter (backlog [116 X-1b](../issues/116-X-1b-terraform-second-cloud.md)).

## Demo

A scorecard table with one row per engine, and side-by-side runs of both tasks on every engine through the same `/v1/run` call. The runs cover the `sidecar` lane, the `remote` lane, the non-Python agent, and the remote solution.

## Exit criteria

- [ ] Every new Python workload passes the `EnginePort` contract suite over A2A on localhost and in memory, and runs offline against the fake model server.
- [ ] The non-Python agent and the remote solution pass the same contract suite, with no change to the chassis core.
- [ ] Every shortlisted engine is scored on every criterion, with numbers where the criterion is measurable.
- [ ] Every supported engine passes the contract, load, and hostile suites, in its lane.
- [ ] The token overhead against plain Python is known per engine, on a hosted big model. PoC-6c measures it on an SLM.
- [ ] What the chassis cannot control is listed for the remote solution.
- [ ] An ADR names the default engine, the supported engines with their lane, and the rejected engines with reasons. Part A leaves it as a draft; part B completes it.
- [ ] The `handle` contract and the chassis event schema are frozen as v1, or the changes they needed are listed.

## Links

- Plan: [000-plan.md](000-plan.md) · Previous: [PoC-3](003-PoC-3-one-interface-every-client.md) for part A, [PoC-5](005-PoC-5-sandboxed.md) for part B · Next: [PoC-7](007-PoC-7-cross-cutting-decisions.md) · The SLM runs: [PoC-6c](006c-PoC-6c-pretrained-slm.md)
- Decision: [ADR-001](../adr/001-chassis-delivery-model.md) (the trust rule and the lanes)
- Backlog issues this informs: [008 H-14](../issues/008-H-14-one-agent-interface.md), [054 H-16](../issues/054-H-16-tool-port.md), [063 S-8](../issues/063-S-8-tool-call-finetune.md), [058 CH-8](../issues/058-CH-8-framework-event-mappings.md), [055 CH-6](../issues/055-CH-6-remote-lane-trust-rule.md)
- Epic: [B.1](../slm-agent-platform-epic-v3.md#b1), [B.3](../slm-agent-platform-epic-v3.md#b3), [I](../slm-agent-platform-epic-v3.md#app-i)
