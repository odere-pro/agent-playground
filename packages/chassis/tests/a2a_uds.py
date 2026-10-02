"""Test support for the `sidecar` lane: serve an ASGI app on uvicorn over a Unix domain socket in a
background task, and record what reaches it. The offline gate refuses TCP and allows Unix sockets.
Not a test module; `test_contracts.py` and `test_a2a_sidecar.py` import it.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import uvicorn

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

SIDECAR_URL = "http://127.0.0.1:9000"
"""What the config names. Over `uds` the host and port only fill the `Host` header."""


@dataclass
class Seen:
    path: str
    headers: dict[str, str]
    method: str | None = None
    """The JSON-RPC method, for example `SendStreamingMessage` or `CancelTask`."""


@dataclass
class Recorder:
    """ASGI middleware: records the path, headers, and JSON-RPC method of every HTTP request."""

    app: ASGIApp
    seen: list[Seen] = field(default_factory=list)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        entry = Seen(
            path=str(scope["path"]),
            headers={k.decode().lower(): v.decode() for k, v in scope["headers"]},
        )
        self.seen.append(entry)
        body = bytearray()

        async def recording_receive() -> Message:
            message = await receive()
            if message["type"] == "http.request":
                body.extend(message.get("body", b""))
                if not message.get("more_body") and body:
                    try:
                        entry.method = json.loads(bytes(body)).get("method")
                    except ValueError:
                        entry.method = None
            return message

        await self.app(scope, recording_receive, send)

    def rpc(self) -> list[Seen]:
        return [s for s in self.seen if s.method is not None]


@asynccontextmanager
async def serve_uds(app: Any) -> AsyncIterator[str]:
    """Serve `app` over a fresh Unix socket; yields the socket path. The path is short on
    purpose: macOS caps a Unix socket path at 104 bytes.
    """
    async with serve_uds_server(app) as (path, _):
        yield path


def kill_connections(server: uvicorn.Server) -> int:
    """Abort every open connection of `server`, the way a sidecar that dies mid-stream drops
    them. Returns how many it aborted.
    """
    connections = list(server.server_state.connections)
    for connection in connections:
        transport = getattr(connection, "transport", None)
        if transport is not None:
            transport.abort()
    return len(connections)


@asynccontextmanager
async def serve_uds_server(app: Any) -> AsyncIterator[tuple[str, uvicorn.Server]]:
    """`serve_uds`, and the uvicorn server too, for a test that kills it mid-run."""
    folder = Path(tempfile.mkdtemp(prefix="ch-"))
    path = str(folder / "a2a.sock")
    server = uvicorn.Server(uvicorn.Config(app, uds=path, log_level="warning", lifespan="off"))
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    task.result()
                    raise RuntimeError("uvicorn exited before it started")
                await asyncio.sleep(0.01)
        yield path, server
    finally:
        server.should_exit = True
        server.force_exit = True
        await task
        shutil.rmtree(folder, ignore_errors=True)
