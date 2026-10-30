"""The pytest plugin: `pytest_plugins = ["agent_testkit.plugin"]` in the agent's top conftest.

Session fixtures:

- `agent_handle_ref`: an `AgentRef` from `[tool.agent-testkit] handle` and `name` in the
  `pyproject.toml` at pytest's rootdir. Override it (session scope) to test another handle; a
  bare `"module:attribute"` string works too.
- `agent_testkit_chassis_source`: where the chassis comes from (`agent_testkit.settings`).
- `workload`: the workload's A2A server, `workload-a2a serve --handle <ref> --name <name>`, in its
  own process on a Unix socket, with the chassis made unimportable in it. Has `.url`, `.uds`,
  `.client()`, `.agent_card()`.
- `chassis`: the chassis in the `fake` profile, `spec.engine.connector: sidecar`, pointed at
  `workload`, in its own process from its own environment. Has `.url`, `.uds`, `.client()`, and
  `.run(input, **kw)`, which POSTs `/v1/run` and returns the envelope.

Nothing here imports `chassis`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from agent_testkit.processes import Chassis, Workload, start_chassis, start_workload
from agent_testkit.settings import AgentRef, ChassisSource, agent_ref, chassis_source


@pytest.fixture(scope="session")
def agent_handle_ref(pytestconfig: pytest.Config) -> AgentRef:
    return agent_ref(pytestconfig.rootpath)


@pytest.fixture(scope="session")
def agent_testkit_chassis_source(pytestconfig: pytest.Config) -> ChassisSource:
    return chassis_source(pytestconfig.rootpath)


@pytest.fixture(scope="session")
def workload(agent_handle_ref: AgentRef | str) -> Iterator[Workload]:
    with start_workload(agent_handle_ref) as started:
        yield started


@pytest.fixture(scope="session")
def chassis(workload: Workload, agent_testkit_chassis_source: ChassisSource) -> Iterator[Chassis]:
    with start_chassis(workload, agent_testkit_chassis_source) as started:
        yield started
