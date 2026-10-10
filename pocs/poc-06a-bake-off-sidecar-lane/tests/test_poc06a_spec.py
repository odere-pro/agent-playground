"""PoC-6a, exit criterion 1 (a Python workload runs offline against the fake model server) and
the benchmark kit (criterion 3): the three tasks, smoke, simplifier, and lookup, on the baseline
engine `echo_python:handle`, with `scripts/bakeoff.yaml` as the model and the two-tool stub as the
tools. Also checks the pass checks themselves, so a wrong answer fails. No socket, no key.
"""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import httpx2
import pytest
from fake_model_server import Script, create_app
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from poc06_harness import (
    BAKEOFF_SCRIPT,
    CHECKS,
    ENGINES,
    LOOKUP,
    MODEL_URL,
    SIMPLIFIER,
    SMOKE,
    TASKS,
    TOOL_NAMES,
    TOOL_URL,
    Task,
    ToolStub,
    check_lookup,
    check_simplifier,
    check_smoke,
    collect,
    engine_names,
    two_tool_stub,
)

ECHO_PYTHON = ENGINES["echo-python"]


@pytest.fixture(autouse=True)
def offline_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """echo-python's model goes to the fake model server on the bakeoff script, over ASGI."""
    handle_module: Any = importlib.import_module("echo_python.handle")
    model_app = create_app(Script.from_yaml(BAKEOFF_SCRIPT))
    monkeypatch.setattr(handle_module, "transport", httpx.ASGITransport(app=model_app))
    monkeypatch.setenv("CHASSIS_MODEL_URL", MODEL_URL)
    monkeypatch.setenv("CHASSIS_TOOL_URL", TOOL_URL)


@asynccontextmanager
async def tools_on(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[ToolStub]:
    """The two-tool stub, installed as echo-python's MCP client. Entered inside the test, so the
    server's lifespan starts and stops in the test's own task."""
    tools_module: Any = importlib.import_module("echo_python.tools")
    async with two_tool_stub() as tool_stub:
        monkeypatch.setattr(tools_module, "client_factory", tool_stub.client_factory)
        yield tool_stub


@pytest.mark.parametrize("task", TASKS, ids=[t.name for t in TASKS])
async def test_echo_python_passes_each_task(task: Task, monkeypatch: pytest.MonkeyPatch) -> None:
    async with tools_on(monkeypatch) as stub:
        events = await collect(ECHO_PYTHON.handle, task.text)
    verdict = CHECKS[task.name](events)
    assert verdict.passed, verdict.problems
    assert [c[0] for c in stub.calls] == [s.name for s in task.tool_calls]


async def test_the_lookup_task_calls_both_tools_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    async with tools_on(monkeypatch) as stub:
        events = await collect(ECHO_PYTHON.handle, LOOKUP.text)
    calls = [(e["name"], e["arguments"]) for e in events if e["type"] == "tool_call"]
    assert calls == [("glossary_lookup", {"term": "SLM"}), ("acronym_expand", {"acronym": "RAG"})]
    assert stub.calls == calls
    assert check_lookup(events).passed


async def test_the_stub_serves_both_tools() -> None:
    def factory(**kwargs: Any) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=stub.transport(), **kwargs)

    async with two_tool_stub() as stub:
        transport = StreamableHttpTransport(
            TOOL_URL,
            httpx_client_factory=factory,  # type: ignore[arg-type]
        )
        async with Client(transport) as client:
            listed = {t.name for t in await client.list_tools()}
    assert set(TOOL_NAMES) <= listed


# --- The pass checks catch a wrong run ---


def _good(task: Task) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [{"schema_version": "0", "type": "start", "request_id": "r"}]
    events.extend(
        {
            "schema_version": "0",
            "type": "tool_call",
            "call_id": f"c-{step.name}",
            "name": step.name,
            "arguments": dict(step.arguments),
            "result": {step.result_key: step.result_contains},
        }
        for step in task.tool_calls
    )
    events.append(
        {"schema_version": "0", "type": "delta", "text": task.exact_text or " ".join(task.facts)}
    )
    events.append({"schema_version": "0", "type": "end", "status": "ok"})
    return events


def test_the_checks_accept_a_good_run() -> None:
    assert check_smoke(_good(SMOKE)).passed
    assert check_simplifier(_good(SIMPLIFIER)).passed
    assert check_lookup(_good(LOOKUP)).passed


def test_the_checks_reject_a_missing_fact_a_missing_tool_and_an_error() -> None:
    wrong_text = _good(SMOKE)
    wrong_text[1] = {"schema_version": "0", "type": "delta", "text": "Goodbye."}
    assert not check_smoke(wrong_text).passed

    no_acronym = [e for e in _good(LOOKUP) if e.get("name") != "acronym_expand"]
    assert any("tool calls" in p for p in check_lookup(no_acronym).problems)

    failed: list[dict[str, Any]] = [
        {"schema_version": "0", "type": "start", "request_id": "r"},
        {
            "schema_version": "0",
            "type": "error",
            "code": "not_implemented",
            "message": "m",
            "retryable": False,
        },
    ]
    verdict = check_simplifier(failed)
    assert not verdict.passed
    assert any("not_implemented" in p for p in verdict.problems)


def test_the_registry_follows_the_trust_rule() -> None:
    assert list(ENGINES) == [
        "echo-python",
        "echo-pydanticai",
        "echo-langgraph",
        "echo-openai-agents",
        "echo-typescript",
        "echo-claude-agent",
        "echo-smolagents",
    ]
    assert set(engine_names(trust="untrusted")) == {"echo-claude-agent", "echo-smolagents"}
    assert set(engine_names(trust="untrusted")) == set(engine_names(lane="remote"))
    assert ENGINES["echo-typescript"].handle == "node"
    assert ENGINES["echo-pydanticai"].model_hook == "echo_pydanticai.handle.model_transport"
