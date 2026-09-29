# SLM agent platform: PoC monorepo

The service chassis for the SLM agent platform, built in nine PoC iterations. One chassis runs in front of any workload, in any language, and owns every standard requirement: auth, limits, guardrails, idempotency, budgets, the evaluator gate, events, and telemetry. The design is in [docs/planning](docs/planning/README.md); the decision on how the chassis runs is [ADR-001](docs/planning/adr/001-chassis-delivery-model.md).

## Quick start

```bash
make setup     # Python 3.12, the uv workspace, the git pre-commit hook
make test      # every test, offline: no network, no keys
make help      # every target
```

`make quick` is the gate before a commit. `make check` is what CI runs.

## Map

| Folder | What it answers |
| ------ | --------------- |
| [packages/](packages/chassis/README.md) | What is the shared code? `chassis`, `contract-suites`, `fake-model-server`, and `workloads/` |
| [pocs/](pocs/README.md) | Which iteration is running, what its exit criteria are, and how it is tested |
| [deploy/](deploy/README.md) | How it runs: Docker Compose, kind, Helm |
| [docs/](docs/README.md) | Planning, contracts, guides, templates, and versioned plans |
| `scripts/` | The offline test wrapper, the harness lint, the git hook |
| `.claude/` | Agents, skills, hooks, and the output style for Claude Code |

Current iteration: see `pocs/CURRENT`.

## Rules in one breath

Every dependency sits behind a port with a fake, and one contract suite checks both. Tests run offline. The chassis imports no agent framework. Only the chassis holds credentials. The epic is read-only; the backlog changes through its tools.
