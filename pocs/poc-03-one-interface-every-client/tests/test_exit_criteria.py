"""PoC-3 exit criteria: one scenario test per criterion in `README.md`, each docstring naming its
criterion word for word (skill `poc-iteration`).

Offline. The client scenarios serve the app built from `packages/chassis/configs/fake.yaml` on
uvicorn over a Unix socket (`poc03_support.serve_on_uds`), with no injected ports: the fake profile
builds them and each run goes through the real `inprocess` lane (A2A in memory) to a wire-form
`handle`. The SDKs and the MCP client speak real HTTP and real SSE to it; the gate allows `AF_UNIX`
only.

A criterion whose evidence has not landed yet is `xfail(strict=True)`, narrowed with `raises=` to
the missing file or module, so any other failure is a real one and the landing turns it into a
strict pass that the gate reports.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import importlib.util
import json
import os
import shlex
import subprocess
import sys
import tomllib
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import ModuleType
from typing import Any, get_args

import anthropic
import httpx2
import openai
import pytest
import yaml
from chassis.adapters.mcp import build_agent_mcp
from chassis.core.inbound import Interface
from chassis.fakes import InMemoryTelemetry
from fastapi import FastAPI
from fastmcp.server.providers.openapi.components import OpenAPITool
from poc03_support import (
    AGENT,
    BASE,
    GAPS_NOTE,
    ROOT,
    TESTS,
    TIMEOUT_S,
    TOOL_ANSWER,
    TOOL_CALL,
    TOOL_CALLING_WIRE,
    lane_app,
    markdown_tables,
    mcp_client,
    run_body_schema,
    serve_on_uds,
    sse_data,
    uds_http_client,
)

TEXT = "hello big world"
KEY = "placeholder-not-a-key"
"""Any placeholder: the chassis holds the credentials; an inbound key is ignored, never logged."""

SUITE_FILES = {
    "interface-contract": TESTS / "test_interface_contract.py",
    "schemathesis": TESTS / "test_openapi_props.py",
}


# --- 1 -----------------------------------------------------------------------------------------


def _recipe(makefile: str, target: str) -> list[str]:
    """The recipe lines of `target` in `makefile`."""
    lines = makefile.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{target}:"))
    recipe: list[str] = []
    for line in lines[start + 1 :]:
        if not line.startswith("\t"):
            break
        recipe.append(line.strip())
    return recipe


def _gate_flags() -> list[str]:
    """The pytest flags `scripts/check_offline.sh` passes (its `exec uv run pytest` line)."""
    script = (ROOT / "scripts/check_offline.sh").read_text()
    [line] = [line for line in script.splitlines() if line.startswith("exec uv run pytest")]
    words = shlex.split(line)
    return words[words.index("pytest") + 1 : words.index("$@")]


def _has_network_marker(path: Path) -> bool:
    """`pytest.mark.network` (or `mark.network`) appears anywhere in `path`."""
    tree = ast.parse(path.read_text())
    return any(
        isinstance(node, ast.Attribute)
        and node.attr == "network"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
        for node in ast.walk(tree)
    )


_COLLECT = r"""
import json, sys, pytest
class Seen:
    items = {}
    def pytest_collection_finish(self, session):
        for item in session.items:
            self.items[item.nodeid] = sorted({m.name for m in item.iter_markers()})
