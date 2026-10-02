"""InterfaceContract: every public interface answers every engine the same way, in every lane, end
to end (PoC-3 open note, section 9; exit criteria 1 and 2).

The matrix is interface {native, openai, anthropic, mcp} x engine x mode {stream, complete} x
lane. Each cell runs a real chassis app under uvicorn on a Unix socket, so a stream reaches the
client frame by frame, as it would over TCP; nothing is buffered by an ASGI test transport. The
suite builds the clients itself, the way a user of each format would:

- native: a plain `httpx2` client on `POST /v1/run`;
- openai: the official `openai.AsyncOpenAI`, `model` set to the agent, a placeholder key, and only
  the base URL changed (its `http_client` is an `httpx2` client on the Unix socket);
- anthropic: the official `anthropic.AsyncAnthropic`, the same way;
- mcp: `fastmcp.Client` over streamable HTTP at `/v1/mcp`, calling the agent's one tool with the
  native body as its arguments.

`max_retries` stays at each SDK's default, except in the two tests about errors and conflicts,
where a retry would hide what the test looks for; each says so.

Two kinds of cell skip, each with its reason, never silently:

- MCP x stream: an MCP tool call answers once; FastMCP's OpenAPI tool buffers the HTTP answer.
- an engine not in `inprocess_engines` x `inprocess`: that lane imports a Python `handle` by path.

A binder subclasses the contract as `Test*` and provides:

- the ClassVars `engine_labels`, `lane_labels` (two or more), and `inprocess_engines`; optionally
  `expected_answers` (engine -> `ExpectedAnswer`) to pin each engine's answer;
- `chassis_for`, a class-scoped (or wider) fixture returning `chassis_for(target, lane)`: a
  context manager that yields a running `Chassis`. `target` is one of `engine_labels` or a handle
  path, `module:attribute`, which the suite uses for its own probe and error handles (`PROBE`,
  `ERROR_HANDLE`): `inprocess` loads the path, `sidecar` serves it with the workload template
  server on a Unix socket. On exit it stops what it started. The suite enters each
  `(target, lane)` once per class and shares it across tests (`ChassisPool`), so a cell costs a
  call, not a server start. The context manager is synchronous on purpose: every server runs in
  a thread of its own, so the suite needs no class-scoped event loop.
- optionally `logical`, the `Logical` request every engine cell sends.

`serve_on_unix_sockets` runs several ASGI apps (the public app and the proxy app, say) with
uvicorn on Unix sockets, on one event loop in one thread, for a binder's `chassis_for`.

Spec: docs/contracts/contract-v0.md and the PoC-3 open note, sections 2 to 9.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass, field
from typing import Any, ClassVar

import anthropic
import httpx2
import openai
import pytest
from chassis.fakes import InMemoryTelemetry
from chassis.server import ChassisConfig
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from chassis_contracts.inbound import Logical, sse

__all__ = [
    "ERROR_HANDLE",
    "INTERFACES",
    "LOGICAL",
    "MODES",
    "PROBE",
    "Answer",
    "Chassis",
    "ChassisFor",
    "ChassisPool",
    "ExpectedAnswer",
    "InterfaceContract",
    "UnixApp",
    "call",
    "native_body",
    "probe_handle",
    "serve_on_unix_sockets",
]

INTERFACES = ("native", "openai", "anthropic", "mcp")
MODES = ("stream", "complete")
REMINT_INTERFACES = ("openai", "anthropic", "mcp")
"""The interfaces that re-mint a trace id in use (section 6). Native keeps its 409 for a body
`trace_id` and is left out."""

BASE_URL = "http://chassis"
"""What every client names; over a Unix socket it only fills the `Host` header."""
MCP_PATH = "/v1/mcp"
PLACEHOLDER_KEY = "placeholder-not-a-key"
"""Both SDKs refuse an empty key. The chassis ignores it; it is not a secret."""
CALL_TIMEOUT_S = 30.0
"""suggested: one call of one cell; a cell that hangs fails instead."""
FINISH: Mapping[str, str | None] = {
    "native": None,
    "mcp": None,
    "openai": "stop",
    "anthropic": "end_turn",
}
"""The one stop reason each format sends for a normal answer (section 4)."""
ID_PREFIX: Mapping[str, str] = {"openai": "chatcmpl-", "anthropic": "msg_"}

MCP_STREAM_SKIP = (
    "MCP answers once: FastMCP's OpenAPI tool buffers the HTTP answer, so `stream` is read as "
    "false (open note, section 7)"
)

LOGICAL = Logical(
    "simplify: the quick brown fox",
    system="Answer in plain words.",
    history=(("user", "Hi."), ("assistant", "Hello. Ask me anything.")),
    max_tokens=300,
)
"""The logical request every engine cell sends (section 2): text, a system prompt, two turns of
history, and a token budget, written natively in each format."""


# --- The suite's own handles: wire form, plain dicts ---

PER_RUN_CTX = ("request_id", "trace_id", "idempotency_key", "traceparent")
"""The ctx keys that are new on every run; the probe leaves them out of what it echoes."""
HOLD_TEXT = "hold"
HOLD_S = 0.5
"""How long the probe holds a run whose text is `HOLD_TEXT`, so two calls overlap."""


async def probe_handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """Echo what the workload sees, `input` and `ctx` without the per-run ids, as one delta of
    sorted JSON. With the text `HOLD_TEXT`, hold the run `HOLD_S` first."""
    yield {"schema_version": "0", "type": "start", "request_id": ctx["request_id"]}
    if input.get("text") == HOLD_TEXT:
        await asyncio.sleep(HOLD_S)
    seen = {"input": input, "ctx": {k: v for k, v in ctx.items() if k not in PER_RUN_CTX}}
    yield {"schema_version": "0", "type": "delta", "text": json.dumps(seen, sort_keys=True)}
    yield {"schema_version": "0", "type": "metrics", "input_tokens": 1, "output_tokens": 1}
    yield {"schema_version": "0", "type": "end", "status": "ok"}


PROBE = f"{__name__}:probe_handle"
ERROR_HANDLE = "chassis_contracts.lane:error_handle"
"""One delta `partial`, then a retryable `model.overloaded` error."""
PARTIAL = "partial"
ERROR_CODE = "model.overloaded"


# --- The chassis a binder serves ---


@dataclass(frozen=True)
class Chassis:
    """One running chassis: its public app on `public_uds`, its telemetry, its config."""

    public_uds: str
    telemetry: InMemoryTelemetry
    config: ChassisConfig

    @property
    def agent(self) -> str:
        return self.config.agent.name


ChassisFor = Callable[[str, str], AbstractContextManager[Chassis]]
"""`chassis_for(target, lane)`: an engine label or a handle path, and a lane label."""


class ChassisPool:
    """Each `(target, lane)` entered once and kept until `close()`. A start that failed (or
    skipped) is raised again for every later cell, not retried."""

    def __init__(self, chassis_for: ChassisFor) -> None:
        self._for = chassis_for
        self._stack = ExitStack()
        self._open: dict[tuple[str, str], Chassis] = {}
        self._failed: dict[tuple[str, str], BaseException] = {}

    def get(self, target: str, lane: str) -> Chassis:
        key = (target, lane)
        if key in self._failed:
            raise self._failed[key]
        if key not in self._open:
            try:
                self._open[key] = self._stack.enter_context(self._for(target, lane))
            except BaseException as exc:
                self._failed[key] = exc
                raise
        return self._open[key]

    def close(self) -> None:
        self._stack.close()


@dataclass(frozen=True)
class UnixApp:
    app: Any
    uds: str
    lifespan: bool = True
    """False for an app that shares another's state and has no lifespan (the proxy app)."""


