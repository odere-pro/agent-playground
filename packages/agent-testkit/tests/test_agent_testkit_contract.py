"""`WorkloadContract` bound to two workloads, each with its own workload and chassis processes.

- echo-python: the chassis from the documented path, an isolated env built from this checkout's
  `uv.lock` (`uv run --isolated --package chassis --frozen --offline`), found by the kit itself.
- echo-pydanticai: the chassis from the monorepo shortcut, `@current-python`.

In both, the workload process runs with `chassis` made unimportable (`processes._guard_dir`).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_testkit.contract import WorkloadContract
from agent_testkit.plugin import agent_testkit_chassis_source  # noqa: F401  (the plugin fixture)
from agent_testkit.settings import CURRENT_PYTHON, AgentRef, ChassisSource, source_for

pytestmark = pytest.mark.slow


class TestEchoPython(WorkloadContract):
    @pytest.fixture(scope="class")
    @classmethod
    def agent_handle_ref(cls) -> AgentRef:
        return AgentRef(handle="echo_python:handle", name="echo-python")

    def test_the_chassis_runs_in_its_own_env(self, chassis: object) -> None:
        source: ChassisSource = chassis.source  # type: ignore[attr-defined]
        assert source.isolated, source
        assert "--isolated" in source.command, source.command


class TestEchoPydanticAI(WorkloadContract):
    @pytest.fixture(scope="class")
    @classmethod
    def agent_handle_ref(cls) -> AgentRef:
        return AgentRef(handle="echo_pydanticai:handle", name="echo-pydanticai")

    @pytest.fixture(scope="class")
    @classmethod
    def agent_testkit_chassis_source(cls) -> ChassisSource:
        return source_for(CURRENT_PYTHON, Path.cwd())
