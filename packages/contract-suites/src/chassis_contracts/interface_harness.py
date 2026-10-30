"""The serving and client harness behind `InterfaceContract` (`chassis_contracts.interface`).

`serve_on_unix_sockets` runs ASGI apps with uvicorn on Unix sockets; `Chassis` and `ChassisPool`
hold what a binder serves; `call` makes one call through one interface's own client (`httpx2`,
`openai`, `anthropic`, or `fastmcp`). `chassis_contracts.interface` re-exports every public name
here, so import from there.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass, field
from typing import Any

import anthropic
import httpx2
import openai
from chassis.fakes import InMemoryTelemetry
from chassis.server import ChassisConfig
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from chassis_contracts.inbound import Logical, sse
from chassis_contracts.serving import serve_in_thread

INTERFACES = ("native", "openai", "anthropic", "mcp")

BASE_URL = "http://chassis"
"""What every client names; over a Unix socket it only fills the `Host` header."""
MCP_PATH = "/v1/mcp"
PLACEHOLDER_KEY = "placeholder-not-a-key"
"""Both SDKs refuse an empty key. The chassis ignores it; it is not a secret."""
CALL_TIMEOUT_S = 30.0
"""suggested: one call of one cell; a cell that hangs fails instead."""

MCP_STREAM_SKIP = (
    "MCP answers once: FastMCP's OpenAPI tool buffers the HTTP answer, so `stream` is read as "
    "false (open note, section 7)"
)


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
    exit. The thread runner is `chassis_contracts.serving.serve_in_thread`.
    """
    import uvicorn

    configs = [
        uvicorn.Config(
            a.app,
            uds=a.uds,
            log_level="warning",
            lifespan="on" if a.lifespan else "off",
            timeout_graceful_shutdown=2,
        )
        for a in apps
    ]
    with serve_in_thread(configs, start_timeout_s, name="uvicorn-uds"):
        yield


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