seen = Seen()
code = pytest.main(sys.argv[1:], plugins=[seen])
print("@@COLLECTED@@" + json.dumps({"code": int(code), "items": seen.items}))
"""


@pytest.fixture(scope="module")
def gate_collection() -> dict[str, list[str]]:
    """What the gate's own pytest flags collect from the suite files that exist: node id to the
    names of its markers. A child process, so this session's state does not leak in.
    """
    files = [str(path.relative_to(ROOT)) for path in SUITE_FILES.values() if path.exists()]
    env = {k: v for k, v in os.environ.items() if not k.endswith(("_API_KEY", "_TOKEN"))}
    done = subprocess.run(
        [sys.executable, "-c", _COLLECT, *_gate_flags(), "--collect-only", "-q", *files],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    [line] = [line for line in done.stdout.splitlines() if line.startswith("@@COLLECTED@@")]
    report = json.loads(line.removeprefix("@@COLLECTED@@"))
    assert report["code"] == 0, done.stdout[-3000:] + done.stderr[-3000:]
    items: dict[str, list[str]] = report["items"]
    return items


@pytest.mark.slow
@pytest.mark.parametrize(
    "suite",
    [
        "interface-contract",
        "schemathesis",
    ],
)
def test_exit_1_the_suites_run_offline_in_ci_on_every_commit(
    suite: str, gate_collection: dict[str, list[str]]
) -> None:
    """Exit criterion: "The interface contract suite and the Schemathesis tests run offline in CI
    on every commit".

    CI runs `make check` on every push and pull request; `make check` runs `make test`, which is
    `scripts/check_offline.sh` with no path, so pytest's `testpaths` (`packages`, `pocs`) decide;
    the script strips keys and passes `--disable-socket --allow-unix-socket --record-mode=none`.
    With those flags, the suite's file is collected and none of its tests is marked `network`.
    """
    path = SUITE_FILES[suite]
    if not path.exists():
        raise FileNotFoundError(path)

    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    triggers = workflow.get("on", workflow.get(True))  # YAML 1.1 reads a bare `on` as true
    assert set(triggers) >= {"push", "pull_request"}
    assert not (triggers["push"] or {}).get("branches"), "every commit, on every branch"
    runs = [step.get("run", "") for job in workflow["jobs"].values() for step in job["steps"]]
    assert "make check" in runs

    makefile = (ROOT / "Makefile").read_text()
    assert "PYTEST := scripts/check_offline.sh" in makefile
    assert "test" in _recipe(makefile, "check")[0].split()
    assert _recipe(makefile, "test") == ["$(PYTEST)"], "no path, no -m: testpaths decide"

    script = (ROOT / "scripts/check_offline.sh").read_text()
    assert "_API_KEY" in script and 'unset "$v"' in script
    flags = _gate_flags()
    assert {"--disable-socket", "--allow-unix-socket", "--record-mode=none"} <= set(flags)
    assert not any(flag.startswith(("-m", "-k", "--ignore", "--deselect")) for flag in flags)

    pytest_ini = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pytest"]
    assert "pocs" in pytest_ini["ini_options"]["testpaths"]
    assert "addopts" not in pytest_ini["ini_options"]

    relative = str(path.relative_to(ROOT))
    collected = {
        node: marks for node, marks in gate_collection.items() if node.startswith(relative)
    }
    assert collected, f"the gate collects nothing from {relative}"
    assert not [node for node, marks in collected.items() if "network" in marks]
    assert not _has_network_marker(path)


# --- 2 -----------------------------------------------------------------------------------------

ENGINES = {"echo_python", "echo_pydanticai", "echo_langgraph", "echo_typescript"}
LANES = {"inprocess", "sidecar"}
MODES = ("stream", "complete")


def _engine(label: str) -> str:
    """`echo-python`, `echo_python`, and `echo_python:handle` are the same engine."""
    return label.split(":")[0].replace("-", "_")


def _load_binding() -> ModuleType:
    path = TESTS / "test_interface_contract.py"
    if not path.exists():
        raise FileNotFoundError(path)
    spec = importlib.util.spec_from_file_location("poc03_interface_binding", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _skips(binding: Any, interface: str, engine: str, mode: str, lane: str) -> bool:
    """The suite's own rule (`_skip_cell`, which calls `pytest.skip` with the reason)."""
    label = next(e for e in binding.engine_labels if _engine(e) == engine)
    try:
        binding._skip_cell(engine=label, lane=lane, interface=interface, mode=mode)
    except pytest.skip.Exception:
        return True
    return False


