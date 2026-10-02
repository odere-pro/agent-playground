"""Real adapters against real services in containers (PoC-4). Every test in this folder is a
`network` test: `make test` skips it (sockets are off), `make test-integration` runs it, and it
skips when Docker is not reachable.
"""

from __future__ import annotations

import functools
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark every test here `network` before `-m` selects, so `-m network` picks them. With
    sockets off, also mark it `skip`: a skip mark is read before any fixture is set up, so a
    module-wide fixture that opens a TCP socket never runs in the offline gate.
    """
    offline = bool(config.getoption("--disable-socket", default=False))
    for item in items:
        if HERE in item.path.parents:
            item.add_marker(pytest.mark.network)
            if offline:
                item.add_marker(
                    pytest.mark.skip(reason="sockets are disabled; run `make test-integration`")
                )


@functools.cache
def docker_problem() -> str | None:
    """Why Docker cannot be used, or `None` when `docker info` answers."""
    if shutil.which("docker") is None:
        return "docker is not on PATH"
    try:
        info = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return "docker info timed out"
    if info.returncode != 0:
        return f"docker info failed, the daemon is not running: {info.stderr.strip()[:200]}"
    return None


@pytest.fixture(autouse=True)
def _needs_sockets_and_docker(request: pytest.FixtureRequest) -> None:
    if request.config.getoption("--disable-socket", default=False):
        pytest.skip("sockets are disabled; run `make test-integration`")
    problem = docker_problem()
    if problem is not None:
        pytest.skip(problem)
