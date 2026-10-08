"""Support for this folder's exit-criteria and Schemathesis tests. Not a test module.

`test_openapi_props.py` and `test_exit_criteria.py` import it. The interface contract binding
(`test_interfaces.py`) has its own harness (`poc03_harness.py`); nothing here is shared with it.

Two kinds of app, both offline:

- `fake_engine_app()`: the fake config (`packages/chassis/configs/fake.yaml`) with a `FakeEngine`
  running `echo`, every interface on. What Schemathesis tests: the spec and the HTTP edges.
- `lane_app(handle)`: the same config with no injected ports, so the profile builds them and the
  run goes through the real `inprocess` lane (A2A in memory) to a wire-form `handle`. What the
  client scenarios use.

`serve_on_uds(app)` puts an app on uvicorn over a Unix socket (lifespan on), so the SDKs and the MCP
client talk real HTTP and real streaming with no TCP. The offline gate allows `AF_UNIX` only.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx2
import uvicorn
import yaml
from chassis.core.envelope import Context, TaskInput
from chassis.core.events import Delta, End, Event, Metrics, Start, ToolCall
from chassis.core.handle import echo, wire
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app, load_config
from chassis.server.interfaces.mcp import MCP_PATH
from fastapi import FastAPI
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

ROOT = Path(__file__).resolve().parents[3]
POC = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
FAKE_CONFIG = ROOT / "packages/chassis/configs/fake.yaml"
GAPS_NOTE = POC / "notes/2026-10-01-format-gaps.md"

AGENT = "echo"
"""The agent name in `fake.yaml`. It is the `model` every SDK call names and the MCP tool name."""
ECHO_WIRE = "chassis.core.handle:echo_wire"
"""Streams the input text back, one word per delta."""
TOOL_CALLING_WIRE = "poc03_support:tool_calling_wire"
"""A workload that makes its own tool call before it answers (see `tool_calling`)."""
BASE = "http://chassis"
"""Over a Unix socket the host only fills the `Host` header."""
TIMEOUT_S = 30.0
"""Every async scenario runs under this deadline, so a half-built feature fails, not hangs."""


# --- config and apps ---------------------------------------------------------------------------


def fake_config(handle: str | None = None) -> ChassisConfig:
    """`fake.yaml` as is, or with `spec.engine.handle` replaced. `spec.interfaces` is left unset,
    so every interface is on by its default.
    """
    data = yaml.safe_load(FAKE_CONFIG.read_text())
    if handle is not None:
        data["spec"]["engine"]["handle"] = handle
    return load_config(data)


def fake_engine_app() -> FastAPI:
    """The fake config served by a `FakeEngine` running `echo`: no lane, no workload import."""
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    return create_app(load_config(FAKE_CONFIG), ports)


def lane_app(handle: str = ECHO_WIRE) -> FastAPI:
    """The fake profile builds every port; the engine is the `inprocess` lane over `handle`."""
    config = fake_config(handle)
    assert config.spec.engine.connector == "inprocess"
    return create_app(config)


# --- a workload that calls its own tool --------------------------------------------------------

TOOL_CALL = {"call_id": "call-1", "name": "glossary_lookup", "arguments": {"term": "API"}}
TOOL_ANSWER = "looked it up"


async def tool_calling(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
    """Calls one of the agent's own tools (a `tool_call` event), then answers in text."""
    yield Start(request_id=ctx.request_id)
    yield ToolCall(**TOOL_CALL, result={"definition": "application programming interface"})
    yield Delta(text=TOOL_ANSWER)
    yield Metrics(input_tokens=3, output_tokens=3, model_route=ctx.model_route)
    yield End(status="ok")


tool_calling_wire = wire(tool_calling)


# --- serving and clients -----------------------------------------------------------------------