def _legitimate_skip(interface: str, engine: str, mode: str, lane: str) -> bool:
    """Section 9: MCP answers once (no stream); the TypeScript echo has no `inprocess` lane."""
    mcp_stream = interface == "mcp" and mode == "stream"
    typescript_inprocess = engine == "echo_typescript" and lane == "inprocess"
    return mcp_stream or typescript_inprocess


def test_exit_2_the_contract_suite_covers_all_engines_interfaces_and_both_transports() -> None:
    """Exit criterion: "The contract suite passes for all engines and all interfaces, over both
    transports".

    Over the binding's declared labels (open note, section 9): all four interfaces, all four
    engines, and both lanes are bound; only the TypeScript echo is missing from `inprocess`; and
    of the 64 cells exactly the two legitimate groups skip (MCP x stream, 8 cells, and
    echo-typescript x inprocess, 8, one in both), 15 in all, so 49 run. That the cells pass is
    the binding's own run in the same gate.
    """
    suite = importlib.import_module("chassis_contracts.interface")
    contract = suite.InterfaceContract
    module = _load_binding()
    bindings = [
        obj
        for obj in vars(module).values()
        if isinstance(obj, type) and issubclass(obj, contract) and obj is not contract
    ]
    assert len(bindings) == 1, bindings
    binding: Any = bindings[0]

    interfaces = getattr(binding, "interface_labels", None) or suite.INTERFACES
    assert set(interfaces) == set(get_args(Interface)) == {"native", "openai", "anthropic", "mcp"}
    engines = {_engine(label) for label in binding.engine_labels}
    assert engines == ENGINES
    assert set(binding.lane_labels) == LANES
    assert {_engine(label) for label in binding.inprocess_engines} == ENGINES - {"echo_typescript"}

    cells = [(i, e, m, lane) for i in interfaces for e in engines for m in MODES for lane in LANES]
    assert len(cells) == 64
    instance = binding()
    skipped = {cell for cell in cells if _skips(instance, *cell)}
    assert skipped == {cell for cell in cells if _legitimate_skip(*cell)}
    # 8 MCP x stream (4 engines x 2 lanes) + 8 TypeScript x inprocess (4 x 2) - 1 in both.
    # Section 9 of the open note says 16, 22, and 42; its arithmetic is off.
    assert (len(skipped), len(cells) - len(skipped)) == (15, 49)


# --- 3 -----------------------------------------------------------------------------------------


async def _openai_both_ways(uds: str) -> None:
    async with openai.AsyncOpenAI(
        base_url=f"{BASE}/v1", api_key=KEY, http_client=uds_http_client(uds)
    ) as client:
        assert client.max_retries == openai.DEFAULT_MAX_RETRIES
        messages: Any = [{"role": "user", "content": TEXT}]
        completion = await client.chat.completions.create(model=AGENT, messages=messages)
        stream = await client.chat.completions.create(model=AGENT, messages=messages, stream=True)
        chunks = [chunk async for chunk in stream]
    assert completion.choices[0].message.content == TEXT
    assert completion.choices[0].finish_reason == "stop"
    assert completion.model == AGENT
    assert completion.usage is not None and completion.usage.total_tokens > 0
    pieces = [
        c.choices[0].delta.content for c in chunks if c.choices and c.choices[0].delta.content
    ]
    assert len(pieces) > 1, "streamed in more than one chunk"
    assert "".join(pieces) == TEXT
    assert chunks[-1].choices[0].finish_reason == "stop"


