"""PoC-2 measurements: event mapping size, token overhead against plain Python, the local hop's
extra latency, the overhead per streamed delta, and the time to first token.

Run from the repo root:

    uv run python pocs/poc-02-two-engines-one-contract/demo/measure.py --runs 200 \
        --out notes/2026-10-01-measurements.md

`--out` is relative to the PoC folder unless it is absolute. Everything runs offline in one
process: the fake model server and a FastMCP stub of the chassis tool endpoint are reached through
the workloads' own test transport hooks (`httpx.ASGITransport` / `httpx2.ASGITransport`), and the
`sidecar` lane is the real `SidecarConnector` over a Unix socket to the `workload_a2a` template
server, served by uvicorn in the same event loop. No TCP socket, no Docker, no key.

A PoC script, not package code: it may import the chassis, the workloads, and the fake model server.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import importlib
import io
import json
import logging
import os
import platform
import shutil
import statistics
import sys
import tempfile
import time
import tokenize
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import date
from importlib import metadata
from pathlib import Path
from typing import Any

import httpx
import httpx2
import uvicorn
from chassis.adapters.a2a import InProcessConnector, SidecarConnector
from chassis.adapters.a2a.inprocess import load_handle
from chassis.core.envelope import Context, Request, TaskInput
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.ports.engine import EngineConnector
from chassis_contracts.helpers import make_context, make_request
from fake_model_server import Script
from fake_model_server import create_app as create_fake_model_app
from fastmcp import FastMCP
from workload_a2a.server import build_agent_card, build_app

POC = Path(__file__).resolve().parents[1]
ROOT = POC.parents[1]
EXAMPLE_SCRIPT = ROOT / "packages/fake-model-server/scripts/example.yaml"
SIDECAR_URL = "http://127.0.0.1:9000"
"""What the connector's config names. Over `uds` the host and port only fill the `Host` header."""
ROUTE = "big-default"
GLOSSARY = "glossary: what is SLM?"
SIMPLIFY = "simplify: the quick brown fox"
DELTA_COUNTS = (1, 10, 100)
CAVEAT = "fake model, Unix socket, one laptop"
TOKENS = 500
"""suggested: a typical answer length, only to turn the per-delta cost into a per-answer one."""

ENGINES: dict[str, str] = {
    "plain Python (echo-python)": "echo_python:handle",
    "PydanticAI (echo-pydanticai)": "echo_pydanticai:handle",
    "LangGraph (echo-langgraph)": "echo_langgraph:handle",
}
PLAIN = "plain Python (echo-python)"
SHARED = "shared runtime (chassis mapping + workload_a2a server)"

MAPPING_FILES: dict[str, list[str]] = {
    "plain Python (echo-python)": [
        "packages/workloads/echo-python/src/echo_python/handle.py",
        "packages/workloads/echo-python/src/echo_python/tools.py",
    ],
    "PydanticAI (echo-pydanticai)": [
        "packages/workloads/echo-pydanticai/src/echo_pydanticai/mapping.py",
        "packages/workloads/echo-pydanticai/src/echo_pydanticai/handle.py",
    ],
    "LangGraph (echo-langgraph)": [
        "packages/workloads/echo-langgraph/src/echo_langgraph/mapping.py",
        "packages/workloads/echo-langgraph/src/echo_langgraph/handle.py",
        "packages/workloads/echo-langgraph/src/echo_langgraph/tools.py",
    ],
    "TypeScript (echo-typescript)": [
        "packages/workloads/echo-typescript/src/a2a_server.ts",
        "packages/workloads/echo-typescript/src/handle.ts",
        "packages/workloads/echo-typescript/src/schema.ts",
    ],
    SHARED: [
        "packages/chassis/src/chassis/adapters/a2a/mapping.py",
        "packages/workload-a2a/src/workload_a2a/server.py",
    ],
}

PACKAGES = (
    "a2a-sdk",
    "pydantic-ai-slim",
    "langgraph",
    "langchain-openai",
    "fastmcp",
    "mcp",
    "openai",
    "httpx",
    "uvicorn",
)


# --- 1. Event mapping size ---


