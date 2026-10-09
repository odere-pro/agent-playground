"""The REAL bundled Claude Code CLI, through `handle`, against a local chassis model proxy.

The proxy serves `POST /v1/messages` (the chassis route, PoC-6 B1) over the fake model with the
bake-off script, and an MCP stub serves the two read-only tools. `handle` runs in a child Python
process started with `env -i` and an explicit allow-list, so the CLI inherits nothing from this
session: no `ANTHROPIC_*` or `CLAUDE_*` value, no token. It never uses a `claude` on PATH, only the
CLI in the SDK wheel. Marked `network` (TCP on loopback, a 242 MB binary); not part of the gate.

It skips until the chassis route exists. Run it with:
    uv run pytest -m network packages/workloads/echo-claude-agent -q -rs
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import pytest

pytestmark = pytest.mark.network

pytest.importorskip(
    "chassis.server.model_proxy_messages",
    reason="the chassis /v1/messages route (PoC-6 B1) is not merged yet",
)

ROOT = Path(__file__).resolve().parents[4]
EVENTS_SCHEMA = json.loads((ROOT / "packages/chassis/schemas/events.v0.json").read_text())
BAKEOFF = ROOT / "packages/fake-model-server/scripts/bakeoff.yaml"
TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
RUN_TIMEOUT_S = 180

GLOSSARY = {"SLM": "small language model"}
ACRONYMS = {"RAG": "retrieval-augmented generation", "SLM": "small language model"}

CHILD = """
import asyncio, json, sys
from echo_claude_agent import handle

async def main() -> None:
    ctx = json.loads(sys.argv[2])
    async for event in handle({"text": sys.argv[1], "data": {}}, ctx):
        print(json.dumps(event), flush=True)

asyncio.run(main())
"""


def _tcp_allowed() -> bool:
    try:
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).close()
    except Exception:  # pytest-socket raises its own error type when TCP is disabled
        return False
    return True


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Recorder:
    """ASGI wrapper that keeps (method, path, traceparent) of each HTTP request."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.seen: list[tuple[str, str, str | None]] = []

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            self.seen.append((scope["method"], scope["path"], headers.get("traceparent")))
        await self.app(scope, receive, send)


class ThreadedServer:
    """A uvicorn server on loopback in a thread, with an optional app lifespan around it."""

    def __init__(self, app: Any, lifespan_app: Any = None) -> None:
        import uvicorn

        self.port = _free_port()
        self.lifespan_app = lifespan_app
        config = uvicorn.Config(
            app, host="127.0.0.1", port=self.port, log_level="warning", lifespan="off"
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=lambda: asyncio.run(self._main()), daemon=True)

    async def _main(self) -> None:
        async with contextlib.AsyncExitStack() as stack:
            if self.lifespan_app is not None:
                app = self.lifespan_app
                await stack.enter_async_context(app.router.lifespan_context(app))
            await self.server.serve()

    def __enter__(self) -> ThreadedServer:
        self.thread.start()
        deadline = time.monotonic() + 20
        while not self.server.started:
            assert self.thread.is_alive() and time.monotonic() < deadline, "server did not start"
            time.sleep(0.05)
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=20)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _mcp_stub() -> Any:
    from fastmcp import FastMCP

    server: FastMCP = FastMCP(name="chassis-tools-stub")

    @server.tool
    def glossary_lookup(term: str) -> dict[str, Any]:
        """Look up a platform term."""
        return {"term": term, "definition": GLOSSARY.get(term)}

    @server.tool
    def acronym_expand(acronym: str) -> dict[str, Any]:
        """Expand an acronym."""
        return {"acronym": acronym, "expansion": ACRONYMS.get(acronym)}

    return server.http_app(path="/mcp", stateless_http=True)


