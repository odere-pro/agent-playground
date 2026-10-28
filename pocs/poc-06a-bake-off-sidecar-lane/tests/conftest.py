"""Puts the PoC-2 to PoC-5 test folders on `sys.path`, so a PoC-6 test can build on `poc02_harness`,
`poc03_harness`, `poc04_harness`, and `poc05_harness`. Excluded from mypy (pyproject.toml), like
the PoC-4 and PoC-5 conftests: a second top-level `conftest` module would clash with the chassis
one.
"""

from __future__ import annotations

import sys
from pathlib import Path

POCS = Path(__file__).resolve().parents[2]
for _tests in (
    POCS / "poc-02-two-engines-one-contract" / "tests",
    POCS / "poc-03-one-interface-every-client" / "tests",
    POCS / "poc-04-stateless-scalable" / "tests",
    POCS / "poc-05-sandboxed" / "tests",
):
    if str(_tests) not in sys.path:
        sys.path.insert(0, str(_tests))


# --- Fixtures shared by the PoC-6a lane bindings ---

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from poc06a_harness import Backends, SidecarAddress, serve_backends, typescript_agent  # noqa: E402


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