async def _anthropic_both_ways(uds: str) -> None:
    async with anthropic.AsyncAnthropic(
        base_url=BASE, api_key=KEY, http_client=uds_http_client(uds)
    ) as client:
        assert client.max_retries == anthropic.DEFAULT_MAX_RETRIES
        messages: Any = [{"role": "user", "content": TEXT}]
        # `max_tokens` is required by the Messages API itself, not by the chassis.
        message = await client.messages.create(model=AGENT, max_tokens=256, messages=messages)
        async with client.messages.stream(model=AGENT, max_tokens=256, messages=messages) as s:
            pieces = [text async for text in s.text_stream]
            final = await s.get_final_message()
    assert [block.type for block in message.content] == ["text"]
    assert message.content[0].type == "text" and message.content[0].text == TEXT
    assert message.stop_reason == "end_turn" and message.model == AGENT
    assert message.usage.output_tokens > 0
    assert len(pieces) > 1, "streamed in more than one text delta"
    assert "".join(pieces) == TEXT
    assert final.stop_reason == "end_turn" and final.usage.input_tokens > 0


@pytest.mark.parametrize("sdk", ["openai", "anthropic"])
async def test_exit_3_the_sdks_work_with_only_a_base_url_change(sdk: str) -> None:
    """Exit criterion: "The OpenAI and Anthropic SDKs work with only a base URL change".

    The official `openai.AsyncOpenAI` and `anthropic.AsyncAnthropic` clients, unmodified, against
    the chassis on uvicorn over a Unix socket: a complete call and a streamed call each. The only
    non-default arguments are the base URL and its transport (`http_client`, an `httpx2` client on
    the socket, because the gate has no TCP), a placeholder key, and `model` set to the agent's
    name. Retries, timeouts, and headers stay the SDK's own.
    """
    app = lane_app()
    async with asyncio.timeout(TIMEOUT_S), serve_on_uds(app) as uds:
        if sdk == "openai":
            await _openai_both_ways(uds)
        else:
            await _anthropic_both_ways(uds)


# --- 4 -----------------------------------------------------------------------------------------


async def test_exit_4_an_mcp_client_lists_the_agents_tool_and_runs_it() -> None:
    """Exit criterion: "An MCP client lists the agent's tool and runs it".

    A real `fastmcp.Client` over streamable HTTP at `/v1/mcp`, through the Unix socket: the list is
    exactly one tool, named after the agent; calling it runs the agent through the lane and
    returns the native response envelope; the run is labeled `interface: mcp`.
    """
    app = lane_app()
    async with asyncio.timeout(TIMEOUT_S), serve_on_uds(app) as uds, mcp_client(uds) as client:
        tools = await client.list_tools()
        result = await client.call_tool(AGENT, {"input": {"text": TEXT}})
        telemetry = app.state.ports.telemetry
    assert [tool.name for tool in tools] == [AGENT]
    assert result.is_error is False
    envelope = result.structured_content
    assert envelope is not None
    assert (envelope["status"], envelope["agent"]) == ("ok", AGENT)
    assert envelope["output"] == {"text": TEXT}
    assert isinstance(telemetry, InMemoryTelemetry)
    assert telemetry.counter_value("chassis.requests", agent=AGENT, interface="mcp") == 1


# --- 5 -----------------------------------------------------------------------------------------

AGENT_MCP_SOURCES = (
    ROOT / "packages/chassis/src/chassis/adapters/mcp/agent.py",
    ROOT / "packages/chassis/src/chassis/server/interfaces/mcp.py",
)
HAND_WRITTEN_TOOL_APIS = {"tool", "add_tool", "from_function", "FunctionTool", "Tool"}
"""The FastMCP ways to write a tool by hand: `@mcp.tool`, `mcp.add_tool(...)`,
`Tool.from_function(...)`, `FunctionTool(...)`, `Tool(...)`."""


def _names_used(tree: ast.AST) -> set[str]:
    return {
        node.attr if isinstance(node, ast.Attribute) else node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute | ast.Name)
    }


def _calls_from_fastapi(function: ast.FunctionDef) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"from_fastapi", "from_openapi"}
        for node in ast.walk(function)
    )


