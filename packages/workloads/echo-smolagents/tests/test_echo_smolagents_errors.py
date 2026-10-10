"""Error codes, the step limit, and what the code executor allows."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from echo_smolagents_support import (
    LOOKUP,
    SMOKE,
    Stubs,
    check_shape,
    handle_module,
    run,
    stub_servers,
    text_of,
    tools_module,
)
from smolagents import LocalPythonExecutor  # type: ignore[import-untyped]
from smolagents.local_python_executor import InterpreterError  # type: ignore[import-untyped]
from smolagents.utils import BASE_BUILTIN_MODULES  # type: ignore[import-untyped]


def _listing(root: Path) -> list[Path]:
    return list(root.rglob("*"))


@pytest.fixture
async def stubs(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Stubs]:
    async with stub_servers(monkeypatch) as s:
        yield s


def _only_error(events: list[dict[str, Any]]) -> dict[str, Any]:
    check_shape(events)
    assert [e["type"] for e in events] == ["start", "error"], events
    return events[1]


@pytest.mark.parametrize(
    ("text", "code", "retryable"),
    [("fail503: x", "http_503", True), ("fail401: x", "http_401", False)],
)
async def test_http_status_errors(stubs: Stubs, text: str, code: str, retryable: bool) -> None:
    error = _only_error(await run(text))
    assert error["code"] == code and error["retryable"] is retryable


def _raising(exc: Exception) -> httpx2.MockTransport:
    def fail(request: httpx2.Request) -> httpx2.Response:
        raise exc

    return httpx2.MockTransport(fail)


async def test_a_model_timeout(stubs: Stubs, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handle_module, "transport", _raising(httpx2.ReadTimeout("slow")))
    error = _only_error(await run(SMOKE))
    assert error["code"] == "timeout" and error["retryable"] is True


async def test_a_connect_error(stubs: Stubs, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handle_module, "transport", _raising(httpx2.ConnectError("refused")))
    error = _only_error(await run(SMOKE))
    assert error["code"] == "connect_error" and error["retryable"] is True


@pytest.mark.parametrize("body", [{"choices": []}, {"id": "x"}, {"choices": [{"message": {}}]}])
async def test_a_reply_that_is_not_a_chat_completion_is_bad_response(
    stubs: Stubs, monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]
) -> None:
    def reply(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, content=json.dumps(body), headers={"content-type": "application/json"}
        )

    monkeypatch.setattr(handle_module, "transport", httpx2.MockTransport(reply))
    error = _only_error(await run(SMOKE))
    assert error["code"] == "bad_response" and error["retryable"] is False


async def test_any_other_failure_is_model_error_and_handle_does_not_raise(
    stubs: Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handle_module, "transport", _raising(RuntimeError("odd")))
    error = _only_error(await run(SMOKE))
    assert error["code"] == "model_error" and "odd" in error["message"]


async def test_a_failed_tool_call_is_a_tool_error(stubs: Stubs) -> None:
    events = await run("toolfail: x")
    check_shape(events)
    assert [e["type"] for e in events] == ["start", "tool_call", "error"]
    assert events[1]["name"] == "explode" and "error" in events[1]["result"]
    assert events[2]["code"] == "tool_error" and events[2]["retryable"] is False


async def test_an_unreachable_tool_endpoint_runs_without_tools(
    stubs: Stubs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tools_module, "transport", _raising(httpx2.ConnectError("down")))
    before = tools_module.list_failures
    events = await run(SMOKE)
    check_shape(events)
    assert events[-1]["status"] == "ok" and text_of(events) == "Hello."
    assert tools_module.list_failures == before + 1
    system = stubs.bodies()[0]["messages"][0]["content"]
    assert "glossary_lookup" not in str(system)


async def test_no_final_answer_in_four_steps_is_tool_loop_exceeded(stubs: Stubs) -> None:
    error = _only_error(await run("loop: forever"))
    assert error["code"] == "tool_loop_exceeded" and error["retryable"] is False
    assert len(stubs.bodies()) == 4, "max_steps is 4 and there is no extra 'final answer' call"


async def test_three_tool_rounds_and_the_answer_fit_in_the_limit(stubs: Stubs) -> None:
    events = await run(LOOKUP)
    assert events[-1]["type"] == "end"
    assert len(stubs.bodies()) == 3


# --- the executor: what generated code may do --------------------------------------------------


def test_the_default_authorized_imports_are_these_eleven_modules() -> None:
    assert sorted(BASE_BUILTIN_MODULES) == [
        "collections", "datetime", "itertools", "math", "queue", "random", "re", "stat",
        "statistics", "time", "unicodedata",
    ]  # fmt: skip


@pytest.mark.parametrize("code", ["import os", "import subprocess", "from os import path"])
def test_the_executor_refuses_os_and_subprocess(code: str) -> None:
    executor = LocalPythonExecutor([])
    executor.send_tools({})
    with pytest.raises(InterpreterError, match="not allowed"):
        executor(code)


def test_the_executor_refuses_open_and_dunder_import_and_allows_re(tmp_path: Path) -> None:
    executor = LocalPythonExecutor([])
    executor.send_tools({})
    target = tmp_path / "f.txt"
    for code in (f"open({str(target)!r}, 'w').write('x')", "__import__('os')"):
        with pytest.raises(InterpreterError, match="Forbidden function evaluation"):
            executor(code)
    assert not target.exists()
    out = executor("import re\nprint(re.sub('a', 'b', 'aa'))")
    assert out.logs.strip() == "bb"


@pytest.mark.parametrize("task", ["hack: x", "hack-subprocess: x"])
async def test_generated_code_that_imports_os_or_subprocess_is_refused_by_the_agent(
    stubs: Stubs, task: str
) -> None:
    events = await run(task)
    check_shape(events)
    assert text_of(events) == "blocked: the import was refused"
    observation = str(stubs.bodies()[1]["messages"][-1]["content"])
    module = "os" if task.startswith("hack:") else "subprocess"
    assert f"Import of {module} is not allowed" in observation
    for allowed in BASE_BUILTIN_MODULES:
        assert allowed in observation, "the refusal lists the default allow-list"


async def test_a_run_writes_nothing_to_disk(
    stubs: Stubs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tempfile

    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(tempfile, "tempdir", str(home))
    for text in (LOOKUP, "hack: x"):
        await run(text)
    assert sorted(_listing(tmp_path)) == [home, work]


async def test_two_runs_share_no_conversation_or_variables(stubs: Stubs) -> None:
    await run(LOOKUP)
    seen = len(stubs.bodies())
    events = await run(SMOKE)
    assert text_of(events) == "Hello."
    second = stubs.bodies()[seen:]
    assert len(second) == 1
    assert "Define SLM" not in str(second[0]["messages"])
