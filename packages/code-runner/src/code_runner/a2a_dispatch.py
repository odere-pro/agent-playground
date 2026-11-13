"""The remote-run front door: one SandboxClaim per A2A run (core review R6-11).

A PoC-6 remote pod used to be long-lived, so runs shared its uid, its `/tmp`, and its process
tree. The front door gives each run a fresh pod from the engine's warm pool and deletes it after.

One port per engine (`engine_ports`, `create_front_doors`): a port serves only its own engine,
so the network policy can let each chassis reach its own engine's port and no other. A request on
a port for another engine's path is 404 and claims nothing.

Per engine path `/<engine>`:

- `GET /<engine>/.well-known/agent-card.json`: the engine's pinned card (a mounted file), with
  every interface URL set to the door's own `/<engine>` URL. Never makes a claim.
- `POST /<engine>`, a streaming message (`STREAM_METHODS`): create a claim on the pool
  `<pool_prefix><engine>`, wait for Ready, forward the body and the caller's headers (its bearer
  and `traceparent` included) to the pod's IP on port 9000, stream the response back unchanged,
  and delete the claim when the stream ends, fails, or the caller goes away.
- `POST /<engine>`, a cancel (`CANCEL_METHODS`): sent to the pod that runs that task; an unknown
  task id is the A2A task-not-found error.
- Any other method: a JSON-RPC error, no claim.

No inbound bearer here: each chassis sends its own remote token, which only its remote checks
(`--require-token-env`). The door holds no remote token and never reads the caller's. Network
policy limits who reaches the door. A platform fault before the stream started (no free slot, a
failed claim, a pod that never answers, a pod 5xx) is HTTP 503 with a fixed body
(`PLATFORM_FAULT_STATUS`), so the chassis connector reports a retryable `a2a.transport`. A fault
once the pod's stream began (an encoded stream, a lost or late stream) and a failed cancel are
the JSON-RPC error `UNAVAILABLE_CODE`, which the chassis reports as `a2a.rpc_error`, not
retryable (P-9): a retry could repeat side effects. Errors have fixed text: never an
upstream body, a header value, or the token. The log line has the engine, the outcome, sizes,
and timing.

The claim lifecycle, the API client, and the pod address check are `code_runner.dispatch`'s.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from code_runner.asgi import guard
from code_runner.dispatch import CONNECT_FAILED, Claims
from code_runner.runner import DEFAULT_LIMITS, RunnerError

STREAM_METHODS = frozenset({"SendStreamingMessage", "message/stream"})
CANCEL_METHODS = frozenset({"CancelTask", "tasks/cancel"})
# The door's own JSON-RPC error answer. Since P-9 the chassis connector reports any JSON-RPC
# error answer as `a2a.rpc_error`, not retryable (contract v9, E.15).
UNAVAILABLE_CODE = -32000
# A platform fault before the stream started (no free slot, a failed claim, a pod that never
# answers, a pod 5xx) is not the workload's answer: HTTP 503 with a fixed body, which the a2a-sdk
# raises as a plain `A2AClientError` ("HTTP Error 503") and the chassis connector reports as a
# retryable `a2a.transport`. No stream byte reached the caller, so a retry repeats nothing
# (contract v9, E, decided 2026-11-13).
PLATFORM_FAULT_STATUS = 503
PLATFORM_FAULT_BODY = {"error": "sandbox_unavailable"}  # suggested: the fixed body
PLATFORM_FAULT_RETRY_AFTER_S = 1  # suggested: as the chassis's 503 `overloaded` (contract v9, D.12)
POD_FAULT_STATUSES = range(500, 600)  # a pod 5xx before the stream: the platform fault answer
# The pod refused the caller's credential (401, 403) before the stream: the door answers with the
# same HTTP status and a fixed body, as the pod did before the door existed. The a2a-sdk raises an
# HTTP error status as A2AClientHTTPError, which the chassis connector reports as `a2a.transport`.
REFUSED_STATUSES = frozenset({401, 403})
TASK_NOT_FOUND = -32001
METHOD_NOT_FOUND = -32601
INVALID_REQUEST = -32600
PARSE_ERROR = -32700
CARD_PATH = "/.well-known/agent-card.json"
REMOTE_PORT = 9000
REMOTE_PATH = "/"
SCAN_LINE_CAP = 64 * 1024  # suggested: a stream line longer than this is not read for a task id
CANCEL_ANSWER_CAP = 64 * 1024  # suggested: a cancel answer is one small JSON-RPC object
MAX_TCP_PORT = 65535
_ENGINE_NAME = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?")
# Hop-by-hop headers and the ones httpx sets itself are not forwarded either way.
_SKIP_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
        "proxy-authorization",
        "proxy-connection",
    }
)

log = logging.getLogger("code_runner.a2a_dispatch")


@dataclass(frozen=True)
class Engine:
    """One engine path: its pool and its pinned agent card."""

    name: str
    pool: str
    card: Mapping[str, Any]


@dataclass(frozen=True)
class FrontDoorConfig:
    """Values are `suggested:`; the run deadline is the bake-off's longest run budget."""

    namespace: str
    public_url: str
    max_claims: int = 4
    claim_timeout_s: float = 60
    poll_s: float = 0.2
    run_deadline_s: float = 600
    shutdown_grace_s: float = 60
    connect_retry_s: float = 5
    headers_timeout_s: float = 30  # a pod that accepts and never answers frees its claim
    cancel_timeout_s: float = 10
    remote_port: int = REMOTE_PORT
    # engine name -> the port that serves it; empty: one app serves every engine (tests, PoC-5)
    engine_ports: Mapping[str, int] = field(default_factory=dict)


