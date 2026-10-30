"""Serve an ASGI app in a thread on a Unix socket (the offline gate allows them) or on TCP.

Both run on `chassis_contracts.serving.serve_in_thread`, the one threaded-uvicorn helper.
`make test` runs with `--disable-socket --allow-unix-socket`, so TCP is for `network` tests only.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx2
import pytest
from chassis_contracts.serving import free_port, serve_in_thread

from poc_harness.outbound import TransportFactory

__all__ = ["free_port", "on_unix_socket", "require_tcp", "serve_tcp", "uds_transport"]

START_TIMEOUT_S = 10.0
SOCKET_DIR_PREFIX = "poc-"
"""Short on purpose: macOS caps a Unix socket path at 104 bytes."""


@contextmanager
def on_unix_socket(app: Any, name: str = "app.sock") -> Iterator[str]:
    """Serve `app` with uvicorn on a fresh Unix socket in a thread (its own event loop, lifespan
    on); yields the socket path. The folder is short on purpose: macOS caps the path at 104 bytes.
    """
    import uvicorn

    folder = tempfile.mkdtemp(prefix=SOCKET_DIR_PREFIX)
    uds = str(Path(folder, name))
    config = uvicorn.Config(app, uds=uds, log_level="warning")
    try:
        with serve_in_thread([config], START_TIMEOUT_S, name="uvicorn-uds"):
            yield uds
    finally:
        shutil.rmtree(folder, ignore_errors=True)


@contextmanager
def serve_tcp(build: Callable[[str], Any]) -> Iterator[str]:
    """Serve `build(url)` on uvicorn at a free loopback port in a thread; yields the URL. TCP, so
    only for `network` tests.
    """
    import uvicorn

    port = free_port()
    url = f"http://127.0.0.1:{port}"
    config = uvicorn.Config(build(url), host="127.0.0.1", port=port, log_level="warning")
    with serve_in_thread([config], START_TIMEOUT_S, name="uvicorn-tcp"):
        yield url


def require_tcp(request: pytest.FixtureRequest) -> None:
    """Skip the test when the offline gate has disabled sockets."""
    if request.config.getoption("--disable-socket", default=False):
        pytest.skip("sockets are disabled; run `uv run pytest -m network -p no:socket`")


def uds_transport(uds: str) -> TransportFactory:
    """For `point_tools_at`: a new `httpx2` transport over `uds` per request."""
    return lambda: httpx2.AsyncHTTPTransport(uds=uds)
