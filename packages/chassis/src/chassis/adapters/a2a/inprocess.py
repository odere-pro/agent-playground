"""The `inprocess` connector: the template A2A server in the chassis process, called over A2A
through `httpx.ASGITransport`. No socket. Allowed in the `fake` and `local` profiles only.

`setup` loads `config["handle"]` by dotted path (`module:attribute`), builds the app, and opens an
A2A client on it. `run` sends one message per `Request`, always streaming, and yields the chassis
events it reads back from `metadata["chassis.event"]`. Closing the stream early or a timeout sends
`CancelTaskRequest`. One span per run; the task id is the span attribute `a2a.task_id`.

`httpx.ASGITransport` runs the whole app call before it returns the body, so in this lane the
events arrive after `handle` returns: no per-delta streaming, no mid-run timeout, no mid-run cancel.
The `sidecar` lane (PoC-2) streams over a socket; a streaming ASGI bridge is PoC-2 work if the
in-memory lane must stream. It sets no `traceparent` header (PoC-2).
"""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator, Mapping
from typing import TYPE_CHECKING, Any, cast

import anyio
import httpx
from a2a.client import Client, ClientCallContext, ClientConfig, ClientFactory
from a2a.types import CancelTaskRequest
from a2a.utils.errors import A2AError
from pydantic import ValidationError

from chassis import CHASSIS_VERSION
from chassis.adapters.a2a.mapping import request_to_message, update_to_event
from chassis.adapters.a2a.server import BASE_URL, WireHandle, build_agent_card, build_app
from chassis.core.envelope import Context, Request
from chassis.core.events import SCHEMA_VERSION, End, Error, Event, Start, parse_event
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


class InProcessConnector:
    kind: Lane = "inprocess"
    capabilities: frozenset[str] = frozenset({"streaming"})

    def __init__(self) -> None:
        self.app: Any = None
        self._http: httpx.AsyncClient | None = None
        self._client: Client | None = None
        self._ports: PortBundle | None = None

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
        self._http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=BASE_URL
        )
        factory = ClientFactory(ClientConfig(httpx_client=self._http, streaming=True))
        self._client = await factory.create_from_url(BASE_URL)
        self._ports = ports

    async def _cancel(self, task_id: str) -> None:
        """Best effort: the task may already be terminal, which is fine."""
        if self._client is None:
            return
        try:
            await self._client.cancel_task(CancelTaskRequest(id=task_id))
        except (A2AError, httpx.HTTPError) as exc:
            if self._ports is not None:
                self._ports.telemetry.log(
                    "debug", "a2a cancel did not apply", task_id=task_id, reason=str(exc)
                )

    async def run(self, request: Request, ctx: Context) -> AsyncIterator[Event]:
        if self._client is None or self._ports is None:
            raise RuntimeError("inprocess connector is not set up")
        telemetry = self._ports.telemetry
        message = request_to_message(
            request.input.model_dump(mode="json"),
            ctx.model_dump(mode="json"),
            schema_version=SCHEMA_VERSION,
        )
        call = ClientCallContext(timeout=request.budget.timeout_ms / 1000)
        task_id: str | None = None
        terminal = False
        with telemetry.span(
            "chassis.engine.run", lane=self.kind, request_id=request.request_id
        ) as span:
            stream = self._client.send_message(message, context=call)
            try:
                async for response in stream:
                    if response.HasField("task") and task_id is None:
                        task_id = response.task.id
                        span.attributes["a2a.task_id"] = task_id
                    raw = update_to_event(response)
                    if raw is None:
                        continue
                    try:
                        event = parse_event(raw)
                    except (ValidationError, ValueError) as exc:
                        event = Error(code="a2a.bad_event", message=str(exc))
                    if isinstance(event, Start) and event.request_id != request.request_id:
                        event = Error(
                            code="a2a.request_mismatch",
                            message=f"start.request_id {event.request_id!r} is not "
                            f"{request.request_id!r}",
                        )
                    if isinstance(event, End | Error):
                        terminal = True
                    yield event
                    if terminal:
                        return
            except httpx.TimeoutException as exc:
                terminal = True
                yield Error(code="a2a.timeout", message=str(exc) or "timed out", retryable=True)
            finally:
                # Shielded: a client disconnect cancels this task, and an unshielded await here
                # would be skipped at its first checkpoint, leaving the A2A task running.
                with anyio.CancelScope(shield=True):
                    closer = getattr(stream, "aclose", None)
                    if closer is not None:
                        await closer()
                    if not terminal and task_id is not None:
                        await self._cancel(task_id)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
        if self._http is not None:
            await self._http.aclose()
            self._http = None