@dataclass
class LineCount:
    path: str
    total: int
    blank: int
    comment: int
    """Comment lines, and docstring lines for Python; `//` and `/* */` lines for TypeScript."""

    @property
    def code(self) -> int:
        return self.total - self.blank - self.comment


def _python_comment_lines(source: str) -> set[int]:
    lines: set[int] = set()
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if (
            tok.type == tokenize.COMMENT
            and not source.splitlines()[tok.start[0] - 1][: tok.start[1]].strip()
        ):
            lines.add(tok.start[0])
    tree = ast.parse(source)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        # A bare string expression is a docstring (module, class, function, or attribute).
        for stmt in body:
            if (
                isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            ):
                end = stmt.end_lineno or stmt.lineno
                lines.update(range(stmt.lineno, end + 1))
    return lines


def _typescript_comment_lines(source: str) -> set[int]:
    lines: set[int] = set()
    in_block = False
    for number, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if in_block:
            lines.add(number)
            if "*/" in stripped:
                in_block = False
        elif stripped.startswith("//"):
            lines.add(number)
        elif stripped.startswith("/*"):
            lines.add(number)
            in_block = "*/" not in stripped
    return lines


def count_lines(relative: str) -> LineCount:
    source = (ROOT / relative).read_text()
    rows = source.splitlines()
    blank = {i for i, line in enumerate(rows, start=1) if not line.strip()}
    comments = (
        _python_comment_lines(source)
        if relative.endswith(".py")
        else _typescript_comment_lines(source)
    )
    return LineCount(relative, len(rows), len(blank), len(comments - blank))


# --- Shared helpers ---


def percentile(values: Sequence[float], q: int) -> float:
    return statistics.quantiles(values, n=100, method="inclusive")[q - 1]


def ms(seconds: float) -> str:
    return f"{seconds * 1000:.2f}"


def request_for(text: str, *, data: dict[str, Any] | None = None, n: int = 0) -> Request:
    request = make_request(text=text, request_id=f"req-measure-{n}")
    return request.model_copy(update={"input": TaskInput(text=text, data=dict(data or {}))})


def context_for(request: Request) -> Context:
    return make_context(request, model_route=ROUTE)


def bundle(connector: EngineConnector) -> PortBundle:
    return PortBundle(
        model=ScriptedModel(),
        engine=connector,
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )


class CountingTransport(httpx2.AsyncBaseTransport):
    """An `httpx2` transport that counts requests and hands them to an inner transport."""

    def __init__(self, inner: httpx2.AsyncBaseTransport) -> None:
        self.inner = inner
        self.count = 0

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.count += 1
        return await self.inner.handle_async_request(request)


def glossary_stub() -> FastMCP:
    server: FastMCP = FastMCP(name="chassis-tools-stub")

    @server.tool
    def glossary_lookup(term: str) -> dict[str, str]:
        """Look up a platform term."""
        return {"term": term, "definition": "A small language model."}

    return server


@dataclass
class Hooks:
    """The workloads' test transport hooks, pointed at one fake model app and one MCP stub."""

    mcp_app: Any
    model_app: Any = None
    mcp: CountingTransport | None = None

    def install(self) -> None:
        """A fresh fake model app (so `state.calls` is per engine) and a fresh MCP counter."""
        self.model_app = create_fake_model_app(Script.from_yaml(EXAMPLE_SCRIPT))
        self.mcp = CountingTransport(httpx2.ASGITransport(app=self.mcp_app))
        mcp = self.mcp

        echo_python_handle: Any = importlib.import_module("echo_python.handle")
        echo_python_tools: Any = importlib.import_module("echo_python.tools")
        echo_python_handle.transport = httpx.ASGITransport(app=self.model_app)
        echo_python_tools.client_factory = lambda headers, timeout: httpx2.AsyncClient(
            transport=mcp, headers=headers, timeout=timeout
        )

        pydanticai: Any = importlib.import_module("echo_pydanticai.handle")
        pydanticai.model_transport = httpx2.ASGITransport(app=self.model_app)
        pydanticai.tool_transport = mcp

        langgraph: Any = importlib.import_module("echo_langgraph.handle")
        langgraph_tools: Any = importlib.import_module("echo_langgraph.tools")
        langgraph.model_transport = httpx2.ASGITransport(app=self.model_app)
        langgraph_tools.transport = mcp

    @property
    def calls(self) -> list[dict[str, Any]]:
        return list(self.model_app.state.calls)