@contextmanager
def serve_on_unix_sockets(apps: Sequence[UnixApp], start_timeout_s: float = 20.0) -> Iterator[None]:
    """Serve each app with uvicorn on its Unix socket, all on one event loop in one thread, so
    apps that share state (the public app and its proxy app) share a loop too. Returns once every
    server listens; raises when one ends before that (a failed lifespan, say). Stops them all on
    exit.
    """
    import uvicorn

    servers = [
        uvicorn.Server(
            uvicorn.Config(
                a.app,
                uds=a.uds,
                log_level="warning",
                lifespan="on" if a.lifespan else "off",
                timeout_graceful_shutdown=2,
            )
        )
        for a in apps
    ]
    ended: list[str] = []
    failure: list[BaseException] = []

    async def one(server: uvicorn.Server, uds: str) -> None:
        try:
            await server.serve()
        finally:
            ended.append(uds)

    async def main() -> None:
        await asyncio.gather(*(one(s, a.uds) for s, a in zip(servers, apps, strict=True)))

    def run() -> None:
        try:
            asyncio.run(main())
        except BaseException as exc:
            failure.append(exc)

    thread = threading.Thread(target=run, daemon=True, name="uvicorn-uds")
    thread.start()
    try:
        deadline = time.monotonic() + start_timeout_s
        while not all(s.started for s in servers):
            if ended or failure or not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError(
                    f"uvicorn did not start on {[a.uds for a in apps]}"
                    f" (ended: {ended}, error: {failure!r})"
                )
            time.sleep(0.02)
        yield
    finally:
        for server in servers:
            server.should_exit = True
        thread.join(timeout=15)


