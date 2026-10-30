# agent-testkit

The test fixture kit for business logic authors (PoC-9). A new agent is testable offline from its first commit: the kit starts the agent's workload and the chassis in the `fake` profile as two separate processes and runs the lane contract over A2A. The workload's environment never installs the chassis (ADR-001, ADR-002).

## Use

In the agent's `pyproject.toml`:

```toml
[tool.agent-testkit]
handle = "my_agent:handle"   # module:attribute
name = "my-agent"            # optional; default: the module name
# chassis = "..."            # optional; see "Where the chassis comes from"
# platform_path = "../agent-platform"
```

In the top `conftest.py`: `pytest_plugins = ["agent_testkit.plugin"]`. Then:

```python
from agent_testkit.contract import WorkloadContract

class TestMyAgent(WorkloadContract):
    pass

def test_my_logic(chassis):
    assert chassis.run({"text": "hello"})["status"] == "ok"
```

## Fixtures (`agent_testkit.plugin`, session scope)

- `agent_handle_ref`: an `AgentRef(handle, name)` from `[tool.agent-testkit]`. Override it to test another handle.
- `agent_testkit_chassis_source`: where the chassis comes from.
- `workload`: `python -m workload_a2a serve --handle <handle> --name <name> --uds <sock>`, with `chassis` and `chassis_contracts` made unimportable on its `PYTHONPATH`. `.url`, `.uds`, `.client()`, `.agent_card()`.
- `chassis`: the chassis, `profile: fake`, `spec.engine.connector: sidecar`, `spec.engine.uds` = the workload's socket. `.url`, `.uds`, `.client()`, `.post(path, body)`, `.run(input, **kw)` (POST `/v1/run`, returns the envelope).

## `WorkloadContract`

Its own `workload` and `chassis` are class-scoped, so a binder can override `agent_handle_ref` with `@pytest.fixture(scope="class")` and get its own pair of processes. Cases: the agent card; one run of `contract_input` over A2A (start first, one terminal last, every event valid against the schema); an unsupported schema version; an input that is not a JSON object; `/ready` and `/v1/run` through the chassis; and the lane cases vendored from `chassis_contracts.lane` (`lane_cases.py`) over A2A and through the chassis, including JSON values, the run's `traceparent`, and the run deadline.

## Where the chassis comes from

First match: `$AGENT_TESTKIT_CHASSIS`, `[tool.agent-testkit] chassis`, `platform_path`, then the platform checkout the kit sits in.

| Value | Command prefix |
| ----- | -------------- |
| a platform checkout (has `uv.lock` and `packages/chassis`) | `uv run --project <dir> --package chassis --isolated --no-dev --frozen --offline python` |
| `@current-python` (monorepo shortcut only) | `sys.executable` |
| anything else (`packages/chassis`, `chassis==0.1.0`, a git URL) | `uv tool run --offline --from <value> python` |

Then `-I <kit>/data/chassis_serve.py --uds <sock> serve --config <file> --port <p> --proxy-port <q> --drain-delay-s 0 --drain-timeout-s 1 --startup-wait-s 30`. `chassis_serve.py` is `chassis serve` plus `--uds` for the public listener; it runs only in the chassis's env.

## Offline

- No network, no key: the `fake` profile, secrets stripped from both processes' env.
- The test process talks to both processes over Unix sockets only (the gate refuses TCP). The model proxy is loopback TCP, used only by the workload process.
- The platform-checkout form installs from the lock with `--offline --frozen`: it works once the wheels are in uv's cache (`uv sync` puts them there).
- The `uv tool run` form resolves, so warm the cache once with network: `uv tool run --from <value> chassis --help`.

## Test

`make test` runs `packages/agent-testkit/tests`. The bindings are `slow` (they start processes; about 40 s).
