"""Fixtures shared by the PoC-6a lane bindings. Excluded from mypy (pyproject.toml), like every PoC
conftest: a second top-level `conftest` module would clash with the chassis one.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from poc_harness.sidecar_lane import Backends, SidecarAddress, serve_backends, typescript_agent


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