# --- The four clients ---


@dataclass(frozen=True)
class ExpectedAnswer:
    """What one engine answers `logical`, in every interface."""

    text: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class Answer:
    """One answer as its client reads it."""

    text: str
    input_tokens: int
    output_tokens: int
    request_id: str
    trace_id: str
    finish: str | None
    """The format's stop reason; None for native and MCP."""
    run_status: str | None
    """The run's status where the format reports it (the envelope, `x-chassis-status`)."""
    reply_id: str | None = None
    """The format's own id (`chatcmpl-...`, `msg_...`); None for native and MCP."""

    @property
    def usage(self) -> tuple[int, int]:
        return self.input_tokens, self.output_tokens


def answer_text(output: Any) -> str:
    """Section 3: `output.text` when it is a string, else the output as compact, sorted JSON."""
    if isinstance(output, dict) and isinstance(output.get("text"), str):
        return str(output["text"])
    return json.dumps(output, separators=(",", ":"), sort_keys=True)


def native_body(logical: Logical) -> dict[str, Any]:
    """The logical request as the native `/v1/run` body (and the MCP tool's arguments)."""
    data: dict[str, Any] = {}
    if logical.system is not None:
        data["system"] = logical.system
    if logical.history:
        data["history"] = [{"role": role, "text": text} for role, text in logical.history]
    return {
        "input": {"text": logical.text, "data": data},
        "budget": {"max_tokens": logical.max_tokens},
    }


def canonical_input(logical: Logical) -> dict[str, Any]:
    """The `input` every workload must receive for `logical`, whatever the interface."""
    return dict(native_body(logical)["input"])


def openai_messages(logical: Logical) -> list[Any]:
    messages: list[Any] = []
    if logical.system is not None:
        messages.append({"role": "system", "content": logical.system})
    messages.extend({"role": role, "content": text} for role, text in logical.history)
    messages.append({"role": "user", "content": logical.text})
    return messages


def anthropic_messages(logical: Logical) -> list[Any]:
    history: list[Any] = [{"role": role, "content": text} for role, text in logical.history]
    return [*history, {"role": "user", "content": logical.text}]


def _transport(uds: str) -> httpx2.AsyncHTTPTransport:
    return httpx2.AsyncHTTPTransport(uds=uds)


@dataclass
class _Seen:
    """The headers of each response a client got (an `httpx2` response hook)."""

    headers: list[httpx2.Headers] = field(default_factory=list)

    async def __call__(self, response: httpx2.Response) -> None:
        self.headers.append(response.headers)

    def last(self, name: str) -> str:
        assert self.headers, "the client got no response"
        value = self.headers[-1].get(name)
        assert value, f"the response carries no {name} header: {dict(self.headers[-1])}"
        return str(value)


def _sdk_http(uds: str, seen: _Seen) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=_transport(uds), timeout=CALL_TIMEOUT_S, event_hooks={"response": [seen]}
    )


def _retries(max_retries: int | None) -> dict[str, Any]:
    return {} if max_retries is None else {"max_retries": max_retries}


def openai_client(uds: str, seen: _Seen, max_retries: int | None = None) -> openai.AsyncOpenAI:
    """The official client; only the base URL differs from talking to OpenAI."""
    return openai.AsyncOpenAI(
        base_url=f"{BASE_URL}/v1",
        api_key=PLACEHOLDER_KEY,
        http_client=_sdk_http(uds, seen),
        **_retries(max_retries),
    )


def anthropic_client(
    uds: str, seen: _Seen, max_retries: int | None = None
) -> anthropic.AsyncAnthropic:
    """The official client; only the base URL differs from talking to Anthropic."""
    return anthropic.AsyncAnthropic(
        base_url=BASE_URL,
        api_key=PLACEHOLDER_KEY,
        http_client=_sdk_http(uds, seen),
        **_retries(max_retries),
    )


def mcp_client(uds: str, headers: Mapping[str, str] | None = None) -> Client[Any]:
    """`fastmcp.Client` over streamable HTTP at `/v1/mcp`, on the Unix socket."""

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx2.Timeout | None = None,
        auth: httpx2.Auth | None = None,
        **kw: Any,
    ) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=_transport(uds), headers=headers, timeout=timeout, auth=auth, **kw
        )

    transport = StreamableHttpTransport(
        f"{BASE_URL}{MCP_PATH}", headers=dict(headers or {}), httpx_client_factory=factory
    )
    return Client(transport)


