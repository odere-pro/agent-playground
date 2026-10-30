"""Run uvicorn servers in a thread for a test: the one threaded-uvicorn helper and `free_port`.

`serve_in_thread` runs one or more `uvicorn.Config`s on one event loop in one daemon thread, so
apps that share state (the public app and its proxy app) share a loop too. It returns once every
server listens, raises when one ends before that, and stops them all on exit.
`chassis_contracts.interface.serve_on_unix_sockets` and the `poc_harness.sockets` helpers build on
it. A TCP config needs a real socket, so only a `network` test passes one.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uvicorn

__all__ = ["free_port", "serve_in_thread"]

POLL_S = 0.02
JOIN_TIMEOUT_S = 15.0


def free_port() -> int:
    """A free loopback TCP port, picked by the OS. TCP, so only for `network` tests."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _where(config: uvicorn.Config) -> str:
    return config.uds or f"http://{config.host}:{config.port}"


@contextmanager
def serve_in_thread(
    configs: Sequence[uvicorn.Config],
    start_timeout_s: float = 20.0,
    name: str = "uvicorn",
) -> Iterator[list[uvicorn.Server]]:
    """Serve each config with uvicorn, all on one event loop in one daemon thread; yield the
    servers once every one listens. Raises `RuntimeError` when one ends, the thread dies, or the
    deadline passes first.
    """
    import uvicorn

    servers = [uvicorn.Server(config) for config in configs]
    where = [_where(config) for config in configs]
    ended: list[str] = []
    failure: list[BaseException] = []

    async def one(server: uvicorn.Server, label: str) -> None:
        try:
            await server.serve()
        finally:
            ended.append(label)

    async def main() -> None:
        await asyncio.gather(*(one(s, w) for s, w in zip(servers, where, strict=True)))

    def run() -> None:
        try:
            asyncio.run(main())
        except BaseException as exc:
            failure.append(exc)

    thread = threading.Thread(target=run, daemon=True, name=name)
    thread.start()
    try:
        deadline = time.monotonic() + start_timeout_s
        while not all(s.started for s in servers):
            if ended or failure or not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError(
                    f"uvicorn did not start on {where} (ended: {ended}, error: {failure!r})"
                )
            time.sleep(POLL_S)
        yield servers
    finally:
        for server in servers:
            server.should_exit = True
        thread.join(timeout=JOIN_TIMEOUT_S)
