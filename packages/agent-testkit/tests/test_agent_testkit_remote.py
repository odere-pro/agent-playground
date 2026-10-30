"""The remote-lane fixture: the kit's lane handle behind the real `RemoteConnector`."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from agent_testkit.lane_cases import CASE_KEY, LANE_HANDLE
from agent_testkit.plugin import agent_testkit_chassis_source  # noqa: F401 (a fixture)
from agent_testkit.settings import AgentRef
from agent_testkit.suites import RemoteLane


class TestRemoteLane(RemoteLane):
    remote_input: ClassVar[dict[str, Any]] = {"data": {CASE_KEY: "deltas"}}

    @pytest.fixture(scope="class")
    def agent_handle_ref(self) -> AgentRef:
        return AgentRef(handle=LANE_HANDLE, name="agent-testkit-remote")