def _from_envelope(envelope: Mapping[str, Any], text: str | None = None) -> Answer:
    metrics = envelope.get("metrics") or {}
    return Answer(
        text=answer_text(envelope.get("output")) if text is None else text,
        input_tokens=int(metrics.get("input_tokens") or 0),
        output_tokens=int(metrics.get("output_tokens") or 0),
        request_id=str(envelope["request_id"]),
        trace_id=str(envelope["trace_id"]),
        finish=None,
        run_status=str(envelope["status"]),
    )


async def _native(
    chassis: Chassis, logical: Logical, stream: bool, headers: Mapping[str, str]
) -> Answer:
    body = {**native_body(logical), "stream": stream}
    async with httpx2.AsyncClient(
        transport=_transport(chassis.public_uds), base_url=BASE_URL, timeout=CALL_TIMEOUT_S
    ) as http:
        if not stream:
            response = await http.post("/v1/run", json=body, headers=dict(headers))
            assert response.status_code == 200, (response.status_code, response.text)
            return _from_envelope(response.json())
        async with http.stream("POST", "/v1/run", json=body, headers=dict(headers)) as response:
            assert response.status_code == 200, (response.status_code, await response.aread())
            assert response.headers["content-type"].startswith("text/event-stream")
            frames = [chunk async for chunk in response.aiter_text()]
    events = sse(frames)
    assert events and events[-1].event == "response", [e.event for e in events]
    envelope = json.loads(events[-1].data)
    text = "".join(json.loads(e.data)["text"] for e in events if e.event == "delta")
    return _from_envelope(envelope, text)


async def _openai(
    chassis: Chassis,
    logical: Logical,
    stream: bool,
    headers: Mapping[str, str],
    max_retries: int | None,
    sink: list[str],
) -> Answer:
    seen = _Seen()
    async with openai_client(chassis.public_uds, seen, max_retries) as client:
        create: Any = client.chat.completions.create
        kwargs: dict[str, Any] = {
            "model": chassis.agent,
            "messages": openai_messages(logical),
            "max_completion_tokens": logical.max_tokens,
            "extra_headers": dict(headers),
        }
        if not stream:
            completion = await create(**kwargs)
            (choice,) = completion.choices
            assert completion.usage is not None
            sink.append(choice.message.content or "")
            return Answer(
                text=choice.message.content or "",
                input_tokens=completion.usage.prompt_tokens,
                output_tokens=completion.usage.completion_tokens,
                request_id=seen.last("x-request-id"),
                trace_id=seen.last("x-trace-id"),
                finish=choice.finish_reason,
                run_status=seen.headers[-1].get("x-chassis-status"),
                reply_id=completion.id,
            )
        chunks = await create(**kwargs, stream=True)
        usage: Any = None
        finish: str | None = None
        ids: set[str] = set()
        async for chunk in chunks:
            ids.add(chunk.id)
            for part in chunk.choices:
                sink.append(part.delta.content or "")
                finish = part.finish_reason or finish
            if chunk.usage is not None:
                usage = chunk.usage
    assert len(ids) == 1, f"the chunks carry more than one id: {ids}"
    assert usage is not None, "no chunk carried usage"
    return Answer(
        text="".join(sink),
        input_tokens=usage.prompt_tokens,
        output_tokens=usage.completion_tokens,
        request_id=seen.last("x-request-id"),
        trace_id=seen.last("x-trace-id"),
        finish=finish,
        run_status=None,
        reply_id=ids.pop(),
    )