@asynccontextmanager
async def serve_uds(app: Any) -> AsyncIterator[str]:
    """Serve `app` on uvicorn over a fresh Unix socket in this event loop; yields the path. The
    same pattern as `packages/chassis/tests/a2a_uds.py`."""
    folder = Path(tempfile.mkdtemp(prefix="m-"))
    path = str(folder / "a2a.sock")
    server = uvicorn.Server(uvicorn.Config(app, uds=path, log_level="warning", lifespan="off"))
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
        server.force_exit = True
        await task
        shutil.rmtree(folder, ignore_errors=True)


@asynccontextmanager
async def lanes(handle_path: str) -> AsyncIterator[dict[str, EngineConnector]]:
    """Both lanes on one handle: `inprocess` (the chassis's in-memory A2A) and `sidecar`
    (`SidecarConnector` over a Unix socket to `workload_a2a.server`)."""
    async with AsyncExitStack() as stack:
        inprocess = InProcessConnector()
        await inprocess.setup({"connector": "inprocess", "handle": handle_path}, bundle(inprocess))
        stack.push_async_callback(inprocess.close)
        card = build_agent_card(name=handle_path, version="0.0.1", url=SIDECAR_URL)
        uds = await stack.enter_async_context(serve_uds(build_app(load_handle(handle_path), card)))
        sidecar = SidecarConnector()
        await sidecar.setup(
            {"connector": "sidecar", "url": SIDECAR_URL, "uds": uds}, bundle(sidecar)
        )
        stack.push_async_callback(sidecar.close)
        yield {"inprocess": inprocess, "sidecar": sidecar}


@dataclass
class Timing:
    first_delta: float
    end: float
    deltas: int
    cpu: float
    """Process CPU time to `end`. Client and server share this process, so it is the path's own
    cost, without the time the process waits for a core on a busy machine."""


async def time_connector(connector: EngineConnector, request: Request) -> Timing:
    """From `run()` (first event requested) to the first `delta` and to `end`."""
    ctx = context_for(request)
    first: float | None = None
    deltas = 0
    end: float | None = None
    cpu = 0.0
    start, cpu_start = time.perf_counter(), time.process_time()
    # Read to exhaustion: `run()` returns after `end`, and closing it early from outside would
    # leave its span to be finalized in another context.
    async for event in connector.run(request, ctx):
        if event.type == "delta":
            deltas += 1
            if first is None:
                first = time.perf_counter() - start
        elif event.type == "error":
            raise RuntimeError(f"{connector.kind}: {event}")
        elif event.type == "end":
            end = time.perf_counter() - start
            cpu = time.process_time() - cpu_start
    if end is None:
        raise RuntimeError(f"{connector.kind}: no end event")
    return Timing(first if first is not None else end, end, deltas, cpu)


async def time_handle(path: str, request: Request) -> Timing:
    """The same, calling the workload's wire-form `handle` directly: no lane at all."""
    handle = load_handle(path)
    ctx = context_for(request).model_dump(mode="json")
    first: float | None = None
    deltas = 0
    start, cpu_start = time.perf_counter(), time.process_time()
    async for raw in handle(request.input.model_dump(mode="json"), ctx):
        kind = dict(raw).get("type")
        if kind == "delta":
            deltas += 1
            if first is None:
                first = time.perf_counter() - start
        elif kind == "error":
            raise RuntimeError(f"{path}: {raw}")
    end, cpu = time.perf_counter() - start, time.process_time() - cpu_start
    return Timing(first if first is not None else end, end, deltas, cpu)


# --- 2. Token overhead against plain Python ---


def _compact(value: Any) -> int:
    """Bytes of `value` as compact UTF-8 JSON, the same serializer for every engine."""
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())


@dataclass
class CallShape:
    messages: int
    messages_bytes: int
    tools_bytes: int
    body_bytes: int
    keys: list[str]


@dataclass
class EngineShape:
    engine: str
    calls: list[CallShape]
    usage: tuple[int, int]
    mcp_requests: int
    event_types: list[str] = field(default_factory=list)


