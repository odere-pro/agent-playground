# PoC-6a: Framework bake-off, part A: the `sidecar` lane

Status: not started
Planning doc: [006-PoC-6-framework-bake-off.md](../../docs/planning/poc/006-PoC-6-framework-bake-off.md)
Time box: 1 week, after PoC-3

## Question

Which trusted frameworks should the platform support, and which one is the default for new agents? Does a full agent in another language pass the same contract?

## Scope

- [ ] The trusted frameworks from the shortlist as workloads, each with its own event mapping: OpenAI Agents SDK, and Google ADK or another framework only if the team has a reason.
- [ ] The TypeScript echo from PoC-2 grown into a full agent that serves A2A itself, emits the chassis event schema, and runs both benchmark tasks.
- [ ] A draft of the bake-off ADR for the trusted engines.

**Both parts:**

- [ ] Two benchmark tasks for every engine: text in, text out (the simplifier), and a tool task (a lookup with two read-only tools).
- [ ] Run every task on a big model and on an SLM (vLLM or llama.cpp), both through the chassis model proxy and the router.
- [ ] Score each engine against the bake-off criteria in [000-plan.md](../../docs/planning/poc/000-plan.md#bake-off-criteria): event mapping effort, streaming fidelity over A2A, tool support, model agnostic, token overhead, latency, footprint, statelessness, lane under the trust rule, observability hooks, durability, license and maturity.
- [ ] Check router compatibility per engine: does it need provider-only features (for example the Responses API or prompt caching) that the chassis model proxy or LiteLLM does not pass through?
- [ ] Rerun the PoC-3 contract suite, the PoC-4 load test, and the PoC-5 hostile suite on each new engine, in its lane.
- [ ] List what the chassis cannot see or control for the remote solution.
- [ ] Freeze the `handle` contract and the chassis event schema as v1.

## Exit criteria

Each one has a scenario test in `tests/` or a recorded reason it cannot have one yet.

- [ ] Every new Python workload passes the `EnginePort` contract suite over A2A on localhost and in memory, and runs offline against the fake model server.
- [ ] The non-Python agent and the remote solution pass the same contract suite, with no change to the chassis core.
- [ ] Every shortlisted engine is scored on every criterion, with numbers where the criterion is measurable.
- [ ] Every supported engine passes the contract, load, and hostile suites, in its lane.
- [ ] The token overhead against plain Python is known per engine, on a big model and on an SLM.
- [ ] What the chassis cannot control is listed for the remote solution.
- [ ] An ADR names the default engine, the supported engines with their lane, and the rejected engines with reasons. Part A leaves it as a draft; part B completes it.
- [ ] The `handle` contract and the chassis event schema are frozen as v1, or the changes they needed are listed.

## How to run

```bash
make test-poc POC=06a
```

## Demo

Not recorded yet. The script and its output go to `demo/`.

## Notes and decisions

Dated files in `notes/`. `notes/backlog-changes.md` lists what the backlog should change when this iteration closes.
