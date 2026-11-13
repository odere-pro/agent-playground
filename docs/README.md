# docs

Start with two files: [the specs](specs/README.md), what the agent container is, and [the plan](plans/2026-11-06-agent-container-product.md), what is built next and in what order. Everything else is the why (ADRs), the evidence (PoC READMEs), and the how (guides).

| Folder | What |
| ------ | ---- |
| [specs/](specs/README.md) | The current statement of the product, one fact in one place: architecture, the wire contract in force, models, security, observability, key metrics |
| [plans/](plans/README.md) | Approved Claude plans and reviews, dated, under version control. The product plan is [2026-11-06-agent-container-product.md](plans/2026-11-06-agent-container-product.md) |
| [planning/](planning/CLAUDE.md) | The epic (read-only), the backlog (`issues/`), the PoC track (`poc/`, with [what is left](planning/poc/011-what-is-left.md)), ADRs (`adr/`), and the tools that keep them consistent |
| [contracts/](contracts/README.md) | History: the contract as each PoC wrote it, v0 to v7, each additive over the one before. The version in force is [specs/contract.md](specs/contract.md) |
| [guides/](guides/local-dev.md) | Local development, testing, adding a port; [how PoC-4 works](guides/poc-04-how-it-works.md) (state, idempotency, reload, readiness, drain, events, with diagrams); [how PoC-5 works](guides/poc-05-how-it-works.md) (the remote lane, the trust rule, NetworkPolicy, credentials, with diagrams); [point a client at the router](guides/point-a-client-at-the-router.md); [show that an answer is AI-generated](guides/client-app-ai-disclosure.md) (for client app teams); [how to add business logic](guides/how-to-add-business-logic.md) (PoC-9: `handle`, events, mappings, lanes, tools, config, tests, evals) |
| `templates/` | PoC README, PoC CLAUDE.md, ADR |
