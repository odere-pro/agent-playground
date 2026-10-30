"""PoC-2 tools: `glossary_lookup` is defined once in the chassis, served over MCP, and works on
each of the three Python engines.

Each test names the exit criterion in docs/planning/poc/002-PoC-2-two-engines-one-contract.md it
covers. No network: MCP is spoken as plain JSON-RPC over ASGI, and each engine's outbound calls
(model and MCP) are routed into the chassis app (`poc_harness.outbound.route_outbound`).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
from pathlib import Path

import pytest
from chassis.ports.bundle import PortBundle
from poc_harness.apps import chassis_app, client_for, proxy_for, running
from poc_harness.mcp_client import glossary_over_mcp
from poc_harness.outbound import route_outbound, tool_list_failures
from poc_harness.paths import ROOT
from poc_harness.scenario import AFTER_TOOL, TIMEOUT_S, TOOL_PROMPT, engine_params
from poc_harness.scripted_model import ToolLoopModel
from poc_harness.sse import sse_frames

CHASSIS_SRC = ROOT / "packages/chassis/src/chassis"
WORKLOADS = ROOT / "packages/workloads"
SOURCE_SUFFIXES = (".py", ".ts", ".js", ".mjs")
TOOL_DECORATOR = re.compile(r"^\s*@(?:\w+\.)*(?:tool|function_tool|tool_plain)\b", re.MULTILINE)
"""A framework's own tool decorator: `@tool`, `@function_tool`, `@agent.tool`, `@mcp.tool()`."""
TOOL_NAME = re.compile(r"""["']glossary_lookup["']|\bdef glossary_lookup\b""")
TERM_SCHEMA = re.compile(r"""["']term["']\s*:|\bterm\s*:\s*str\b""")
"""A `term` parameter, as a JSON Schema property or a typed Python argument."""
MCP_LISTING = re.compile(r"\.list_tools\(|\bMCPToolset\(")
"""Tools listed from the MCP endpoint: a `list_tools` call on an MCP session, or PydanticAI's
`MCPToolset`, which lists the server's tools itself (the pydantic-ai workload has no
`list_tools` call in its own source)."""
PYTHON_WORKLOADS = ("echo-python", "echo-pydanticai", "echo-langgraph")


def _workload_sources() -> list[Path]:
    return [
        p
        for p in sorted(WORKLOADS.rglob("*"))
        if p.suffix in SOURCE_SUFFIXES
        and "node_modules" not in p.parts
        and "dist" not in p.parts
        and "tests" not in p.parts
        and "test" not in p.parts
    ]


def test_no_workload_defines_its_own_tool() -> None:
    """Exit criterion: "The tool is defined once, served by the chassis over MCP, and works on all
    three engines."

    Proves "defined once": no workload defines a tool itself; each lists them from the chassis.
    """
    from chassis.fakes.tool import GLOSSARY

    # Wrapping the tools `tools/list` returns (echo-langgraph's `StructuredTool`) is allowed:
    # their name, description, and schema still come from the listing.
    glossary_text = tuple(GLOSSARY.values())
    offenders: list[str] = []
    for path in _workload_sources():
        text = path.read_text()
        where = str(path.relative_to(ROOT))
        if TOOL_NAME.search(text) and (
            TERM_SCHEMA.search(text) or any(line in text for line in glossary_text)
        ):
            offenders.append(f"{where}: defines glossary_lookup (its name and schema or glossary)")
        offenders.extend(f"{where}: {m.group(0).strip()}" for m in TOOL_DECORATOR.finditer(text))
    for workload in PYTHON_WORKLOADS:
        src = "\n".join(p.read_text() for p in sorted((WORKLOADS / workload / "src").rglob("*.py")))
        if not MCP_LISTING.search(src):
            offenders.append(f"{workload}: does not list its tools from the MCP endpoint")
    assert offenders == []


