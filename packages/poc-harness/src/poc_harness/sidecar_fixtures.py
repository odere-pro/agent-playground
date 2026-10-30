"""The fixtures of the sidecar-lane suites. A conftest takes them by importing them.

A test that uses `wired` or `address` parametrizes `engine_name`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from poc_harness.bakeoff_tasks import ENGINES
from poc_harness.sidecar_lane import (
    Backends,
    SidecarAddress,
    serve_backends,
    typescript_agent,
    wire_python_engine,
)

__all__ = ["_backends_for_module", "address", "backends", "typescript", "wired"]


@pytest.fixture(scope="module")
def _backends_for_module() -> Iterator[Backends]:
    """The fake model server and the two-tool MCP server, each on a Unix socket, for a module."""
    with serve_backends() as backends:
        yield backends


@pytest.fixture
def backends(_backends_for_module: Backends) -> Backends:
    """The module's backends with the recordings cleared, so a test sees only its own requests."""
    _backends_for_module.reset()
    return _backends_for_module


@pytest.fixture(scope="module")
def typescript(_backends_for_module: Backends) -> Iterator[SidecarAddress]:
    """The TypeScript agent (`node dist/src/main.js`) on a Unix socket, started when a test asks
    for it. Skips when the workload's `node_modules` is absent (`npm ci`)."""
    with typescript_agent(_backends_for_module) as address:
        yield address


@pytest.fixture
def address(request: pytest.FixtureRequest) -> SidecarAddress | None:
    """The TypeScript agent for a TypeScript cell, else None."""
    if ENGINES[request.getfixturevalue("engine_name")].language != "typescript":
        return None
    found: SidecarAddress = request.getfixturevalue("typescript")
    return found


@pytest.fixture
def wired(engine_name: str, backends: Backends, monkeypatch: pytest.MonkeyPatch) -> Backends:
    """The backends, with a Python engine's hooks pointed at them."""
    if ENGINES[engine_name].language == "python":
        wire_python_engine(monkeypatch, ENGINES[engine_name], backends)
    return backends