class TrimTrailingSystem(httpx.AsyncBaseTransport):
    """The fake model picks a rule by the LAST message: after a tool round it wants a `tool`
    message. The CLI adds a `<total_tokens>` note as a `role: "system"` turn, and a reminder in
    the user turn, after each tool result, and the route keeps them in order, so the fake would
    see no tool result. This drops those trailing turns when a tool result sits before them. It
    touches only the fake's input; the route and the CLI are the real ones."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"null")
        messages = body.get("messages") if isinstance(body, dict) else None
        if isinstance(messages, list):
            kept = list(messages)
            while kept and kept[-1].get("role") in ("system", "user"):
                kept.pop()
            if kept and kept[-1].get("role") == "tool" and len(kept) < len(messages):
                headers = {k: v for k, v in request.headers.items() if k != "content-length"}
                request = httpx.Request(
                    request.method, request.url, headers=headers, json={**body, "messages": kept}
                )
        return await self.inner.handle_async_request(request)


def _chassis_proxy() -> tuple[Any, Any]:
    """(public app, proxy app): the real chassis model proxy over a ScriptedModel-free stack, the
    fake model server behind the `LiteLLMModel` adapter."""
    litellm = importlib.import_module("chassis.adapters.litellm")
    fakes = importlib.import_module("chassis.fakes")
    bundle = importlib.import_module("chassis.ports.bundle")
    server = importlib.import_module("chassis.server")
    proxy_app = importlib.import_module("chassis.server.proxy_app")
    handle_mod = importlib.import_module("chassis.core.handle")
    fake_model_server = importlib.import_module("fake_model_server")

    script = fake_model_server.Script.from_yaml(BAKEOFF)
    for rule in script.rules:
        # The CLI knows MCP tools as `mcp__<server>__<tool>` and says "No such tool available" to a
        # bare name, so the scripted calls carry the prefix. `handle` strips it from the event.
        if rule.tool_call is not None:
            rule.tool_call.name = f"mcp__chassis__{rule.tool_call.name}"
    fake = fake_model_server.create_app(script)
    model = litellm.LiteLLMModel(
        "http://fake/v1", transport=TrimTrailingSystem(httpx.ASGITransport(app=fake))
    )
    ports = bundle.PortBundle(
        model=model,
        engine=fakes.FakeEngine(handle=handle_mod.echo),
        config=fakes.InMemoryConfig(),
        telemetry=fakes.InMemoryTelemetry(),
    )
    config = server.ChassisConfig.model_validate(
        {
            "profile": "fake",
            "agent": {"name": "echo", "version": "0.0.1"},
            "spec": {
                "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}
            },
        }
    )
    public = server.create_app(config, ports)
    return public, proxy_app.create_proxy_app(public)


def _open_run(public: Any) -> Any:
    """Register an in-flight run for the traceparent's trace id, as `/v1/run` does when it starts
    one. The CLI's calls (`max_tokens` 32000) are then correlated and charged to this run's
    budget, not to the uncorrelated cap (which stays at its default)."""
    envelope = importlib.import_module("chassis.core.envelope")
    trace_id = TRACEPARENT.split("-")[1]
    request = envelope.Request(
        request_id="req-e2e",
        trace_id=trace_id,
        idempotency_key="idem-e2e",
        agent="echo",
        agent_version="0.0.1",
        input=envelope.TaskInput(text="x"),
        budget=envelope.Budget(max_tokens=1_000_000),
    )
    ctx = envelope.Context(
        request_id=request.request_id,
        trace_id=trace_id,
        idempotency_key=request.idempotency_key,
        agent=request.agent,
        agent_version=request.agent_version,
        budget=request.budget,
        versions=envelope.Versions(chassis="0"),
    )
    return public.state.runs.open(request, ctx)


class Stack:
    def __init__(self, proxy: Recorder, tools: Recorder, proxy_url: str, tool_url: str) -> None:
        self.proxy, self.tools = proxy, tools
        self.proxy_url, self.tool_url = proxy_url, tool_url


@pytest.fixture
def stack() -> Iterator[Stack]:
    if not _tcp_allowed():
        pytest.skip("TCP is disabled (the offline gate); run with `uv run pytest -m network`")
    public, proxy_app = _chassis_proxy()
    proxy, tools = Recorder(proxy_app), Recorder(_mcp_stub())
    with (
        ThreadedServer(proxy, lifespan_app=public) as proxy_server,
        ThreadedServer(tools, lifespan_app=tools.app) as tool_server,
    ):
        record = _open_run(public)
        try:
            yield Stack(proxy, tools, proxy_server.url + "/v1", tool_server.url + "/mcp")
        finally:
            public.state.runs.close(record)


def _run_child(stack: Stack, text: str, tmp_path: Path) -> list[dict[str, Any]]:
    """`handle` in a child started with `env -i` and this allow-list, nothing else."""
    base = tmp_path / "homes"
    allow = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "NO_PROXY": "127.0.0.1,localhost",
        "CHASSIS_MODEL_URL": stack.proxy_url,
        "CHASSIS_TOOL_URL": stack.tool_url,
        "CLAUDE_AGENT_HOME_BASE": str(base),
    }
    ctx = {
        "request_id": "e2e-1",
        "model_route": "big-default",
        "traceparent": TRACEPARENT,
        "budget": {"timeout_ms": RUN_TIMEOUT_S * 1000 - 10_000},
    }
    cmd = ["env", "-i", *(f"{k}={v}" for k, v in allow.items())]
    cmd += [sys.executable, "-I", "-c", CHILD, text, json.dumps(ctx)]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=RUN_TIMEOUT_S, check=False)
    assert done.returncode == 0, done.stderr[-2000:]
    assert not base.exists() or list(base.iterdir()) == [], "a run dir survived"
    return [json.loads(line) for line in done.stdout.splitlines() if line.strip()]


def _check(events: list[dict[str, Any]]) -> str:
    for event in events:
        jsonschema.validate(event, EVENTS_SCHEMA)
    assert events[0]["type"] == "start", events
    summary = [(e["type"], e.get("name") or e.get("code"), e.get("arguments")) for e in events]
    assert events[-1]["type"] == "end" and events[-1]["status"] == "ok", summary
    return "".join(e["text"] for e in events if e["type"] == "delta")


def test_smoke_simplifier_and_lookup_through_the_real_cli(stack: Stack, tmp_path: Path) -> None:
    smoke = _run_child(stack, "simplify: Hello.", tmp_path)
    assert "delta" in [e["type"] for e in smoke]
    assert _check(smoke).strip()

    text = "simplify: The SLM, released in 2026 by Acme, cut costs by 30 percent."
    answer = _check(_run_child(stack, text, tmp_path))
    assert "2026" in answer and "Acme" in answer and "30" in answer

    lookup = _run_child(stack, "lookup: Define SLM and expand RAG.", tmp_path)
    answer = _check(lookup)
    calls = [e for e in lookup if e["type"] == "tool_call"]
    assert [(c["name"], c["arguments"]) for c in calls] == [
        ("glossary_lookup", {"term": "SLM"}),
        ("acronym_expand", {"acronym": "RAG"}),
    ]
    assert "small language model" in answer
    assert "retrieval-augmented generation" in answer

    # The CLI talked to the proxy and the MCP stub only, and sent the traceparent to both.
    model_calls = [s for s in stack.proxy.seen if s[1] == "/v1/messages"]
    assert model_calls and all(s[0] == "POST" for s in model_calls)
    assert {s[1] for s in stack.proxy.seen} == {"/v1/messages"}
    assert all(s[2] == TRACEPARENT for s in model_calls)
    tool_calls = [s for s in stack.tools.seen if s[1] == "/mcp"]
    assert tool_calls and all(s[2] == TRACEPARENT for s in tool_calls)