async def token_shape(engine: str, path: str, hooks: Hooks) -> EngineShape:
    hooks.install()
    request = request_for(GLOSSARY)
    ctx = context_for(request).model_dump(mode="json")
    events = [dict(e) async for e in load_handle(path)(request.input.model_dump(mode="json"), ctx)]
    metrics = next((e for e in events if e.get("type") == "metrics"), {})
    if events[-1].get("type") != "end":
        raise RuntimeError(f"{engine} did not end: {events[-1]}")
    calls = [
        CallShape(
            messages=len(body.get("messages", [])),
            messages_bytes=_compact(body.get("messages", [])),
            tools_bytes=_compact(body["tools"]) if "tools" in body else 0,
            body_bytes=_compact(body),
            keys=sorted(body),
        )
        for body in hooks.calls
    ]
    assert hooks.mcp is not None
    return EngineShape(
        engine=engine,
        calls=calls,
        usage=(int(metrics.get("input_tokens", 0)), int(metrics.get("output_tokens", 0))),
        mcp_requests=hooks.mcp.count,
        event_types=[str(e.get("type")) for e in events],
    )


# --- 4. Overhead per streamed delta ---


async def deltas_handle(
    input: dict[str, Any], ctx: dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    """`start`, `input.data.n` one-character deltas, `end`. No model call, no tool."""
    yield {"schema_version": "0", "type": "start", "request_id": str(ctx.get("request_id", ""))}
    for _ in range(int(input.get("data", {}).get("n", 1))):
        yield {"schema_version": "0", "type": "delta", "text": "x"}
    yield {"schema_version": "0", "type": "end", "status": "ok"}


DELTAS_PATH = f"{__name__}:deltas_handle"


# --- Runner ---


@dataclass
class Samples:
    first: list[float] = field(default_factory=list)
    end: list[float] = field(default_factory=list)
    cpu: list[float] = field(default_factory=list)

    def add(self, timing: Timing) -> None:
        self.first.append(timing.first_delta)
        self.end.append(timing.end)
        self.cpu.append(timing.cpu)


def _sent(shape: EngineShape) -> int:
    return sum(c.messages_bytes + c.tools_bytes for c in shape.calls)


def _p(values: Sequence[float]) -> tuple[str, str]:
    return ms(percentile(values, 50)), ms(percentile(values, 95))


async def measure(runs: int, warmup: int, log: Callable[[str], None]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    mcp_app = glossary_stub().http_app(path="/mcp", stateless_http=True)
    async with mcp_app.router.lifespan_context(mcp_app):
        hooks = Hooks(mcp_app)

        log("2. token overhead")
        out["shapes"] = [await token_shape(name, path, hooks) for name, path in ENGINES.items()]

        # 3 and 5 (lanes): echo_python on both lanes, and directly, interleaved per run.
        log("3/5. lane latency and time to first token, echo_python")
        hooks.install()
        lane_samples: dict[str, Samples] = {
            "direct handle": Samples(),
            "inprocess": Samples(),
            "sidecar": Samples(),
        }
        async with lanes("echo_python:handle") as connectors:
            for i in range(warmup + runs):
                request = request_for(SIMPLIFY, n=i)
                timings = {"direct handle": await time_handle("echo_python:handle", request)}
                for lane, connector in connectors.items():
                    timings[lane] = await time_connector(connector, request)
                if i >= warmup:
                    for lane, timing in timings.items():
                        lane_samples[lane].add(timing)
                hooks.model_app.state.calls.clear()
        out["lanes"] = lane_samples

        # 4: the deltas handle on both lanes and directly, every N in each run (paired).
        log("4. overhead per streamed delta")
        per_n: dict[str, dict[int, Samples]] = {
            lane: {n: Samples() for n in DELTA_COUNTS}
            for lane in ("direct handle", "inprocess", "sidecar")
        }
        async with lanes(DELTAS_PATH) as connectors:
            for i in range(warmup + runs):
                for n in DELTA_COUNTS:
                    request = request_for("deltas", data={"n": n}, n=i)
                    timings = {"direct handle": await time_handle(DELTAS_PATH, request)}
                    for lane, connector in connectors.items():
                        timings[lane] = await time_connector(connector, request)
                    for lane, timing in timings.items():
                        if timing.deltas != n:
                            raise RuntimeError(f"{lane}: {timing.deltas} deltas, expected {n}")
                        if i >= warmup:
                            per_n[lane][n].add(timing)
        out["deltas"] = per_n

        # 5 (engines): each Python engine's own handle, directly, no lane.
        log("5. time to first token per engine")
        engine_samples: dict[str, Samples] = {name: Samples() for name in ENGINES}
        for name, path in ENGINES.items():
            hooks.install()
            for i in range(warmup + runs):
                timing = await time_handle(path, request_for(SIMPLIFY, n=i))
                if i >= warmup:
                    engine_samples[name].add(timing)
                hooks.model_app.state.calls.clear()
        out["engines"] = engine_samples
    return out


# --- The note ---


def environment(load: tuple[tuple[float, ...], tuple[float, ...]]) -> list[str]:
    versions = []
    for name in PACKAGES:
        try:
            versions.append(f"{name} {metadata.version(name)}")
        except metadata.PackageNotFoundError:
            continue
    return [
        f"- Python {platform.python_version()} ({platform.python_implementation()})",
        f"- Machine: {platform.platform()}, {platform.machine()}, {os.cpu_count()} CPUs",
        f"- Packages: {', '.join(versions)}",
        "- Load average (1, 5, 15 min) at start: "
        + ", ".join(f"{x:.1f}" for x in load[0])
        + "; at end: "
        + ", ".join(f"{x:.1f}" for x in load[1]),
    ]


def render(
    results: dict[str, Any],
    args: argparse.Namespace,
    took_s: float,
    load: tuple[tuple[float, ...], tuple[float, ...]],
) -> str:
    today = date.today().isoformat()
    lines: list[str] = [
        f"# PoC-2 measurements ({today})",
        "",
        "Exit criterion: event mapping size (lines of code), token overhead against plain Python, "
        "the local hop's extra latency (p50 and p95), the overhead per streamed delta, and the "
        "time to first token are recorded.",
        "",
        "Produced by, from the repo root:",
        "",
        "```bash",
        "uv run python pocs/poc-02-two-engines-one-contract/demo/measure.py "
        f"--runs {args.runs} --warmup {args.warmup} --out {args.out}",
        "```",
        "",
        f"{args.runs} measured runs per series after {args.warmup} warm-up runs; the whole script "
        f"took {took_s:.0f} s.",
        "",
        "## Environment",
        "",
        *environment(load),
        "",
        "## Caveats",
        "",
        f"Every number below is from a {CAVEAT}. Read them as orders of magnitude, not as a "
        "budget.",
        "",
        "- Docker was not running and the offline gate blocks TCP, so nothing here goes over TCP "
        "or between containers. The `sidecar` lane is the real `SidecarConnector` and the real "
        "`workload_a2a` server, over a Unix socket, with uvicorn in the same process and the same "
        "event loop as the client. A real sidecar is another process in another container: it "
        "adds a process hop and loopback TCP, and it removes the contention of one event loop.",
        "- The `inprocess` lane is the chassis's in-memory A2A (`httpx.ASGITransport`). That "
        "transport runs the whole app call before it returns the body, so in this lane every "
        "event, the first `delta` too, arrives at the end of the run.",
        "- The model is the fake model server, reached through each workload's test transport "
        "hook (`httpx.ASGITransport` or `httpx2.ASGITransport`), with no chassis model proxy in "
        "the path. It answers at once, so a time here is the stack's own overhead, with no model "
        "time in it. The tool endpoint is a FastMCP stub of the chassis's `glossary_lookup`, "
        "over the same kind of hook; every engine lists the tools on every run.",
        "- Token counts are scripted by the fake server, not real. The token overhead is a "
        "byte-count proxy; see that section.",
        "- The laptop was not idle: other work shared the CPUs (load average above). The same "
        "handle can differ between two sections of one run; compare rows within a table, not "
        "across tables.",
        "- p50 and p95 are over the measured runs of one series. A difference of two p95s is "
        "not the p95 of the difference.",
        "",
    ]

    # 1
    lines += [
        "## Event mapping size",
        "",
        "Lines per engine: the code that turns the framework's output into chassis events, and "
        "its tool plumbing. `code` is total minus blank lines, comment lines, and docstring "
        "lines (Python) or `//` and `/* */` lines (TypeScript).",
        "",
        "| Engine | Files | Total lines | Code lines |",
        "| ------ | ----- | ----------: | ---------: |",
    ]
    per_file: list[LineCount] = []
    engine_code: dict[str, int] = {}
    for engine, files in MAPPING_FILES.items():
        counts = [count_lines(f) for f in files]
        per_file += counts
        names = ", ".join(f"`{Path(c.path).name}`" for c in counts)
        total = sum(c.total for c in counts)
        code = sum(c.code for c in counts)
        engine_code[engine] = code
        lines.append(f"| {engine} | {names} | {total} | {code} |")
    lines += [
        "",
        "| File | Total | Blank | Comment or docstring | Code |",
        "| ---- | ----: | ----: | -------------------: | ---: |",
    ]
    for c in per_file:
        lines.append(f"| `{c.path}` | {c.total} | {c.blank} | {c.comment} | {c.code} |")
    shared = engine_code.pop(SHARED)
    own = list(engine_code.values())
    lines += [
        "",
        f"What it means: each engine's own mapping is {min(own)} to {max(own)} code lines, in "
        f"the workload; the shared runtime ({shared} code lines) is written once and every "
        "Python workload reuses it. `packages/workload-a2a/src/workload_a2a/mapping.py` is a "
        "copy of the chassis mapping "
        "(a test diffs them) and is not counted again. Plain Python's count includes its "
        "hand-written tool loop (`tools.py`), which the frameworks do for it; LangGraph's "
        "`tools.py` is a stand-in for `langchain-mcp-adapters` and goes away when that works with "
        "`mcp` 2.",
        "",
    ]

    # 2
    shapes: list[EngineShape] = results["shapes"]
    plain = next(s for s in shapes if s.engine == PLAIN)
    plain_total = _sent(plain)
    lines += [
        "## Token overhead against plain Python",
        "",
        f"The same request (`{GLOSSARY}`), which calls `glossary_lookup` once, through each "
        "engine's `handle`. What each engine sent to the model, from the fake server's record of "
        "every request body (`app.state.calls`), re-serialized as compact JSON with one "
        "serializer so formatting does not count. `sent` is `messages` plus `tools` bytes over "
        "all calls.",
        "",
        "| Engine | Model calls | Messages (per call) | `messages` bytes (per call) | "
        "`tools` bytes (per call) | Sent bytes | Against plain Python | Scripted usage in/out | "
        "MCP HTTP requests |",
        "| ------ | ----------: | ------------------- | --------------------------- | "
        "------------------------ | ---------: | -------------------: | --------------------- | "
        "----------------: |",
    ]
    for s in shapes:
        sent = _sent(s)
        delta = sent - plain_total
        rel = f"{delta:+d} B ({delta / plain_total:+.0%})" if plain_total else "n/a"
        lines.append(
            f"| {s.engine} | {len(s.calls)} | {', '.join(str(c.messages) for c in s.calls)} | "
            f"{', '.join(str(c.messages_bytes) for c in s.calls)} | "
            f"{', '.join(str(c.tools_bytes) for c in s.calls)} | {sent} | "
            f"{'baseline' if s is plain else rel} | {s.usage[0]} / {s.usage[1]} | "
            f"{s.mcp_requests} |"
        )
    spread = ", ".join(
        f"{s.engine.split(' (')[0]} {_sent(s) - plain_total:+d}" for s in shapes if s is not plain
    )
    lines += [
        "",
        "Request body keys each engine sent (first call):",
        "",
    ]
    for s in shapes:
        keys = ", ".join(f"`{k}`" for k in s.calls[0].keys) if s.calls else "none"
        lines.append(f"- {s.engine}: {keys}")
    lines += [
        "",
        f"What it means: for the same work the frameworks sent {spread} bytes against plain "
        "Python's `messages` and `tools`, from the same number of model calls and messages. "
        "suggested: bytes are the offline "
        "proxy for tokens (roughly 4 bytes per token for English and JSON). The scripted usage "
        "is the same for every engine by construction, so it says nothing about overhead. Real "
        "token counts need the `local` Compose variant with a tokenizer-backed router (LiteLLM "
        "logs them per call); that run is not done here.",
        "",
    ]

    # 3
    lane: dict[str, Samples] = results["lanes"]
    lines += [
        "## The local hop's extra latency",
        "",
        f"`echo_python:handle` on `{SIMPLIFY}`, wall time from `run()` (first event requested) to "
        "`end`. The same request each run through three paths, interleaved: the workload's "
        "`handle` called directly (no lane), the `inprocess` lane, and the `sidecar` lane over a "
        "Unix socket. The model is the fake server behind the workload's transport hook in every "
        "path, so the paths differ only by the lane. Wall time is the latency; CPU time is the "
        "process's CPU time over the same span, which a busy machine does not inflate (client "
        "and server share the process).",
        "",
        "| Path | Wall p50 ms | Wall p95 ms | CPU p50 ms | CPU p95 ms |",
        "| ---- | ----------: | ----------: | ---------: | ---------: |",
    ]
    for name, samples in lane.items():
        p50, p95 = _p(samples.end)
        c50, c95 = _p(samples.cpu)
        lines.append(f"| {name} | {p50} | {p95} | {c50} | {c95} |")

    def q(path: str, values: str) -> tuple[float, float]:
        series: list[float] = getattr(lane[path], values)
        return percentile(series, 50), percentile(series, 95)

    (ip50, ip95), (sp50, sp95), (dp50, dp95) = (
        q(p, "end") for p in ("inprocess", "sidecar", "direct handle")
    )
    (ic50, ic95), (sc50, sc95), (dc50, dc95) = (
        q(p, "cpu") for p in ("inprocess", "sidecar", "direct handle")
    )
    lines += [
        f"| extra: `sidecar` minus `inprocess` | {ms(sp50 - ip50)} | {ms(sp95 - ip95)} | "
        f"{ms(sc50 - ic50)} | {ms(sc95 - ic95)} |",
        f"| extra: `sidecar` minus direct `handle` | {ms(sp50 - dp50)} | {ms(sp95 - dp95)} | "
        f"{ms(sc50 - dc50)} | {ms(sc95 - dc95)} |",
        "",
        f"What it means: going over the socket instead of in memory costs {ms(sp50 - ip50)} ms "
        f"at p50 and {ms(sp95 - ip95)} ms at p95 here"
        + (
            " (a negative value: the hop is below the noise of this setup)"
            if min(sp50 - ip50, sp95 - ip95) < 0
            else ""
        )
        + f" ({ms(sc50 - ic50)} ms of CPU at p50); the whole A2A path over the socket costs "
        f"{ms(sp50 - dp50)} ms at p50 over calling `handle` directly. The socket hop is HTTP "
        "over a Unix socket, server-sent events, and a second uvicorn task. Loopback TCP "
        "between two containers will add to it.",
        "",
    ]

    # 4
    per_n: dict[str, dict[int, Samples]] = results["deltas"]
    top = DELTA_COUNTS[-1]
    lines += [
        "## Overhead per streamed delta",
        "",
        "A handle that yields `start`, N one-character `delta`s, and `end`, with no model call, "
        "N = " + ", ".join(str(n) for n in DELTA_COUNTS) + ". Wall time from `run()` to `end` per "
        "N, then the per-delta overhead (time for N minus time for 1) / (N - 1), taken per run "
        "(the N values run back to back in each run) and then p50 and p95 over runs.",
        "",
        "| Path | "
        + " | ".join(f"N={n} p50 ms | N={n} p95 ms" for n in DELTA_COUNTS)
        + " | Per delta p50 µs | Per delta p95 µs | Per delta CPU p50 µs | Per delta CPU p95 µs |",
        "| ---- | "
        + " | ".join("-----: | -----:" for _ in DELTA_COUNTS)
        + " | -----: | -----: | -----: | -----: |",
    ]
    per_delta: dict[str, float] = {}
    for name, by_n in per_n.items():
        cells = []
        for n in DELTA_COUNTS:
            p50, p95 = _p(by_n[n].end)
            cells += [p50, p95]
        for values in ("end", "cpu"):
            tops: list[float] = getattr(by_n[top], values)
            ones: list[float] = getattr(by_n[1], values)
            per = [(a - b) / (top - 1) for a, b in zip(tops, ones, strict=True)]
            per50 = percentile(per, 50) * 1e6
            cells += [f"{per50:.1f}", f"{percentile(per, 95) * 1e6:.1f}"]
            per_delta[f"{name}:{values}"] = per50
        lines.append(f"| {name} | {' | '.join(cells)} |")
    lines += [
        "",
        f"The per-delta columns use N = {top} against N = 1. What it means: each `delta` is one "
        "A2A `TaskArtifactUpdateEvent` with the chassis event in its metadata, validated on the "
        "server and parsed on the client; this is its cost in each lane. At the p50 cost, a "
        f"{TOKENS}-token answer streamed one token per `delta` adds about "
        f"{per_delta['inprocess:end'] * TOKENS / 1000:.0f} ms `inprocess` and "
        f"{per_delta['sidecar:end'] * TOKENS / 1000:.0f} ms `sidecar` of wall time "
        f"({per_delta['inprocess:cpu'] * TOKENS / 1000:.0f} and "
        f"{per_delta['sidecar:cpu'] * TOKENS / 1000:.0f} ms of CPU) (suggested: {TOKENS} tokens "
        "as a typical answer). A workload that batches a few tokens per `delta` divides that.",
        "",
    ]

    # 5
    engines: dict[str, Samples] = results["engines"]
    lines += [
        "## Time to first token",
        "",
        f"`{SIMPLIFY}`, time from the call to the first `delta`, and to the last event.",
        "",
        "Over the lanes, `echo_python:handle` (the same runs as the latency section):",
        "",
        "| Path | First delta p50 ms | First delta p95 ms | End p50 ms | End p95 ms |",
        "| ---- | -----------------: | -----------------: | ---------: | ---------: |",
    ]
    for name, samples in lane.items():
        f50, f95 = _p(samples.first)
        e50, e95 = _p(samples.end)
        lines.append(f"| {name} | {f50} | {f95} | {e50} | {e95} |")
    lines += [
        "",
        "Per engine, in process, each engine's own `handle` called directly (no lane), so the "
        "framework's own overhead before the first delta is visible. Each run lists the MCP tools "
        "(the stub) and makes one model call.",
        "",
        "| Engine | First delta p50 ms | First delta p95 ms | End p50 ms | End p95 ms |",
        "| ------ | -----------------: | -----------------: | ---------: | ---------: |",
    ]
    first50 = {name: percentile(samples.first, 50) for name, samples in engines.items()}
    for name, samples in engines.items():
        f50, f95 = _p(samples.first)
        e50, e95 = _p(samples.end)
        lines.append(f"| {name} | {f50} | {f95} | {e50} | {e95} |")
    lines += [
        "",
        "What it means: with a model that answers at once, the time to the first `delta` is all "
        "stack: the MCP tool listing, building the framework's client or graph, and the first "
        "chunk's mapping. Against plain Python at p50: "
        + ", ".join(
            f"{name.split(' (')[0]} {ms(first50[name] - first50[PLAIN])} ms"
            for name in engines
            if name != PLAIN
        )
        + ". In the `inprocess` lane the first delta lands with `end` (see caveats); "
        "only the `sidecar` lane streams it early. A real model's first-token time adds to all "
        "of these.",
        "",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--runs", type=int, default=200, help="measured runs per series")
    parser.add_argument("--warmup", type=int, default=10, help="unmeasured runs first")
    parser.add_argument("--out", default=None, help="the note, relative to the PoC folder")
    args = parser.parse_args(argv)
    if args.runs < 2:
        parser.error("--runs must be at least 2")
    logging.basicConfig(level=logging.WARNING)
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    load_start = os.getloadavg()
    started = time.perf_counter()
    results = asyncio.run(measure(args.runs, args.warmup, lambda m: print(m, file=sys.stderr)))
    if args.out is None:
        args.out = f"notes/{date.today().isoformat()}-measurements.md"
    took = time.perf_counter() - started
    text = render(results, args, took, (load_start, os.getloadavg()))
    out = Path(args.out)
    target = out if out.is_absolute() else POC / out
    target.write_text(text)
    print(f"wrote {target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
