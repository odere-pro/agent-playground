# docs

| Folder | What |
| ------ | ---- |
| [planning/](planning/CLAUDE.md) | The epic (read-only), the backlog (`issues/`), the PoC track (`poc/`), ADRs (`adr/`), and the tools that keep them consistent |
| [plans/](plans/README.md) | Approved Claude plans and reviews, dated, under version control |
| [contracts/](contracts/contract-v0.md) | The written contract: envelope, events, `handle`, ports. Each version is additive over the one before: [v1](contracts/contract-v1.md) (PoC-2), [v2](contracts/contract-v2.md) (PoC-3, the public interfaces), [v3](contracts/contract-v3.md) (PoC-4, idempotency, readiness, shutdown, config reload, state and event ports) |
| [guides/](guides/local-dev.md) | Local development, testing, adding a port; [how PoC-4 works](guides/poc-04-how-it-works.md) (state, idempotency, reload, readiness, drain, events, with diagrams); [how PoC-5 works](guides/poc-05-how-it-works.md) (the remote lane, the trust rule, NetworkPolicy, credentials, with diagrams) |
| `templates/` | PoC README, PoC CLAUDE.md, ADR |
