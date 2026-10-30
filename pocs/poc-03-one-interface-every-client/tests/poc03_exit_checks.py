"""Helpers for `test_exit_criteria.py`. Not a test module.

`poc03_support.py` holds what the exit criteria and the Schemathesis tests share: the apps, the
clients, and the spec readers. This module holds what only the exit criteria read: the gate's
files, the contract binding, the SDK calls, the agent's MCP sources, the gaps note, and the checks
of the handled gaps.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import os
import shlex
import subprocess
import sys
import tomllib
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import anthropic
import httpx2
import openai
import pytest
import yaml
from poc03_support import (
    AGENT,
    BASE,
    ECHO_WIRE,
    GAPS_NOTE,
    ROOT,
    TESTS,
    TOOL_ANSWER,
    TOOL_CALL,
    TOOL_CALLING_WIRE,
    markdown_tables,
    mcp_client,
    uds_http_client,
)
from poc_harness.sse import sse_data

TEXT = "hello big world"
KEY = "placeholder-not-a-key"
"""Any placeholder: the chassis holds the credentials; an inbound key is ignored, never logged."""


# --- 1: the gate -------------------------------------------------------------------------------

SUITE_FILES = {
    "interface-contract": TESTS / "test_interface_contract.py",
    "schemathesis": TESTS / "test_openapi_props.py",
}
CI_WORKFLOW = ROOT / ".github/workflows/ci.yml"
MAKEFILE = ROOT / "Makefile"
OFFLINE_SCRIPT = ROOT / "scripts/check_offline.sh"
ROOT_PYPROJECT = ROOT / "pyproject.toml"


def ci_triggers() -> dict[str, Any]:
    """The workflow's `on` table. YAML 1.1 reads a bare `on` key as true."""
    workflow = yaml.safe_load(CI_WORKFLOW.read_text())
    triggers: dict[str, Any] = workflow.get("on", workflow.get(True))
    return triggers


def ci_run_steps() -> list[str]:
    """The `run` line of every step of every CI job."""
    workflow = yaml.safe_load(CI_WORKFLOW.read_text())
    return [step.get("run", "") for job in workflow["jobs"].values() for step in job["steps"]]


def recipe(makefile: str, target: str) -> list[str]:
    """The recipe lines of `target` in `makefile`."""
    lines = makefile.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{target}:"))
    found: list[str] = []
    for line in lines[start + 1 :]:
        if not line.startswith("\t"):
            break
        found.append(line.strip())
    return found


def gate_flags() -> list[str]:
    """The pytest flags `scripts/check_offline.sh` passes (its `exec uv run pytest` line)."""
    [line] = [
        line
        for line in OFFLINE_SCRIPT.read_text().splitlines()
        if line.startswith("exec uv run pytest")
    ]
    words = shlex.split(line)
    return words[words.index("pytest") + 1 : words.index("$@")]


def pytest_options() -> dict[str, Any]:
    """`[tool.pytest.ini_options]` in the root `pyproject.toml`."""
    options: dict[str, Any] = tomllib.loads(ROOT_PYPROJECT.read_text())["tool"]["pytest"][
        "ini_options"
    ]
    return options


def has_network_marker(path: Path) -> bool:
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