@dataclass
class _Run:
    engine: str
    host: str
    task_id: str | None = None
    scanned: bytearray = field(default_factory=bytearray)


@dataclass
class _Claim:
    """One run's claim: its name, and what the log line reports."""

    name: str
    engine: str
    request_bytes: int
    may_exist: bool = True  # until the create proves it applied nothing
    released: bool = False
    outcome: str = "ok"
    response_bytes: int = 0
    started: float = field(default_factory=time.monotonic)


def load_engines(cards_dir: str | Path, *, pool_prefix: str) -> dict[str, Engine]:
    """One engine per `<engine>.json` card file. A bad name or card stops the start."""
    engines: dict[str, Engine] = {}
    for path in sorted(Path(cards_dir).glob("*.json")):
        name = path.stem
        if not _ENGINE_NAME.fullmatch(name):
            raise ValueError(f"engine name {name!r} is not a DNS label")
        card = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(card, dict):
            raise ValueError(f"engine {name}: the card is not a JSON object")
        engines[name] = Engine(name, f"{pool_prefix}{name}", card)
    if not engines:
        raise ValueError("no engine card files found")
    return engines


def parse_engine_ports(specs: Sequence[str], engines: Mapping[str, Engine]) -> dict[str, int]:
    """`NAME=PORT` per engine: every engine exactly one port, every port one engine. A bad
    value stops the start."""
    ports: dict[str, int] = {}
    for spec in specs:
        name, sep, value = spec.partition("=")
        if not sep or not value.isdigit():
            raise ValueError(f"engine port {spec!r} is not NAME=PORT")
        port = int(value)
        if not 0 < port <= MAX_TCP_PORT:
            raise ValueError(f"engine port {spec!r}: {port} is not a TCP port")
        if name not in engines:
            raise ValueError(f"engine port {spec!r}: no card for engine {name!r}")
        if name in ports:
            raise ValueError(f"engine port {spec!r}: engine {name!r} has two ports")
        if port in ports.values():
            raise ValueError(f"engine port {spec!r}: port {port} serves two engines")
        ports[name] = port
    missing = sorted(set(engines) - set(ports))
    if missing:
        raise ValueError(f"engine port: no port for {', '.join(missing)}")
    return ports


def _rpc_error(rpc_id: object, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}
    )


def _unavailable(rpc_id: object, message: str) -> JSONResponse:
    return _rpc_error(rpc_id, UNAVAILABLE_CODE, f"sandbox_unavailable: {message}")


def _platform_fault() -> JSONResponse:
    """The one answer to a fault before the stream started: no request id, no claim name, no
    error text, no pod header."""
    return JSONResponse(
        dict(PLATFORM_FAULT_BODY),
        status_code=PLATFORM_FAULT_STATUS,
        headers={"Retry-After": str(PLATFORM_FAULT_RETRY_AFTER_S)},
    )


def _connection_named(values: list[str]) -> frozenset[str]:
    """The header names the `Connection` headers list: hop-by-hop too, so never forwarded."""
    return frozenset(n.strip().lower() for v in values for n in v.split(",") if n.strip())


def _forward_headers(request: Request) -> dict[str, str]:
    """The run's headers for the pod. It asks for no encoding, so the stream reaches the chassis
    plain: the door scans it for the task id, and the chassis caps no decoded bytes."""
    skip = _SKIP_HEADERS | _connection_named(request.headers.getlist("connection"))
    skip = skip | {"accept-encoding"}
    kept = {k: v for k, v in request.headers.items() if k.lower() not in skip}
    return {**kept, "accept-encoding": "identity"}


