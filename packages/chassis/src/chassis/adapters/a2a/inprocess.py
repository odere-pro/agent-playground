"""The `inprocess` connector: the template A2A server in the chassis process, called over A2A
through `StreamingASGITransport` (`asgi_stream.py`). No socket. Allowed in the `fake` and
`local` profiles only.

`setup` loads `config["handle"]` by dotted path (`module:attribute`), builds the app, and opens an
A2A client on it. Everything after that is `A2AConnector`: one message per `Request`, always
streaming, the run's `traceparent` both as the HTTP header on every request of the run and in
`ctx.traceparent`, cancel, and one span per run.

The transport hands the response back when the app sends its headers and streams the body as
the app sends it, so the events arrive one at a time, as in `sidecar`. The connector reads the
task id from the first A2A item; a timeout or a caller that leaves then sends `CancelTask`, the
cancel every lane uses, and `handle` sees `CancelledError` at its await (009 CH-1, P-7). The
transport applies no HTTP timeout; the connector's run deadline (`budget.timeout_ms`) ends the
run with `error {code: "a2a.timeout"}`. Before 2026-11-13 the lane used `httpx.ASGITransport`,
which returns the body only after the app ends: the events came in one batch, no task id reached
the connector in time, no cancel went out, and `handle` ran on.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

import httpx

from chassis import CHASSIS_VERSION
from chassis.adapters.a2a.asgi_stream import StreamingASGITransport
from chassis.adapters.a2a.connector import A2AConnector
from chassis.adapters.a2a.server import BASE_URL, WireHandle, build_agent_card, build_app
from chassis.ports.engine import Lane

if TYPE_CHECKING:
    from chassis.ports.bundle import PortBundle


def load_handle(path: str) -> WireHandle:
    """Import `module:attribute`, the way an entry point is loaded."""
    module_name, sep, attr = path.partition(":")
    if not sep or not module_name or not attr:
        raise ValueError(f"engine.handle must be 'module:attribute', got {path!r}")
    module = importlib.import_module(module_name)
    try:
        handle = getattr(module, attr)
    except AttributeError as exc:
        raise ValueError(f"{module_name} has no attribute {attr!r}") from exc
    if not callable(handle):
        raise ValueError(f"{path} is not callable")
    return cast(WireHandle, handle)


class InProcessConnector(A2AConnector):
    """`probe()` is `True` once set up: the workload is this process, so it answers when the
    chassis does.
    """

    kind: Lane = "inprocess"

    def __init__(self) -> None:
        super().__init__()
        self.app: Any = None
        self.transport: StreamingASGITransport | None = None

    async def setup(self, config: Mapping[str, Any], ports: PortBundle) -> None:
        path = config.get("handle")
        if not isinstance(path, str) or not path:
            raise ValueError("engine.handle is required for the inprocess connector")
        handle = load_handle(path)
        card_cfg = dict(config.get("card") or {})
        card = build_agent_card(
            name=str(card_cfg.get("name") or path),
            version=str(card_cfg.get("version") or CHASSIS_VERSION),
            description=str(card_cfg.get("description") or ""),
        )
        self.app = build_app(handle, card)
        self.transport = StreamingASGITransport(self.app)
        await self._open(
            httpx.AsyncClient(transport=self.transport, base_url=BASE_URL), BASE_URL, ports
        )

    async def probe(self) -> bool:
        return self._client is not None