async def _anthropic(
    chassis: Chassis,
    logical: Logical,
    stream: bool,
    headers: Mapping[str, str],
    max_retries: int | None,
    sink: list[str],
) -> Answer:
    seen = _Seen()
    async with anthropic_client(chassis.public_uds, seen, max_retries) as client:
        create: Any = client.messages.create
        kwargs: dict[str, Any] = {
            "model": chassis.agent,
            "max_tokens": logical.max_tokens,
            "messages": anthropic_messages(logical),
            "extra_headers": dict(headers),
        }
        if logical.system is not None:
            kwargs["system"] = logical.system
        if not stream:
            message = await create(**kwargs)
            text = "".join(block.text for block in message.content if block.type == "text")
            sink.append(text)
            return Answer(
                text=text,
                input_tokens=message.usage.input_tokens,
                output_tokens=message.usage.output_tokens,
                request_id=seen.last("x-request-id"),
                trace_id=seen.last("x-trace-id"),
                finish=message.stop_reason,
                run_status=seen.headers[-1].get("x-chassis-status"),
                reply_id=message.id,
            )
        events = await create(**kwargs, stream=True)
        types: list[str] = []
        reply_id: str | None = None
        tokens_in = tokens_out = 0
        finish: str | None = None
        async for event in events:
            types.append(event.type)
            if event.type == "message_start":
                reply_id = event.message.id
                tokens_in = event.message.usage.input_tokens
                tokens_out = event.message.usage.output_tokens
            elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                sink.append(event.delta.text)
            elif event.type == "message_delta":
                finish = event.delta.stop_reason
                if event.usage.input_tokens is not None:
                    tokens_in = event.usage.input_tokens
                tokens_out = event.usage.output_tokens
    assert types[0] == "message_start" and types[-1] == "message_stop", types
    return Answer(
        text="".join(sink),
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        request_id=seen.last("x-request-id"),
        trace_id=seen.last("x-trace-id"),
        finish=finish,
        run_status=None,
        reply_id=reply_id,
    )


async def _mcp(chassis: Chassis, logical: Logical, headers: Mapping[str, str]) -> Answer:
    async with mcp_client(chassis.public_uds, headers) as client:
        result = await client.call_tool(chassis.agent, native_body(logical), raise_on_error=False)
    assert result.is_error is False, result
    assert isinstance(result.structured_content, dict), "the tool result is not the envelope"
    return _from_envelope(result.structured_content)


async def call(
    chassis: Chassis,
    interface: str,
    logical: Logical,
    *,
    stream: bool,
    headers: Mapping[str, str] | None = None,
    max_retries: int | None = None,
    sink: list[str] | None = None,
) -> Answer:
    """One call of `logical` through `interface`'s own client. `headers` are sent as is (a
    `traceparent`, say). `sink` collects the text as it arrives, so a caller still sees what
    streamed before an SDK raised. An SDK error is raised as the SDK raises it."""
    headers = headers or {}
    got: list[str] = [] if sink is None else sink
    async with asyncio.timeout(CALL_TIMEOUT_S):
        if interface == "native":
            return await _native(chassis, logical, stream, headers)
        if interface == "openai":
            return await _openai(chassis, logical, stream, headers, max_retries, got)
        if interface == "anthropic":
            return await _anthropic(chassis, logical, stream, headers, max_retries, got)
        if interface == "mcp":
            assert not stream, MCP_STREAM_SKIP
            return await _mcp(chassis, logical, headers)
    raise ValueError(f"unknown interface {interface!r}; one of {INTERFACES}")


async def _get_json(chassis: Chassis, path: str) -> Any:
    async with httpx2.AsyncClient(
        transport=_transport(chassis.public_uds), base_url=BASE_URL, timeout=CALL_TIMEOUT_S
    ) as http:
        response = await http.get(path)
    assert response.status_code == 200, (path, response.status_code, response.text)
    return response.json()