async def test_exit_5_mcp_tools_and_rest_come_from_one_openapi_spec() -> None:
    """Exit criterion: "The MCP tools and the REST API come from the same OpenAPI spec (no
    hand-written tool definitions)".

    The tool's input schema is the `/v1/run` request body schema of the app's own spec (FastMCP
    keeps `properties` and `required` and drops only the body's `title`, `description`, and
    `additionalProperties`). Change the spec and the tool follows. In the source, the agent's MCP
    server is built only by `FastMCP.from_openapi` (or `from_fastapi`) over the app's own spec,
    with no `mcp_component_fn`, and no tool is written by hand.
    """
    app = lane_app()
    async with asyncio.timeout(TIMEOUT_S), serve_on_uds(app) as uds, mcp_client(uds) as client:
        [listed] = await client.list_tools()
    body = run_body_schema(app.openapi())
    schema = listed.input_schema
    assert schema["type"] == body["type"] == "object"
    assert schema["properties"] == body["properties"]
    assert schema["required"] == body["required"]
    assert set(body) - set(schema) <= {"title", "description", "additionalProperties"}
    assert set(schema) <= set(body)

    [tool] = await app.state.agent_mcp.list_tools()
    assert isinstance(tool, OpenAPITool), type(tool)

    # The tool follows the spec: add a body property to the spec and build again.
    changed = app.openapi()
    run_ref = changed["paths"]["/v1/run"]["post"]["requestBody"]["content"]["application/json"]
    name = run_ref["schema"]["$ref"].rsplit("/", 1)[-1]
    changed = json.loads(json.dumps(changed))
    changed["components"]["schemas"][name]["properties"]["probe"] = {"type": "string"}
    other = FastAPI()
    other.openapi = lambda: changed  # type: ignore[method-assign]
    [rebuilt] = await build_agent_mcp(other, agent_name=AGENT).list_tools()
    assert rebuilt.parameters["properties"]["probe"] == {"type": "string"}

    for source in AGENT_MCP_SOURCES:
        tree = ast.parse(source.read_text())
        hand_written = _names_used(tree) & HAND_WRITTEN_TOOL_APIS
        assert not hand_written, f"{source.name}: {hand_written}"
        assert "mcp_component_fn" not in _names_used(tree)
    agent_tree = ast.parse(AGENT_MCP_SOURCES[0].read_text())
    builders = [
        node
        for node in ast.walk(agent_tree)
        if isinstance(node, ast.FunctionDef) and node.name == "build_agent_mcp"
    ]
    assert len(builders) == 1 and _calls_from_fastapi(builders[0])
    src = ROOT / "packages/chassis/src/chassis"
    callers = sorted(
        str(path.relative_to(src))
        for path in src.rglob("*.py")
        if "build_agent_mcp(" in path.read_text() and path != AGENT_MCP_SOURCES[0]
    )
    assert callers == ["server/interfaces/mcp.py"]


# --- 6 -----------------------------------------------------------------------------------------

GAPS_CHECKED = {
    "client-tools": "client tools",
    "n-above-1": "n > 1",
    "images": "images",
    "model": "model",
    "own-tool-calls": "tool calls",
    "stop-reasons": "stop reasons",
    "mcp-stream": "streaming over mcp",
    "other-endpoints": "other endpoints",
}
"""The gaps whose handling the behavior test checks, and the words their row starts with."""

HANDLED = "how it is handled"


def _gap_rows() -> list[dict[str, str]]:
    text = GAPS_NOTE.read_text()  # FileNotFoundError until the note lands
    tables = [t for t in markdown_tables(text) if t and any(HANDLED in k.lower() for k in t[0])]
    assert len(tables) == 1, "one gaps table with a 'How it is handled' column"
    return tables[0]