def _response_headers(response: httpx.Response) -> dict[str, str]:
    skip = _SKIP_HEADERS | _connection_named(response.headers.get_list("connection"))
    return {k: v for k, v in response.headers.items() if k.lower() not in skip}


def _encoded(response: httpx.Response) -> bool:
    """True when the pod sent an encoded body despite `accept-encoding: identity`."""
    values = response.headers.get_list("content-encoding", split_commas=True)
    return any(v.strip().lower() not in ("", "identity") for v in values)


def _task_id_of(message: object) -> str | None:
    """The task id in one stream answer, v1 (`task`, `statusUpdate`, ...) or v0.3 (`kind`)."""
    result = message.get("result") if isinstance(message, dict) else None
    if not isinstance(result, dict):
        return None
    task = result.get("task")
    if isinstance(task, dict) and isinstance(task.get("id"), str):
        return str(task["id"])
    for key in ("statusUpdate", "artifactUpdate", "message"):
        inner = result.get(key)
        if isinstance(inner, dict) and isinstance(inner.get("taskId"), str):
            return str(inner["taskId"])
    if result.get("kind") == "task" and isinstance(result.get("id"), str):
        return str(result["id"])
    value = result.get("taskId")
    return value if isinstance(value, str) else None


def _scan(run: _Run, chunk: bytes) -> None:
    """Reads SSE `data:` lines until the task id is seen. The bytes sent on are not changed."""
    if run.task_id is not None:
        return
    run.scanned += chunk
    *lines, rest = bytes(run.scanned).split(b"\n")
    run.scanned = bytearray(rest if len(rest) <= SCAN_LINE_CAP else b"")
    for line in lines:
        if not line.startswith(b"data:"):
            continue
        try:
            found = _task_id_of(json.loads(line[len(b"data:") :]))
        except (ValueError, RecursionError):
            continue
        if found is not None:
            run.task_id = found
            run.scanned = bytearray()
            return


class _ClaimedStream(StreamingResponse):
    """A streaming response whose `cleanup` runs on every path, even if it never starts."""

    def __init__(
        self, content: AsyncIterator[bytes], cleanup: Callable[[], Awaitable[None]], **kw: Any
    ) -> None:
        super().__init__(content, **kw)
        self._cleanup = cleanup

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._cleanup()