def _resolve(node: Any, components: Mapping[str, Any]) -> Any:
    """`node` with every `#/components/schemas/<name>` reference replaced by that schema."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            return _resolve(components[ref.rsplit("/", 1)[1]], components)
        return {k: _resolve(v, components) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(v, components) for v in node]
    return node


def _uncorrelated(telemetry: InMemoryTelemetry) -> int:
    return sum(
        n
        for (name, _), n in telemetry.counters.items()
        if name == "chassis.model_calls_uncorrelated"
    )


HTTP_OPERATIONS: Mapping[str, tuple[str, str, str]] = {
    "native": ("post", "/v1/run", "run"),
    "openai": ("post", "/v1/chat/completions", "chat_completions"),
    "anthropic": ("post", "/v1/messages", "messages"),
}
"""Each HTTP interface: method, path, and operation id (sections 5, 8, and 11)."""
INTERFACE_KEY = "x-chassis-interface"


@pytest.mark.contract
class InterfaceContract:
    """Subclass as `Test*`: set the ClassVars and provide `chassis_for` (module docstring)."""

    engine_labels: ClassVar[tuple[str, ...]] = ()
    lane_labels: ClassVar[tuple[str, ...]] = ()
    inprocess_engines: ClassVar[tuple[str, ...]] = ()
    """The engines `inprocess` can run (Python handles); the rest skip in that lane."""
    expected_answers: ClassVar[Mapping[str, ExpectedAnswer]] = {}
    """Optional: each engine's answer to `logical`, checked in every cell that runs it."""

    def pytest_generate_tests(self, metafunc: pytest.Metafunc) -> None:
        cls = type(self)
        names = metafunc.fixturenames
        if "interface" in names:
            metafunc.parametrize("interface", INTERFACES, ids=INTERFACES)
        if "remint_interface" in names:
            metafunc.parametrize("remint_interface", REMINT_INTERFACES, ids=REMINT_INTERFACES)
        if "engine" in names:
            if not cls.engine_labels:
                raise TypeError(f"{cls.__name__}: set engine_labels")
            metafunc.parametrize("engine", cls.engine_labels, ids=cls.engine_labels)
        if "mode" in names:
            metafunc.parametrize("mode", MODES, ids=MODES)
        if "lane" in names:
            if len(cls.lane_labels) < 2:
                raise TypeError(
                    f"{cls.__name__}: set lane_labels to two or more lanes; the matrix covers "
                    "both transports"
                )
            metafunc.parametrize("lane", cls.lane_labels, ids=cls.lane_labels)

    # --- Fixtures ---

    @pytest.fixture(scope="class")
    @classmethod
    def chassis_for(cls) -> ChassisFor:
        raise NotImplementedError(
            "provide a class-scoped chassis_for fixture (a classmethod): "
            "chassis_for(target, lane) -> a context manager yielding a running Chassis"
        )

    @pytest.fixture(scope="class")
    @classmethod
    def chassis(cls, chassis_for: ChassisFor) -> Iterator[ChassisPool]:
        pool = ChassisPool(chassis_for)
        try:
            yield pool
        finally:
            pool.close()

    @pytest.fixture
    def logical(self) -> Logical:
        return LOGICAL

    # --- Helpers ---

    def _skip_cell(self, *, engine: str, lane: str, interface: str = "", mode: str = "") -> None:
        if interface == "mcp" and mode == "stream":
            pytest.skip(MCP_STREAM_SKIP)
        if lane == "inprocess" and engine not in self.inprocess_engines:
            pytest.skip(f"inprocess imports a Python handle by path; {engine} has none")

    def _check_answer(self, answer: Answer, interface: str, engine: str | None, where: str) -> None:
        assert answer.text, f"{where}: no answer text"
        assert answer.finish == FINISH[interface], f"{where}: finish {answer.finish!r}"
        assert answer.run_status in (None, "ok"), f"{where}: run status {answer.run_status!r}"
        assert answer.request_id and answer.trace_id, where
        if interface in ID_PREFIX:
            want = f"{ID_PREFIX[interface]}{answer.request_id}"
            assert answer.reply_id == want, f"{where}: id {answer.reply_id!r}, want {want!r}"
        expected = self.expected_answers.get(engine) if engine else None
        if expected is not None:
            assert answer.text == expected.text, f"{where}: text {answer.text!r}"
            assert answer.usage == (expected.input_tokens, expected.output_tokens), (
                f"{where}: usage {answer.usage}"
            )

    # --- The matrix ---

    async def test_every_cell_answers(
        self,
        chassis: ChassisPool,
        logical: Logical,
        interface: str,
        engine: str,
        mode: str,
        lane: str,
    ) -> None:
        """Exit criterion 2: every interface x engine x mode x lane answers the logical request,
        read by that format's own client: the text, the usage, the one finish reason, the ids."""
        self._skip_cell(engine=engine, lane=lane, interface=interface, mode=mode)
        target = chassis.get(engine, lane)
        answer = await call(target, interface, logical, stream=mode == "stream")
        self._check_answer(answer, interface, engine, f"{interface}-{engine}-{mode}-{lane}")

    async def test_stream_and_complete_agree(
        self, chassis: ChassisPool, logical: Logical, interface: str, engine: str, lane: str
    ) -> None:
        """The streamed answer joins to the complete one: same text, usage, and finish."""
        self._skip_cell(engine=engine, lane=lane, interface=interface, mode="stream")
        target = chassis.get(engine, lane)
        complete = await call(target, interface, logical, stream=False)
        streamed = await call(target, interface, logical, stream=True)
        assert streamed.text == complete.text
        assert streamed.usage == complete.usage
        assert streamed.finish == complete.finish == FINISH[interface]
        assert streamed.request_id != complete.request_id, "two runs share a request id"

    async def test_every_interface_gives_the_same_answer(
        self, chassis: ChassisPool, logical: Logical, engine: str, lane: str
    ) -> None:
        """The same logical request through all four interfaces gives the same answer text and
        the same usage."""
        self._skip_cell(engine=engine, lane=lane)
        target = chassis.get(engine, lane)
        answers = {i: await call(target, i, logical, stream=False) for i in INTERFACES}
        texts = {i: a.text for i, a in answers.items()}
        usages = {i: a.usage for i, a in answers.items()}
        assert len(set(texts.values())) == 1, f"the interfaces disagree on the text: {texts}"
        assert len(set(usages.values())) == 1, f"the interfaces disagree on the usage: {usages}"

    async def test_model_calls_are_charged_to_the_run(
        self, chassis: ChassisPool, logical: Logical, interface: str, engine: str, lane: str
    ) -> None:
        """Every model call the engine makes is a `chassis.model.call` span correlated to the run
        the interface opened (its request id), the `chassis.run` span is labeled with the
        interface, and no model call is served uncorrelated."""
        self._skip_cell(engine=engine, lane=lane)
        target = chassis.get(engine, lane)
        telemetry = target.telemetry
        uncorrelated = _uncorrelated(telemetry)
        answer = await call(target, interface, logical, stream=False)
        calls = [
            s
            for s in telemetry.spans
            if s.name == "chassis.model.call"
            and s.attributes.get("request_id") == answer.request_id
        ]
        assert calls, f"no model call was charged to run {answer.request_id}"
        assert all(s.attributes.get("correlated") is True for s in calls), calls
        runs = [
            s
            for s in telemetry.spans
            if s.name == "chassis.run" and s.attributes.get("request_id") == answer.request_id
        ]
        assert [s.attributes.get("interface") for s in runs] == [interface], runs
        assert _uncorrelated(telemetry) == uncorrelated == 0, "a model call was not correlated"

    async def test_the_workload_cannot_tell_the_interface(
        self, chassis: ChassisPool, logical: Logical, interface: str, lane: str
    ) -> None:
        """011 H-2: the probe handle sees the same `input` and `ctx` (per-run ids aside) through
        every interface as through native, and `input` is the canonical one."""
        target = chassis.get(PROBE, lane)
        native = json.loads((await call(target, "native", logical, stream=False)).text)
        seen = json.loads((await call(target, interface, logical, stream=False)).text)
        assert seen["input"] == canonical_input(logical), seen["input"]
        assert seen == native, f"{interface} vs native: {seen} != {native}"

    async def test_one_traceparent_twice_at_once_never_conflicts(
        self, chassis: ChassisPool, remint_interface: str, lane: str
    ) -> None:
        """Section 6: two overlapping calls with one `traceparent` both answer; the second run
        gets a fresh trace id (`chassis.trace_id_reminted`), never a 409. `max_retries=0`: an
        SDK retries a 409, which would hide one."""
        target = chassis.get(PROBE, lane)
        trace = uuid.uuid4().hex
        headers = {"traceparent": f"00-{trace}-{'b' * 16}-01"}
        hold = Logical(HOLD_TEXT, max_tokens=100)
        counter = ("chassis.trace_id_reminted", (("interface", remint_interface),))
        before = target.telemetry.counters.get(counter, 0)
        first, second = await asyncio.gather(
            *(
                call(target, remint_interface, hold, stream=False, headers=headers, max_retries=0)
                for _ in range(2)
            )
        )
        assert {first.trace_id, second.trace_id} >= {trace}, (first.trace_id, second.trace_id)
        assert first.trace_id != second.trace_id, "two in-flight runs share a trace id"
        assert target.telemetry.counters.get(counter, 0) == before + 1

    async def test_an_error_event_is_the_formats_error(
        self, chassis: ChassisPool, interface: str, mode: str, lane: str
    ) -> None:
        """Section 3: the run's `error` (after one delta) reaches each client in its format's
        shape: an SDK error (HTTP in complete mode, an error frame mid-stream, with the text that
        streamed first), or a 200 envelope with `status: error` (native and MCP, the known MCP
        gap). `max_retries=0`: the error is retryable, and the SDK would retry it."""
        if interface == "mcp" and mode == "stream":
            pytest.skip(MCP_STREAM_SKIP)
        target = chassis.get(ERROR_HANDLE, lane)
        stream = mode == "stream"
        logical = Logical("x", max_tokens=100)
        if interface in ("native", "mcp"):
            answer = await call(target, interface, logical, stream=stream)
            assert answer.run_status == "error", answer
            assert answer.text == PARTIAL
            return
        sink: list[str] = []
        errors: dict[str, type[Exception]] = {
            "openai": openai.APIError,
            "anthropic": anthropic.APIError,
        }
        with pytest.raises(errors[interface]) as info:
            await call(target, interface, logical, stream=stream, max_retries=0, sink=sink)
        error: Any = info.value
        assert ERROR_CODE in str(error.body) + str(error), error
        if stream:
            assert "".join(sink) == PARTIAL, "the text before the error did not stream"
            return
        assert sink == [], "an HTTP error carries no answer"
        assert error.status_code == 503, error.status_code
        assert error.response.headers.get("x-should-retry") == "true"

    # --- MCP, OpenAPI, and the manifest ---

    async def test_mcp_lists_one_tool_named_after_the_agent(
        self, chassis: ChassisPool, lane: str
    ) -> None:
        """Exit criterion 4: an MCP client lists exactly one tool, the agent."""
        target = chassis.get(PROBE, lane)
        async with mcp_client(target.public_uds) as client:
            tools = await client.list_tools()
        assert [t.name for t in tools] == [target.agent]

    async def test_mcp_tool_schema_is_the_openapi_request_schema(
        self, chassis: ChassisPool
    ) -> None:
        """Exit criterion 5: the tool's input schema is the `/v1/run` body schema of the served
        OpenAPI spec; FastMCP drops only the body's own `title`, `description`, and
        `additionalProperties`."""
        target = chassis.get(PROBE, self.lane_labels[0])
        spec = await _get_json(target, "/openapi.json")
        async with mcp_client(target.public_uds) as client:
            (tool,) = await client.list_tools()
        content = spec["paths"]["/v1/run"]["post"]["requestBody"]["content"]["application/json"]
        body = _resolve(content["schema"], spec["components"]["schemas"])
        schema = _resolve(tool.input_schema, tool.input_schema.get("$defs", {}))
        assert schema["type"] == body["type"] == "object"
        assert schema["properties"] == body["properties"]
        assert schema["required"] == body["required"]
        assert set(body) - set(schema) <= {"title", "description", "additionalProperties"}

    async def test_openapi_is_3_1_and_names_every_interface(self, chassis: ChassisPool) -> None:
        """The spec is OpenAPI 3.1 and names each HTTP interface by its operation and the
        `x-chassis-interface` extension; `/v1/mcp` is not in it (the manifest lists MCP)."""
        target = chassis.get(PROBE, self.lane_labels[0])
        spec = await _get_json(target, "/openapi.json")
        assert str(spec["openapi"]).startswith("3.1"), spec["openapi"]
        for name, (method, path, operation_id) in HTTP_OPERATIONS.items():
            operation = spec["paths"].get(path, {}).get(method)
            assert operation is not None, f"{name}: {method.upper()} {path} is not in the spec"
            assert operation.get("operationId") == operation_id, (
                name,
                operation.get("operationId"),
            )
            assert operation.get(INTERFACE_KEY) == name, (name, operation.get(INTERFACE_KEY))
            assert "text/event-stream" in operation["responses"]["200"]["content"], name
        assert MCP_PATH not in spec["paths"]

    async def test_manifest_matches_config_and_openapi(self, chassis: ChassisPool) -> None:
        """Section 8, in every lane: `/manifest` names the configured agent, versions, and lane;
        lists the four interfaces as the spec and the MCP server give them; and hashes the spec
        it serves."""
        for lane in self.lane_labels:
            target = chassis.get(PROBE, lane)
            config = target.config
            manifest = await _get_json(target, "/manifest")
            spec = await _get_json(target, "/openapi.json")
            assert manifest["agent"] == {
                "name": config.agent.name,
                "version": config.agent.version,
                "trust": config.spec.trust,
            }, (lane, manifest["agent"])
            versions = manifest["versions"]
            assert versions["config"] == config.version, (lane, versions)
            assert versions["prompt"] == config.spec.prompt.version, (lane, versions)
            assert versions["model_route"] == config.spec.model.route, (lane, versions)
            assert manifest["lane"] == config.spec.engine.connector == lane
            by_name = {i["name"]: i for i in manifest["interfaces"]}
            assert sorted(by_name) == sorted(INTERFACES), (lane, sorted(by_name))
            for name, (method, path, operation_id) in HTTP_OPERATIONS.items():
                entry = by_name[name]
                assert (entry["method"], entry["path"], entry["operation_id"]) == (
                    method.upper(),
                    path,
                    operation_id,
                ), (lane, entry)
                assert entry["streaming"] is True, (lane, entry)
            mcp = by_name["mcp"]
            assert (mcp["path"], mcp["streaming"], mcp["tools"]) == (
                MCP_PATH,
                False,
                [config.agent.name],
            ), (lane, mcp)
            raw = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
            assert manifest["openapi"]["sha256"] == hashlib.sha256(raw).hexdigest(), lane
            assert manifest["openapi"]["version"] == spec["openapi"], lane
