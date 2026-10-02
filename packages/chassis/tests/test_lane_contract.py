"""The lane contract suite (`chassis_contracts.lane.LaneContract`) over the chassis's lanes: the
template server in memory (`inprocess`), the same server over a Unix socket (`sidecar`), and the
template server a Python workload ships (`workload_a2a.server`) over a Unix socket
(`sidecar-workload-server`). The offline gate refuses TCP and allows Unix sockets.

Named `test_lane_contract.py`, not `test_lanes.py`: the PoC-2 folder has a `test_lanes.py`, and two
test modules with one basename break collection under pytest's default import mode.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from functools import partial
from typing import Any, ClassVar

import pytest
from a2a_uds import SIDECAR_URL, serve_uds
from chassis.adapters.a2a import InProcessConnector, SidecarConnector
from chassis.adapters.a2a.inprocess import load_handle
from chassis.adapters.a2a.server import WireHandle, build_agent_card, build_app
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.ports.engine import EngineConnector
from chassis_contracts import LaneContract, LaneFactory


def _bundle(engine: EngineConnector) -> PortBundle:
    return PortBundle(
        model=ScriptedModel(), engine=engine, config=InMemoryConfig(), telemetry=InMemoryTelemetry()
    )


@asynccontextmanager
async def _inprocess(path: str) -> AsyncIterator[EngineConnector]:
    connector = InProcessConnector()
    await connector.setup({"connector": "inprocess", "handle": path}, _bundle(connector))
    try:
        yield connector
    finally:
        await connector.close()


def _chassis_server(handle: WireHandle, name: str) -> Any:
    return build_app(handle, build_agent_card(name=name, version="1", url=SIDECAR_URL))


def _workload_server(handle: WireHandle, name: str) -> Any:
    from workload_a2a.server import build_agent_card as card
    from workload_a2a.server import build_app as app

    return app(handle, card(name=name, version="1", url=SIDECAR_URL))


@asynccontextmanager
async def _sidecar(
    build: Callable[[WireHandle, str], Any], path: str
) -> AsyncIterator[EngineConnector]:
    async with serve_uds(build(load_handle(path), path)) as uds:
        connector = SidecarConnector()
        await connector.setup(
            {"connector": "sidecar", "url": SIDECAR_URL, "uds": uds}, _bundle(connector)
        )
        try:
            yield connector
        finally:
            await connector.close()


class TestChassisLanes(LaneContract):
    lane_labels: ClassVar[tuple[str, ...]] = ("inprocess", "sidecar", "sidecar-workload-server")

    @pytest.fixture
    def lanes(self) -> Mapping[str, LaneFactory]:
        return {
            "inprocess": _inprocess,
            "sidecar": partial(_sidecar, _chassis_server),
            "sidecar-workload-server": partial(_sidecar, _workload_server),
        }
