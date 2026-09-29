# Adding a port or an adapter

The rule: every external dependency sits behind a port with an in-memory fake, and one contract suite checks the fake and every real adapter. Adapters are picked by `spec.adapters`, never in code.

## A new port

1. **Interface.** `packages/chassis/src/chassis/ports/<name>.py`: a `typing.Protocol` with its data types as Pydantic models. No network code, no product SDK (`make lint` enforces it). Export it from `ports/__init__.py`; add a field to `PortBundle`.
2. **Fake.** `packages/chassis/src/chassis/fakes/<name>.py`: in memory, scripted answers and scripted errors, records calls so tests can assert.
3. **Suite.** `packages/contract-suites/src/chassis_contracts/<name>.py`: a class `<Name>PortContract` (not `Test*`). Fixture methods the binder must provide raise `NotImplementedError`; optional case fixtures `pytest.skip` with a hint. Test methods cover the port's promise: happy path, error path, and the behavior it guarantees (ordering, versions, cancel).
4. **Bind the fake.** `packages/chassis/tests/test_contracts.py`: `class TestX(<Name>PortContract)` with the fixtures.
5. **Register.** `chassis/profiles.py` `REGISTRY`: the fake's factory, and for each real adapter the PoC that adds it.
6. **Document.** `docs/contracts/contract-v0.md` and `packages/chassis/CLAUDE.md`.

## A new real adapter

1. Package `packages/chassis/src/chassis/adapters/<product>/`. Import the product SDK only there.
2. Bind the same suite in its tests. Prefer testcontainers or record-and-replay so it runs without a live service; otherwise mark `network`.
3. Register it in `REGISTRY` and in the profile that uses it. A swap is then a config change.
4. Pin the dependency exactly in the package's `pyproject.toml`; bump it in its own commit.
