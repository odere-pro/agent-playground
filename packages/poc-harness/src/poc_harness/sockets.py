"""Serve an ASGI app on a Unix socket (the offline gate allows them) or on TCP.

`socket_path` is the one helper for a socket's folder. `on_unix_socket` serves in a thread, on
`chassis_contracts.serving.serve_in_thread`, the one threaded-uvicorn helper.
`on_unix_socket_on_this_loop` serves on the test's own event loop, for fakes that one loop must
touch. `make test` runs with `--disable-socket --allow-unix-socket`, so TCP is for `network` tests
only.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
from chassis_contracts.serving import free_port, serve_in_thread

from poc_harness.outbound import TransportFactory

if TYPE_CHECKING:
    import uvicorn

__all__ = [
    "free_port",
    "on_unix_socket",
    "on_unix_socket_on_this_loop",
    "require_tcp",
    "serve_on_this_loop",
    "serve_tcp",
    "socket_path",
    "uds_transport",
]

START_TIMEOUT_S = 10.0
POLL_S = 0.01
SOCKET_DIR_PREFIX = "poc-"
"""Short on purpose: macOS caps a Unix socket path at 104 bytes."""


@contextmanager
def socket_path(name: str = "app.sock") -> Iterator[str]:
    """A Unix socket path in a fresh short folder; the folder is removed on exit."""
    folder = tempfile.mkdtemp(prefix=SOCKET_DIR_PREFIX)
    try:
        yield str(Path(folder, name))
    finally:
        shutil.rmtree(folder, ignore_errors=True)


@contextmanager
def on_unix_socket(app: Any, name: str = "app.sock") -> Iterator[str]:
    """Serve `app` with uvicorn on a fresh Unix socket in a thread (its own event loop, lifespan
    on); yields the socket path.
    """
    import uvicorn

    with socket_path(name) as uds:
        config = uvicorn.Config(app, uds=uds, log_level="warning")
        with serve_in_thread([config], START_TIMEOUT_S, name="uvicorn-uds"):
            yield uds


@asynccontextmanager
async def serve_on_this_loop(server: uvicorn.Server) -> AsyncIterator[None]:
    """Run `server` as a task on the running event loop until the block exits."""
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(START_TIMEOUT_S):
            while not server.started:
                if task.done():
                    task.result()
                    raise RuntimeError("uvicorn exited before it started")
                await asyncio.sleep(POLL_S)
        yield
    finally:
        server.should_exit = True
        server.force_exit = True
        await task


@asynccontextmanager
async def on_unix_socket_on_this_loop(app: Any, name: str = "app.sock") -> AsyncIterator[str]:
    """Serve `app` with uvicorn on a fresh Unix socket on the running event loop, lifespan off;
    yields the socket path.
    """
    import uvicorn

    with socket_path(name) as uds:
        config = uvicorn.Config(app, uds=uds, lifespan="off", log_level="warning")
        async with serve_on_this_loop(uvicorn.Server(config)):
            yield uds


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
