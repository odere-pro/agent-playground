"""PoC-6a, exit criterion 1 (every new Python workload passes the `EnginePort` contract suite over
A2A on localhost and in memory, offline against the fake model server) and criterion 2 (the
non-Python agent passes the same suite, with no change to the chassis core).

`chassis_contracts.EngineConnectorContract` is bound twice and parametrized over the trusted
engines of `poc06_harness.ENGINES`, so a new engine is one registry line:

- `TestEngineInMemory`: each Python engine behind the chassis's own `inprocess` connector, served
  by the chassis app (`poc02_harness.chassis_app`).
- `TestEngineOverA2A`: each engine behind `SidecarConnector` on a Unix socket. A Python engine is
  served by `workload-a2a`; the TypeScript agent is the built `dist` (`node dist/src/main.js`).

Every engine's model calls go to the fake model server on `scripts/bakeoff.yaml` and its tool calls
to the two-tool fake MCP server, both on Unix sockets (`poc06a_harness`). No TCP, no key. The
TypeScript cases skip only when its `node_modules` is absent (`npm ci`).

The JSON-integer and `ctx.traceparent` cases run the suite's own `json_values_handle` in each
Python engine's lane. The TypeScript agent serves one fixed handle, so they skip there; its
integers both ways are checked in `npm test` (`test/wire.test.ts`), its `traceparent` on the model
and tool calls in `test_poc06a_tasks.py`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from chassis.core.envelope import Request
from chassis.ports.engine import EngineConnector
from chassis_contracts import EngineConnectorContract
from chassis_contracts.engine import JSON_VALUES_HANDLE
from chassis_contracts.helpers import make_request
from poc02_harness import a2a_on_unix_socket, chassis_app, running
from poc06_harness import ENGINES, SMOKE
from poc06a_harness import (
    Backends,
    SidecarAddress,
    python_engines,
    sidecar_connector,
    sidecar_engines,
    wire_python_engine,
)

SIDECAR_ENGINES = list(sidecar_engines())
PYTHON_ENGINES = list(python_engines())


class _Binding(EngineConnectorContract):
    """What both bindings share: the engine under test, and a request the bakeoff script answers."""

    @pytest.fixture
    def run_request(self) -> Request:
        return make_request(text=SMOKE.text)


class TestEngineInMemory(_Binding):
    """Exit criterion 1, in memory: each Python engine behind the `inprocess` connector."""

    @pytest.fixture(params=PYTHON_ENGINES)
    def engine_name(self, request: pytest.FixtureRequest) -> str:
        return str(request.param)

    @pytest.fixture
    async def engine(
        self, engine_name: str, backends: Backends, monkeypatch: pytest.MonkeyPatch
    ) -> AsyncIterator[EngineConnector]:
        wire_python_engine(monkeypatch, ENGINES[engine_name], backends)
        app = chassis_app(ENGINES[engine_name].handle)
        async with running(app):
            yield app.state.ports.engine

    @pytest.fixture
    async def json_values_engine(self) -> AsyncIterator[EngineConnector]:
        app = chassis_app(JSON_VALUES_HANDLE)
        async with running(app):
            yield app.state.ports.engine


class TestEngineOverA2A(_Binding):
    """Exit criteria 1 and 2, over A2A on a Unix socket: each trusted engine behind
    `SidecarConnector`."""

    @pytest.fixture(params=SIDECAR_ENGINES)
    def engine_name(self, request: pytest.FixtureRequest) -> str:
        return str(request.param)

    @pytest.fixture
    async def engine(
        self,
        engine_name: str,
        backends: Backends,
        monkeypatch: pytest.MonkeyPatch,
        request: pytest.FixtureRequest,
    ) -> AsyncIterator[EngineConnector]:
        spec = ENGINES[engine_name]
        if spec.language == "typescript":
            address: SidecarAddress = request.getfixturevalue("typescript")
            async with sidecar_connector(address) as connector:
                yield connector
            return
        wire_python_engine(monkeypatch, spec, backends)
        with a2a_on_unix_socket(spec.handle) as (url, uds):
            async with sidecar_connector(SidecarAddress(url, uds)) as connector:
                yield connector

    @pytest.fixture
    async def json_values_engine(self, engine_name: str) -> AsyncIterator[EngineConnector]:
        if ENGINES[engine_name].language != "python":
            pytest.skip(f"{engine_name} serves one fixed handle; it cannot run json_values_handle")
        with a2a_on_unix_socket(JSON_VALUES_HANDLE) as (url, uds):
            async with sidecar_connector(SidecarAddress(url, uds)) as connector:
                yield connector