class RunDispatcher:
    """Claims a fresh remote pod per streaming run; holds the task id to pod map for cancels."""

    def __init__(
        self,
        api: httpx.AsyncClient,
        upstream: httpx.AsyncClient,
        config: FrontDoorConfig,
        engines: Mapping[str, Engine],
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._upstream = upstream
        self.config = config
        self.engines = dict(engines)
        self.claims = Claims(
            api,
            config.namespace,
            claim_timeout_s=config.claim_timeout_s,
            poll_s=config.poll_s,
            now=now,
        )
        self._slots = asyncio.Semaphore(config.max_claims)
        self._runs: dict[str, _Run] = {}

    def task_count(self) -> int:
        return sum(1 for r in self._runs.values() if r.task_id is not None)

    def url_for(self, engine: Engine) -> str:
        """The door's URL for the engine: on the engine's own port when it has one."""
        base = self.config.public_url.rstrip("/")
        port = self.config.engine_ports.get(engine.name)
        if port is not None:
            parts = urlsplit(base)
            base = parts._replace(netloc=f"{parts.hostname}:{port}").geturl()
        return f"{base}/{engine.name}"

    def card(self, engine: Engine) -> dict[str, Any]:
        """The pinned card with every URL the client could post to set to the door."""
        url = self.url_for(engine)
        card: dict[str, Any] = json.loads(json.dumps(engine.card))
        interfaces = card.get("supportedInterfaces")
        if isinstance(interfaces, list):
            for interface in interfaces:
                if isinstance(interface, dict):
                    interface["url"] = url
        if "url" in card:
            card["url"] = url
        return card

    async def cancel(
        self, engine: Engine, rpc_id: object, body: bytes, request: Request
    ) -> Response:
        params = json.loads(body).get("params")
        task_id = params.get("id") if isinstance(params, dict) else None
        run = next(
            (
                r
                for r in self._runs.values()
                if r.engine == engine.name and r.task_id is not None and r.task_id == task_id
            ),
            None,
        )
        if run is None:
            return _rpc_error(rpc_id, TASK_NOT_FOUND, "task not found")
        url = f"http://{run.host}:{self.config.remote_port}{REMOTE_PATH}"
        try:
            async with asyncio.timeout(self.config.cancel_timeout_s):
                return await self._send_cancel(engine, rpc_id, url, body, request)
        except TimeoutError:
            log.warning("a2a-dispatch cancel_timeout engine=%s", engine.name)
            return _unavailable(rpc_id, "the cancel did not reach the sandbox")

    async def _send_cancel(
        self, engine: Engine, rpc_id: object, url: str, body: bytes, request: Request
    ) -> Response:
        try:
            async with self._upstream.stream(
                "POST", url, content=body, headers=_forward_headers(request)
            ) as answer:
                data = bytearray()
                async for chunk in answer.aiter_raw():
                    if len(data) + len(chunk) > CANCEL_ANSWER_CAP:
                        return _unavailable(rpc_id, "the cancel answer was over the size cap")
                    data += chunk
                return Response(
                    bytes(data), status_code=answer.status_code, headers=_response_headers(answer)
                )
        except httpx.HTTPError as exc:
            log.warning(
                "a2a-dispatch cancel_failed engine=%s error=%s", engine.name, type(exc).__name__
            )
            return _unavailable(rpc_id, "the cancel did not reach the sandbox")

    async def stream(
        self, engine: Engine, rpc_id: object, body: bytes, request: Request
    ) -> Response:
        try:
            async with asyncio.timeout(self.config.claim_timeout_s):
                await self._slots.acquire()
        except TimeoutError:
            log.warning("a2a-dispatch no_free_slot engine=%s", engine.name)
            return _platform_fault()
        claim = _Claim(f"run-{uuid.uuid4().hex}", engine.name, len(body))
        try:
            host = await self._claim(engine, claim)
            upstream = await self._open(host, body, _forward_headers(request))
        except RunnerError as exc:
            # No claim, or the claimed pod never answered: no stream byte went to the caller.
            claim.outcome = exc.code
            await self._release(claim)
            return _platform_fault()
        except BaseException:
            claim.outcome = "error"
            await self._release(claim)
            raise
        if upstream.status_code != 200:
            status = upstream.status_code
            claim.outcome = f"status_{status}"
            try:
                await upstream.aclose()
            finally:
                await self._release(claim)
            if status in REFUSED_STATUSES:
                return JSONResponse({"error": "sandbox_refused"}, status_code=status)
            if status in POD_FAULT_STATUSES:
                return _platform_fault()
            return _unavailable(rpc_id, f"the sandbox refused the run (HTTP {status})")
        if _encoded(upstream):
            claim.outcome = "encoded"
            try:
                await upstream.aclose()
            finally:
                await self._release(claim)
            return _unavailable(rpc_id, "the sandbox sent an encoded stream")
        run = _Run(engine.name, host)
        self._runs[claim.name] = run

        async def close_and_release() -> None:
            try:
                await upstream.aclose()
            finally:
                await self._release(claim)

        return _ClaimedStream(
            self._relay(upstream, run, claim, rpc_id),
            close_and_release,
            status_code=200,
            headers=_response_headers(upstream),
        )

    async def _claim(self, engine: Engine, claim: _Claim) -> str:
        created = await self.claims.create(
            claim.name, engine.pool, self.config.run_deadline_s + self.config.shutdown_grace_s
        )
        claim.may_exist = created is not None
        if not created:
            raise RunnerError("sandbox_unavailable", "no sandbox could be claimed")
        return await self.claims.wait_ready(claim.name)

    async def _relay(
        self, upstream: httpx.Response, run: _Run, claim: _Claim, rpc_id: object
    ) -> AsyncIterator[bytes]:
        """The pod's bytes, unchanged, until the stream ends. On the run deadline or a lost pod,
        one last SSE event: the door's own JSON-RPC error. The deadline is one for the
        whole run, applied to each read, never across a `yield`."""
        deadline = asyncio.get_running_loop().time() + self.config.run_deadline_s
        chunks = upstream.aiter_raw()
        tail = b"\n\n"
        while True:
            try:
                async with asyncio.timeout_at(deadline):
                    chunk = await anext(chunks)
            except StopAsyncIteration:
                return
            except TimeoutError:
                claim.outcome = "run_deadline"
                message = "sandbox_lost: the run passed its deadline"
                break
            except httpx.HTTPError as exc:
                claim.outcome = f"lost_{type(exc).__name__}"
                message = "sandbox_lost: the sandbox stream broke"
                break
            _scan(run, chunk)
            claim.response_bytes += len(chunk)
            tail = (tail + chunk)[-2:]
            yield chunk
        error = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {"code": UNAVAILABLE_CODE, "message": message},
        }
        # A blank line first ends any event the pod left half sent.
        yield (
            (b"" if tail == b"\n\n" else b"\n\n") + b"data: " + json.dumps(error).encode() + b"\n\n"
        )

    async def _release(self, claim: _Claim) -> None:
        """Once per claim, on every path: forget the task, delete the claim, free the slot."""
        if claim.released:
            return
        claim.released = True
        self._runs.pop(claim.name, None)
        try:
            if claim.may_exist:
                await self.claims.delete_shielded(claim.name)
        finally:
            self._slots.release()
            log.info(
                "a2a-dispatch engine=%s outcome=%s request_bytes=%d response_bytes=%d seconds=%.2f",
                claim.engine,
                claim.outcome,
                claim.request_bytes,
                claim.response_bytes,
                time.monotonic() - claim.started,
            )

    async def _open(self, host: str, body: bytes, headers: dict[str, str]) -> httpx.Response:
        """The upstream response, headers read, body not yet. Retries only a failed connect:
        Ready may come a moment before the server listens."""
        url = f"http://{host}:{self.config.remote_port}{REMOTE_PATH}"
        deadline = time.monotonic() + self.config.connect_retry_s
        while True:
            request = self._upstream.build_request("POST", url, content=body, headers=headers)
            try:
                async with asyncio.timeout(self.config.headers_timeout_s):
                    return await self._upstream.send(request, stream=True)
            except TimeoutError:
                log.warning("a2a-dispatch sandbox_silent")
                raise RunnerError("sandbox_unavailable", "the sandbox did not answer") from None
            except CONNECT_FAILED as exc:
                if time.monotonic() >= deadline:
                    log.warning("a2a-dispatch sandbox_unreachable error=%s", type(exc).__name__)
                    raise RunnerError("sandbox_unavailable", "the sandbox did not answer") from None
            except httpx.HTTPError as exc:
                log.warning("a2a-dispatch sandbox_lost error=%s", type(exc).__name__)
                raise RunnerError("sandbox_unavailable", "the sandbox did not answer") from None
            await asyncio.sleep(self.config.poll_s)


