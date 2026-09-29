# pocs

One folder per iteration of the PoC track in [docs/planning/poc/000-plan.md](../docs/planning/poc/000-plan.md). The planning doc is the source; the folder tracks execution.

| Folder | Planning doc | Question |
| ------ | ------------ | -------- |
| `poc-01-walking-skeleton` | [001](../docs/planning/poc/001-PoC-1-walking-skeleton.md) | Is the chassis testable offline from day 0? Does one request go end to end? |
| `poc-02-two-engines-one-contract` | [002](../docs/planning/poc/002-PoC-2-two-engines-one-contract.md) | Can two frameworks and a TypeScript echo run behind one `handle` contract over A2A? |
| `poc-03-one-interface-every-client` | [003](../docs/planning/poc/003-PoC-3-one-interface-every-client.md) | Can any client call any engine the same way? |
| `poc-04-stateless-scalable` | [004](../docs/planning/poc/004-PoC-4-stateless-scalable.md) | Does it scale by adding replicas, with no state? Dapr or a broker client? |
| `poc-05-sandboxed` | [005](../docs/planning/poc/005-PoC-5-sandboxed.md) | Can untrusted code run in the `remote` lane without reaching anything? |
| `poc-06a-bake-off-sidecar-lane` | [006](../docs/planning/poc/006-PoC-6-framework-bake-off.md) | Which trusted frameworks to support, and which is the default? |
| `poc-06b-bake-off-remote-lane` | [006](../docs/planning/poc/006-PoC-6-framework-bake-off.md) | Which untrusted frameworks and which remote solution does the chassis front? |
| `poc-07-cross-cutting-decisions` | [007](../docs/planning/poc/007-PoC-7-cross-cutting-decisions.md) | Does the chosen stack give security, observability, feedback, and evals to every engine? |
| `poc-08-cross-cutting-build` | [008](../docs/planning/poc/008-PoC-8-build-cross-cutting.md) | Do those designs work end to end on every engine? |
| `poc-09-agent-mvp-template` | [009](../docs/planning/poc/009-PoC-9-agent-mvp-template.md) | Can a new agent be scaffolded and running in under a day? |

## How an iteration runs

1. **Open.** Set `CURRENT`. Fill `README.md` from `docs/templates/poc-readme.md` and `CLAUDE.md` from `docs/templates/poc-claude.md`. Write one scenario test per exit criterion in `tests/`.
2. **Run.** Small steps, `make quick` after each, `make test-poc POC=NN` before ticking a box. Measurements go to `notes/` with the command that produced them. Decisions that outlive the iteration become ADRs.
3. **Close.** Every exit criterion has evidence linked from the README. The demo is in `demo/`. `notes/backlog-changes.md` lists what the backlog should change. Status `done`, `CURRENT` moves on, `make check` green.

Each folder: `README.md` (question, scope and exit criteria as checklists, status), `CLAUDE.md` (what to read, what is touched, what done means), `tests/`, `demo/`, `notes/`.
