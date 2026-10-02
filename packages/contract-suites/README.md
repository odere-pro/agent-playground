# chassis-contracts

Contract suites for the chassis ports. Bind one by subclassing it as a `Test*` class and providing its fixtures. Details in [CLAUDE.md](CLAUDE.md) and [docs/guides/adding-a-port.md](../../docs/guides/adding-a-port.md).

Suites: model, engine (with `probe`), config, telemetry, tool, state, and events, plus the inbound and interface suites. Real adapters bind them through the testcontainers helpers in `chassis_contracts.containers` (Valkey, MinIO, Kafka, Dapr), run by `make test-integration`.
