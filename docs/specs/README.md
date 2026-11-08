# Specs: the agent container

The current statement of what the agent container is. One fact, in one place. Each file says what the thing is and where it lives in the code, and links the ADR that says why. History, evidence, and status live elsewhere:

- Why a rule exists: the ADRs in [`docs/planning/adr/`](../planning/adr/001-chassis-delivery-model.md).
- What is proven, and what is still open: the PoC READMEs in [`pocs/`](../../pocs/README.md) and [what is left](../planning/poc/011-what-is-left.md).
- What is built next, in what order: [the plan](../plans/2026-11-06-agent-container-product.md).
- How the code is laid out, module by module: [the chassis reference](../guides/chassis-reference.md).

Where a spec and the code differ, the code wins, and the spec is fixed. A value the epic does not give is marked `suggested:`.

## What the container is

The agent container is one generic image, the chassis, that runs in front of every agent. It owns the public port. It maps native, OpenAI, Anthropic, MCP, A2A, and CloudEvents calls to one request. It runs one pipeline on every request: auth, limits, guards, idempotency, config, budgets, the evaluator gate, AI marking, events, and telemetry. The agent's own code plugs in behind it over A2A, in one of three lanes. Only the chassis holds credentials. A new agent is scaffolded with `agentctl new`, tested offline with `agent-testkit`, and deployed with the `agent-lib` Helm chart. The chassis ships as one image, meant to roll out in rings, with no agent rebuild (rings are not built: 056 CH-7).

## Reading order

| File | What it states |
| ---- | -------------- |
| [architecture.md](architecture.md) | The shape: the core behind ports, the lanes and the trust rule, the two hard requirements, stateless replicas, one image |
| [contract.md](contract.md) | The wire contract in force: envelope, events, `handle` over A2A, the public interfaces, the proxies, status codes, config |
| [models.md](models.md) | Routes, the route map per task, the judge model and its defaults |
| [security.md](security.md) | The controls: fixed ones that are the boundary, model-based ones that lower the odds |
| [observability.md](observability.md) | Spans, baggage, metrics, logs, dashboards, feedback, and the backends behind the Collector |
| [metrics.md](metrics.md) | The north star, the input metrics, and the counter metrics, with formulas |
| [how to add business logic](../guides/how-to-add-business-logic.md) | How a new agent is built from the template and tested |

## Rules for these files

- Plain words, short sentences, one idea per sentence, US spelling.
- State the rule and name the module or schema that holds it, in backticks. Do not restate the ADR's options or reasons; link the ADR.
- No PoC narrative, no test names, no run ids. Those stay in the PoC READMEs.
- A change to a rule is an ADR, or an amendment to one, first. Then the spec.
