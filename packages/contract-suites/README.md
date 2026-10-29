# chassis-contracts

One pytest contract suite per chassis port, plus suites for the inbound adapters, the public interfaces, and the lanes. A fake and every real adapter pass the same suite, so a fake that passes behaves like the real thing. `uv run pytest packages/contract-suites` runs the suites' own self-tests. The fakes are bound in the chassis tests, so `uv run pytest packages/chassis` runs the suites against them.

Bind a suite by subclassing it in a `Test*` class and providing its fixtures. How, and the rules: [CLAUDE.md](CLAUDE.md) and [docs/guides/adding-a-port.md](../../docs/guides/adding-a-port.md). The suites are the modules in [src/chassis_contracts/](src/chassis_contracts/).

Real adapters bind the same suites through the testcontainers helpers in [src/chassis_contracts/containers/](src/chassis_contracts/containers/). `make test-integration` runs them; it needs Docker.