def test_exit_6_the_gaps_note_lists_each_gap_with_how_it_is_handled() -> None:
    """Exit criterion: "Any gaps between the formats are listed (for example tool calls or stop
    reasons that do not map one to one), with how each is handled".

    The note exists and has one table whose every row has a non-empty "How it is handled" cell,
    and it lists each gap the behavior test checks.
    """
    rows = _gap_rows()
    handled = next(k for k in rows[0] if HANDLED in k.lower())
    first = next(iter(rows[0]))
    assert len(rows) >= len(GAPS_CHECKED)
    empty = [row[first] for row in rows if not row[handled].strip()]
    assert not empty, f"rows with no handling: {empty}"
    names = [row[first].replace("`", "").lower() for row in rows]
    missing = [gap for gap in GAPS_CHECKED.values() if not any(gap in name for name in names)]
    assert not missing, f"gaps the behavior test checks but the note does not list: {missing}"


Check = Callable[[httpx2.AsyncClient, str], Awaitable[None]]
OPENAI_PATH = "/v1/chat/completions"
ANTHROPIC_PATH = "/v1/messages"


def _openai(**extra: Any) -> dict[str, Any]:
    return {"model": AGENT, "messages": [{"role": "user", "content": TEXT}], **extra}


def _anthropic(**extra: Any) -> dict[str, Any]:
    body = {"model": AGENT, "max_tokens": 256, "messages": [{"role": "user", "content": TEXT}]}
    return {**body, **extra}