def collect_with_gate_flags() -> dict[str, list[str]]:
    """What the gate's own pytest flags collect from the suite files that exist: node id to the
    names of its markers. A child process, so this session's state does not leak in.
    """
    files = [str(path.relative_to(ROOT)) for path in SUITE_FILES.values() if path.exists()]
    env = {k: v for k, v in os.environ.items() if not k.endswith(("_API_KEY", "_TOKEN"))}
    done = subprocess.run(
        [sys.executable, "-c", _COLLECT, *gate_flags(), "--collect-only", "-q", *files],
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


# --- 2: the contract binding -------------------------------------------------------------------

ENGINES = {"echo_python", "echo_pydanticai", "echo_langgraph", "echo_typescript"}
LANES = {"inprocess", "sidecar"}
MODES = ("stream", "complete")


def engine_name(label: str) -> str:
    """`echo-python`, `echo_python`, and `echo_python:handle` are the same engine."""
    return label.split(":")[0].replace("-", "_")


def _load_binding_module() -> ModuleType:
    path = TESTS / "test_interface_contract.py"
    spec = importlib.util.spec_from_file_location("poc03_interface_binding", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def contract_binding() -> Any:
    """The one `InterfaceContract` subclass this PoC binds."""
    contract = importlib.import_module("chassis_contracts.interface").InterfaceContract
    bindings = [
        obj
        for obj in vars(_load_binding_module()).values()
        if isinstance(obj, type) and issubclass(obj, contract) and obj is not contract
    ]
    assert len(bindings) == 1, bindings
    return bindings[0]


def bound_interfaces(binding: Any) -> list[str]:
    """The interfaces the binding runs: its own labels, else the suite's."""
    suite = importlib.import_module("chassis_contracts.interface")
    return list(getattr(binding, "interface_labels", None) or suite.INTERFACES)


def contract_cells(binding: Any) -> list[tuple[str, str, str, str]]:
    """Every interface x engine x mode x lane the binding declares."""
    engines = {engine_name(label) for label in binding.engine_labels}
    return [
        (interface, engine, mode, lane)
        for interface in bound_interfaces(binding)
        for engine in engines
        for mode in MODES
        for lane in LANES
    ]


def skips(binding: Any, interface: str, engine: str, mode: str, lane: str) -> bool:
    """The suite's own rule (`_skip_cell`, which calls `pytest.skip` with the reason)."""
    label = next(e for e in binding.engine_labels if engine_name(e) == engine)
    try:
        binding._skip_cell(engine=label, lane=lane, interface=interface, mode=mode)
    except pytest.skip.Exception:
        return True
    return False


def legitimate_skip(interface: str, engine: str, mode: str, lane: str) -> bool:
    """Section 9: MCP answers once (no stream); the TypeScript echo has no `inprocess` lane."""
    mcp_stream = interface == "mcp" and mode == "stream"
    typescript_inprocess = engine == "echo_typescript" and lane == "inprocess"
    return mcp_stream or typescript_inprocess


# --- 3: the SDK calls --------------------------------------------------------------------------


async def openai_both_ways(uds: str) -> None:
    """The unmodified OpenAI SDK, one complete call and one streamed call."""
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


async def anthropic_both_ways(uds: str) -> None:
    """The unmodified Anthropic SDK, one complete call and one streamed call."""
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


# --- 5: the agent's MCP sources ----------------------------------------------------------------

CHASSIS_SRC = ROOT / "packages/chassis/src/chassis"
AGENT_MCP_BUILDER = CHASSIS_SRC / "adapters/mcp/agent.py"
AGENT_MCP_SOURCES = (AGENT_MCP_BUILDER, CHASSIS_SRC / "server/interfaces/mcp.py")
HAND_WRITTEN_TOOL_APIS = {"tool", "add_tool", "from_function", "FunctionTool", "Tool"}
"""The FastMCP ways to write a tool by hand: `@mcp.tool`, `mcp.add_tool(...)`,
`Tool.from_function(...)`, `FunctionTool(...)`, `Tool(...)`."""


def names_used(source: Path) -> set[str]:
    """Every name and attribute `source` mentions."""
    return {
        node.attr if isinstance(node, ast.Attribute) else node.id
        for node in ast.walk(ast.parse(source.read_text()))
        if isinstance(node, ast.Attribute | ast.Name)
    }


def _calls_an_openapi_builder(function: ast.FunctionDef) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"from_fastapi", "from_openapi"}
        for node in ast.walk(function)
    )


def agent_mcp_builders_on_the_spec() -> list[bool]:
    """For each `build_agent_mcp` in the source, whether it builds from an OpenAPI spec."""
    return [
        _calls_an_openapi_builder(node)
        for node in ast.walk(ast.parse(AGENT_MCP_BUILDER.read_text()))
        if isinstance(node, ast.FunctionDef) and node.name == "build_agent_mcp"
    ]


def agent_mcp_callers() -> list[str]:
    """The chassis files, other than the builder's own, that call `build_agent_mcp`."""
    return sorted(
        str(path.relative_to(CHASSIS_SRC))
        for path in CHASSIS_SRC.rglob("*.py")
        if "build_agent_mcp(" in path.read_text() and path != AGENT_MCP_BUILDER
    )


def spec_with_run_property(
    spec: Mapping[str, Any], name: str, schema: dict[str, Any]
) -> dict[str, Any]:
    """A copy of `spec` whose `/v1/run` body has one more property."""
    run_body = spec["paths"]["/v1/run"]["post"]["requestBody"]["content"]["application/json"]
    component = run_body["schema"]["$ref"].rsplit("/", 1)[-1]
    changed: dict[str, Any] = json.loads(json.dumps(spec))
    changed["components"]["schemas"][component]["properties"][name] = schema
    return changed


# --- 6: the gaps -------------------------------------------------------------------------------

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


def gap_rows() -> list[dict[str, str]]:
    """The rows of the one table in the note with a "How it is handled" column."""
    text = GAPS_NOTE.read_text()
    tables = [t for t in markdown_tables(text) if t and any(HANDLED in k.lower() for k in t[0])]
    assert len(tables) == 1, "one gaps table with a 'How it is handled' column"
    return tables[0]


def gaps_with_no_handling(rows: list[dict[str, str]]) -> list[str]:
    """The name of each row with an empty "How it is handled" cell."""
    handled = next(k for k in rows[0] if HANDLED in k.lower())
    first = next(iter(rows[0]))
    return [row[first] for row in rows if not row[handled].strip()]


def gap_names(rows: list[dict[str, str]]) -> list[str]:
    """The first cell of each row, in lower case, with no backticks."""
    first = next(iter(rows[0]))
    return [row[first].replace("`", "").lower() for row in rows]


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


BEHAVIORS: dict[str, tuple[str, Check]] = {
    "client-tools": (ECHO_WIRE, _client_tools_are_400),
    "n-above-1": (ECHO_WIRE, _n_above_1_is_400),
    "images": (ECHO_WIRE, _images_are_400),
    "model": (ECHO_WIRE, _wrong_model_is_404),
    "own-tool-calls": (TOOL_CALLING_WIRE, _own_tool_calls_are_never_client_tool_calls),
    "stop-reasons": (ECHO_WIRE, _stop_reasons_are_stop_and_end_turn),
    "mcp-stream": (ECHO_WIRE, _mcp_stream_is_read_as_false),
    "other-endpoints": (ECHO_WIRE, _other_endpoints_are_404),
}
"""Per gap, the workload it runs on and the check of its handling."""
assert set(BEHAVIORS) == set(GAPS_CHECKED)
