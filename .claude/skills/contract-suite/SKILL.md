---
name: contract-suite
description: Add a port, a fake, or a real adapter to the chassis so that both pass the same contract suite. Use whenever a new external dependency or adapter appears.
---
# Contract suite: port, fake, adapter

Rule: every external dependency sits behind a port with a fake, and one suite checks both.

## New port
1. Interface in `packages/chassis/src/chassis/ports/<name>.py`: a `typing.Protocol`, no network code, no product SDK. Export it from `ports/__init__.py` and add it to `PortBundle`.
2. Fake in `packages/chassis/src/chassis/fakes/<name>.py`: in memory, scripted answers and scripted errors, records calls for assertions.
3. Suite in `packages/contract-suites/src/chassis_contracts/<name>.py`: a `<Name>PortContract` class (not `Test*`) with fixture methods the binder overrides and test methods over them. Cases: the happy path streaming and complete where it applies, an error path, and the behavior the port promises (versioning, ordering, cancel).
4. Bind the fake in `packages/chassis/tests/test_contracts.py` as `class TestX(<Name>PortContract)`.
5. Register the fake in `chassis/profiles.py` `REGISTRY` and name the PoC that adds each real adapter.
6. Document in `docs/contracts/contract-v0.md` and the package `CLAUDE.md`.

## New real adapter
1. Package `packages/chassis/src/chassis/adapters/<product>/`. The product SDK is imported only here; `make lint` enforces it.
2. Bind the same suite in the adapter's tests. Use testcontainers or record-and-replay so it runs without a live service where possible; otherwise mark `network`.
3. Register it in `REGISTRY` and the profile that uses it. A swap is then a config change.