async def _client_tools_are_400(http: httpx2.AsyncClient, uds: str) -> None:
    tool = {"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}
    oa = await http.post(OPENAI_PATH, json=_openai(tools=[tool]))
    an = await http.post(
        ANTHROPIC_PATH, json=_anthropic(tools=[{"name": "f", "input_schema": {"type": "object"}}])
    )
    assert oa.status_code == 400 and oa.json()["error"]["param"] == "tools", oa.text
    assert an.status_code == 400 and an.json()["error"]["type"] == "invalid_request_error"


async def _n_above_1_is_400(http: httpx2.AsyncClient, uds: str) -> None:
    res = await http.post(OPENAI_PATH, json=_openai(n=2))
    assert res.status_code == 400 and res.json()["error"]["param"] == "n", res.text


async def _images_are_400(http: httpx2.AsyncClient, uds: str) -> None:
    image_url = {"type": "image_url", "image_url": {"url": "https://example.invalid/a.png"}}
    png = {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}
    oa = await http.post(
        OPENAI_PATH, json=_openai(messages=[{"role": "user", "content": [image_url]}])
    )
    an = await http.post(
        ANTHROPIC_PATH,
        json=_anthropic(messages=[{"role": "user", "content": [{"type": "image", "source": png}]}]),
    )
    assert oa.status_code == 400 and oa.json()["error"]["code"] == "unsupported_message", oa.text
    assert an.status_code == 400 and an.json()["error"]["type"] == "invalid_request_error"


async def _wrong_model_is_404(http: httpx2.AsyncClient, uds: str) -> None:
    oa = await http.post(OPENAI_PATH, json=_openai(model="gpt-4o"))
    an = await http.post(ANTHROPIC_PATH, json=_anthropic(model="claude-sonnet-4-5"))
    assert oa.status_code == 404 and oa.json()["error"]["code"] == "model_not_found"
    assert an.status_code == 404 and an.json()["error"]["type"] == "not_found_error"


async def _own_tool_calls_are_never_client_tool_calls(http: httpx2.AsyncClient, uds: str) -> None:
    native = await http.post("/v1/run", json={"input": {"text": TEXT}})
    assert native.json()["output"]["tool_calls"][0]["name"] == TOOL_CALL["name"], "the control"
    oa = await http.post(OPENAI_PATH, json=_openai())
    oa_stream = await http.post(OPENAI_PATH, json=_openai(stream=True))
    an = await http.post(ANTHROPIC_PATH, json=_anthropic())
    an_stream = await http.post(ANTHROPIC_PATH, json=_anthropic(stream=True))
    message = oa.json()["choices"][0]["message"]
    assert message["content"] == TOOL_ANSWER and not message.get("tool_calls")
    deltas = [d["choices"][0]["delta"] for d in sse_data(oa_stream.text)[:-1] if d["choices"]]
    assert not any(delta.get("tool_calls") for delta in deltas)
    assert [block["type"] for block in an.json()["content"]] == ["text"]
    blocks = [e for e in sse_data(an_stream.text) if e["type"] == "content_block_start"]
    assert [b["content_block"]["type"] for b in blocks] == ["text"]
    assert str(TOOL_CALL["name"]) not in oa.text + oa_stream.text + an.text + an_stream.text


async def _stop_reasons_are_stop_and_end_turn(http: httpx2.AsyncClient, uds: str) -> None:
    oa = await http.post(OPENAI_PATH, json=_openai())
    oa_stream = await http.post(OPENAI_PATH, json=_openai(stream=True))
    an = await http.post(ANTHROPIC_PATH, json=_anthropic(max_tokens=1))
    an_stream = await http.post(ANTHROPIC_PATH, json=_anthropic(stream=True))
    assert oa.json()["choices"][0]["finish_reason"] == "stop"
    finishes = [d["choices"][0]["finish_reason"] for d in sse_data(oa_stream.text)[:-1]]
    assert [f for f in finishes if f] == ["stop"]
    assert an.json()["stop_reason"] == "end_turn", "never max_tokens, even at max_tokens=1"
    [delta] = [e for e in sse_data(an_stream.text) if e["type"] == "message_delta"]
    assert delta["delta"]["stop_reason"] == "end_turn"


async def _mcp_stream_is_read_as_false(http: httpx2.AsyncClient, uds: str) -> None:
    async with mcp_client(uds) as client:
        result = await client.call_tool(AGENT, {"input": {"text": TEXT}, "stream": True})
    assert result.is_error is False
    assert result.structured_content is not None, "an SSE body would not be a JSON envelope"
    assert result.structured_content["output"] == {"text": TEXT}


async def _other_endpoints_are_404(http: httpx2.AsyncClient, uds: str) -> None:
    models = await http.get("/v1/models")
    responses = await http.post("/v1/responses", json={"model": AGENT, "input": TEXT})
    count = await http.post("/v1/messages/count_tokens", json=_anthropic())
    assert (models.status_code, responses.status_code, count.status_code) == (404, 404, 404)


BEHAVIORS: dict[str, tuple[str | None, Check]] = {
    "client-tools": (None, _client_tools_are_400),
    "n-above-1": (None, _n_above_1_is_400),
    "images": (None, _images_are_400),
    "model": (None, _wrong_model_is_404),
    "own-tool-calls": (TOOL_CALLING_WIRE, _own_tool_calls_are_never_client_tool_calls),
    "stop-reasons": (None, _stop_reasons_are_stop_and_end_turn),
    "mcp-stream": (None, _mcp_stream_is_read_as_false),
    "other-endpoints": (None, _other_endpoints_are_404),
}
assert set(BEHAVIORS) == set(GAPS_CHECKED)


@pytest.mark.parametrize("gap", list(BEHAVIORS))
async def test_exit_6_the_handled_gaps_behave_as_listed(gap: str) -> None:
    """Exit criterion: "Any gaps between the formats are listed (for example tool calls or stop
    reasons that do not map one to one), with how each is handled".

    The handling the gaps table gives (open note, section 4) is true of the running app: client
    tools, `n > 1`, and images are refused with 400; a `model` that is not the agent is 404; the
    agent's own tool calls never appear as client tool calls; stop reasons are only `stop` and
    `end_turn`; MCP reads `stream` as false; the other endpoints are 404.
    """
    handle, check = BEHAVIORS[gap]
    app = lane_app(handle) if handle else lane_app()
    async with (
        asyncio.timeout(TIMEOUT_S),
        serve_on_uds(app) as uds,
        uds_http_client(uds, base_url=BASE) as http,
    ):
        await check(http, uds)