def test_the_tool_is_defined_once_in_the_chassis() -> None:
    """Exit criterion: "The tool is defined once, served by the chassis over MCP, and works on all
    three engines."

    Proves "defined once": exactly one chassis source file names the tool, the fake `ToolPort`.
    """
    from chassis.fakes.tool import InMemoryTools, default_tools
    from chassis.ports.tool import ToolPort

    assert ToolPort is not None
    assert isinstance(default_tools(), InMemoryTools)
    assert "tools" in {f.name for f in dataclasses.fields(PortBundle)}
    naming = sorted(
        str(p.relative_to(ROOT))
        for p in CHASSIS_SRC.rglob("*.py")
        if "glossary_lookup" in p.read_text()
    )
    assert naming == ["packages/chassis/src/chassis/fakes/tool.py"], naming


async def test_the_tool_is_served_by_the_chassis_over_mcp() -> None:
    """Exit criterion: "The tool is defined once, served by the chassis over MCP, and works on all
    three engines."

    Proves "served by the chassis over MCP": a client on the proxy app's `/mcp` lists and calls it.
    """
    from chassis.fakes.tool import default_tools

    app = chassis_app("echo_python:handle", tools=default_tools())
    tool_app = proxy_for(app)
    async with asyncio.timeout(TIMEOUT_S), running(app):
        listing, answer = await glossary_over_mcp(tool_app)
        async with client_for(app) as client:
            assert (await client.post("/mcp", json={})).status_code == 404
    assert listing.get("annotations", {}).get("readOnlyHint") is True, listing
    assert answer.strip(), "glossary_lookup answered nothing"


@pytest.mark.parametrize("engine", engine_params())
async def test_the_tool_works_on_each_engine(engine: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit criterion: the tool is defined once, served by the chassis over MCP, and works on all
    three engines. The model is offered `glossary_lookup` (the engine listed it over MCP), asks
    for it, the engine calls it through the chassis's `/mcp`, the answer reaches the model as a
    tool result, and the run reports the tool call and the model's final text.

    The same checks for every engine: the streamed `tool_call` event names `glossary_lookup` and
    carries the chassis's own result (the MCP answer, parsed); the second model request holds a
    `tool` message whose content contains the definition text, in whatever JSON framing the
    framework uses (PydanticAI sends compact JSON, the others the MCP text as is). The engine's
    MCP listing did not fail: a swallowed failure would run it without tools.
    """
    from chassis.fakes.tool import default_tools

    model = ToolLoopModel()
    app = chassis_app(engine, model=model, tools=default_tools())
    outbound = route_outbound(monkeypatch, app)
    failures = tool_list_failures()
    body = {"input": {"text": TOOL_PROMPT}}
    async with (
        asyncio.timeout(TIMEOUT_S),
        running(app),
        client_for(app) as client,
    ):
        _, answer = await glossary_over_mcp(outbound.app)
        outbound.calls.clear()
        streamed = await client.post("/v1/run", json={**body, "stream": True})
    assert tool_list_failures() == failures, f"{engine}: the MCP listing failed and was swallowed"
    expected = json.loads(answer)
    definition = expected["definition"]
    assert isinstance(definition, str) and definition, expected
    frames = sse_frames(streamed.text)
    out = frames[-1][1]
    assert frames[-1][0] == "response" and out["status"] == "ok", out
    assert out["output"]["text"] == AFTER_TOOL
    tool_events = [data for name, data in frames if name == "tool_call"]
    assert [(e["name"], e["result"]) for e in tool_events] == [("glossary_lookup", expected)]
    assert any("glossary_lookup" in offered for _, offered in model.calls), model.calls
    assert len(model.calls) >= 2, "the model was not called again with the tool result"
    second_request, _ = model.calls[1]
    tool_messages = [m.content or "" for m in second_request if m.role == "tool"]
    assert any(definition in content for content in tool_messages), tool_messages
    mcp_bodies = [json.loads(c.body) for c in outbound.to("/mcp") if c.body]
    assert any(
        b.get("method") == "tools/call" and b["params"]["name"] == "glossary_lookup"
        for b in mcp_bodies
    ), mcp_bodies