def _parse(body: bytes) -> tuple[object, str | None, JSONResponse | None]:
    """The request id and method, or the JSON-RPC error to answer with."""
    try:
        message = json.loads(body)
    except (ValueError, RecursionError):
        return None, None, _rpc_error(None, PARSE_ERROR, "parse error")
    if not isinstance(message, dict) or not isinstance(message.get("method"), str):
        return None, None, _rpc_error(None, INVALID_REQUEST, "invalid request")
    rpc_id = message.get("id")
    if not (rpc_id is None or isinstance(rpc_id, str | int)) or isinstance(rpc_id, bool):
        return None, None, _rpc_error(None, INVALID_REQUEST, "invalid request")
    return rpc_id, str(message["method"]), None


def create_front_doors(door: RunDispatcher) -> dict[int, ASGIApp]:
    """One app per engine port, each serving only its engine. Needs a port for every engine."""
    ports = dict(door.config.engine_ports)
    if set(ports) != set(door.engines):
        raise ValueError("engine port: want exactly one port for every engine")
    return {port: create_front_door(door, only=name) for name, port in ports.items()}


def create_front_door(door: RunDispatcher, *, only: str | None = None) -> ASGIApp:
    """The ASGI app: the routes behind the shared body cap (`code_runner.asgi.guard`). With
    `only`, every engine path but that one is 404 (`unknown_engine`), before any claim."""
    if only is not None and only not in door.engines:
        raise ValueError(f"engine port: no card for engine {only!r}")

    def _engine(request: Request) -> Engine | None:
        name = str(request.path_params["engine"])
        if only is not None and name != only:
            return None
        return door.engines.get(name)

    async def card(request: Request) -> Response:
        engine = _engine(request)
        if engine is None:
            return JSONResponse({"error": "unknown_engine"}, status_code=404)
        return JSONResponse(door.card(engine))

    async def rpc(request: Request) -> Response:
        engine = _engine(request)
        if engine is None:
            return JSONResponse({"error": "unknown_engine"}, status_code=404)
        body = await request.body()
        rpc_id, method, refusal = _parse(body)
        if refusal is not None:
            return refusal
        if method in STREAM_METHODS:
            return await door.stream(engine, rpc_id, body, request)
        if method in CANCEL_METHODS:
            return await door.cancel(engine, rpc_id, body, request)
        return _rpc_error(rpc_id, METHOD_NOT_FOUND, "method not found")

    async def health(_request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    app = Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/{engine}" + CARD_PATH, card, methods=["GET"]),
            Route("/{engine}", rpc, methods=["POST"]),
            Route("/{engine}/", rpc, methods=["POST"]),
        ]
    )
    return guard(app, limits=DEFAULT_LIMITS)