@asynccontextmanager
async def serve_on_uds(app: Any) -> AsyncIterator[str]:
    """Serve `app` on uvicorn over a fresh Unix socket, lifespan on, in this event loop; yields
    the socket path. The folder is short on purpose: macOS caps a socket path at 104 bytes.
    """
    folder = Path(tempfile.mkdtemp(prefix="poc03-"))
    path = str(folder / "public.sock")
    server = uvicorn.Server(uvicorn.Config(app, uds=path, log_level="warning", lifespan="on"))
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    task.result()
                    raise RuntimeError("uvicorn exited before it started")
                await asyncio.sleep(0.01)
        yield path
    finally:
        server.should_exit = True
        await task
        shutil.rmtree(folder, ignore_errors=True)


def uds_http_client(uds: str, **kwargs: Any) -> httpx2.AsyncClient:
    """An `httpx2` client over the Unix socket: the HTTP line both SDKs and FastMCP run on."""
    return httpx2.AsyncClient(transport=httpx2.AsyncHTTPTransport(uds=uds), **kwargs)


def mcp_client(uds: str) -> Client[Any]:
    """A real `fastmcp.Client` over streamable HTTP to `/v1/mcp`, through the Unix socket."""

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx2.Timeout | None = None,
        auth: httpx2.Auth | None = None,
        **kw: Any,
    ) -> httpx2.AsyncClient:
        return uds_http_client(uds, headers=headers, timeout=timeout, auth=auth, **kw)

    return Client(StreamableHttpTransport(f"{BASE}{MCP_PATH}", httpx_client_factory=factory))


# --- the OpenAPI spec --------------------------------------------------------------------------


def resolve_refs(node: Any, components: Mapping[str, Any]) -> Any:
    """`node` with every `#/components/schemas/<name>` reference replaced by that schema."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            return resolve_refs(components[ref.rsplit("/", 1)[1]], components)
        return {k: resolve_refs(v, components) for k, v in node.items()}
    if isinstance(node, list):
        return [resolve_refs(v, components) for v in node]
    return node


def run_body_schema(spec: Mapping[str, Any]) -> dict[str, Any]:
    """The `POST /v1/run` request body schema from `spec`, references resolved."""
    body = spec["paths"]["/v1/run"]["post"]["requestBody"]["content"]["application/json"]
    resolved = resolve_refs(body["schema"], spec["components"]["schemas"])
    assert isinstance(resolved, dict)
    return resolved


def operations(spec: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """`(METHOD, path) -> operation` for every operation in `spec`."""
    methods = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
    return {
        (method.upper(), path): op
        for path, item in spec["paths"].items()
        for method, op in item.items()
        if method in methods
    }


# --- SSE and markdown --------------------------------------------------------------------------


def sse_data(text: str) -> list[Any]:
    """The `data:` payloads of an SSE body, JSON-decoded, `[DONE]` kept as the string."""
    out: list[Any] = []
    for block in text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                raw = line[len("data: ") :]
                out.append(raw if raw == "[DONE]" else json.loads(raw))
    return out


def markdown_tables(text: str) -> list[list[dict[str, str]]]:
    """Every pipe table in `text`, each as a list of rows keyed by the header cells."""
    tables: list[list[dict[str, str]]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines) - 1:
        head, rule = lines[i].strip(), lines[i + 1].strip()
        if head.startswith("|") and rule.startswith("|") and set(rule) <= set("|-: "):
            names = _cells(head)
            rows: list[dict[str, str]] = []
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = (_cells(lines[i].strip()) + [""] * len(names))[: len(names)]
                rows.append(dict(zip(names, cells, strict=True)))
                i += 1
            tables.append(rows)
        else:
            i += 1
    return tables


def _cells(line: str) -> list[str]:
    """The cells of one table line. A `\\|` inside a cell (escaped pipe) is kept as `|`."""
    inner = line.strip().strip("|").replace("\\|", "\x00")
    return [cell.strip().replace("\x00", "|") for cell in inner.split("|")]
