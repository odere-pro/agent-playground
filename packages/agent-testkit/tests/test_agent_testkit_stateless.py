"""The stateless check (057 H-19) in process: test agents that write a file or keep a module
global fail it, with the container named; the simplifier (`echo_python`) passes it on fresh and
reused pods in shuffled order, with the fake model server as its model. No socket, no process.
"""

from __future__ import annotations

import importlib
import sys
import textwrap
import uuid
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
from agent_testkit.stateless import (
    Pods,
    StatelessError,
    WriteGuard,
    check_kept_data,
    default_ctx,
    run_call,
)
from fake_model_server import Script, create_app

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_SCRIPT = ROOT / "packages/fake-model-server/scripts/example.yaml"
REQUESTS = [{"text": f"request {i}", "data": {}} for i in range(4)]

CLEAN = """
async def handle(input, ctx):
    yield {"type": "start", "request_id": ctx["request_id"]}
    yield {"type": "delta", "text": input["text"].upper()}
    yield {"type": "end", "status": "ok"}
"""
WRITER = """
from pathlib import Path

async def handle(input, ctx):
    yield {"type": "start", "request_id": ctx["request_id"]}
    Path(input["path"]).write_text(input["text"])
    yield {"type": "end", "status": "ok"}
"""
QUIET_WRITER = """
import os

async def handle(input, ctx):
    try:
        fd = os.open(input["path"], os.O_WRONLY | os.O_CREAT)
        os.close(fd)
    except OSError:
        pass  # the agent hides the failure; the check must still see the write
    yield {"type": "end", "status": "ok"}
"""
THREAD_WRITER = """
import threading

def _write(path):
    try:
        with open(path, "a") as f:
            f.write("x")
    except OSError:
        pass

async def handle(input, ctx):
    worker = threading.Thread(target=_write, args=(input["path"],))
    worker.start()
    worker.join()
    yield {"type": "end", "status": "ok"}
"""
KEEPER = """
last = None

async def handle(input, ctx):
    global last
    seen, last = last, input["text"]
    yield {"type": "delta", "text": f"{input['text']} (before: {seen})"}
    yield {"type": "end", "status": "ok"}
"""
CACHE = """
cache = {}

async def handle(input, ctx):
    cache[input["text"]] = len(cache)
    yield {"type": "end", "status": "ok"}
"""


@pytest.fixture
def agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """`agent(source)` writes a one-module test agent and returns its `module:handle` path."""
    folder = tmp_path / "agents"
    monkeypatch.syspath_prepend(str(folder))

    def make(source: str) -> str:
        name = f"stateless_probe_{uuid.uuid4().hex[:8]}"
        package = folder / name
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (package / "agent.py").write_text(textwrap.dedent(source))
        return f"{name}.agent:handle"

    return make


def _load(ref: str) -> Any:
    module, _, attr = ref.partition(":")
    return getattr(importlib.import_module(module), attr)


async def test_a_call_that_writes_a_file_fails_naming_the_workload_and_the_path(
    agent: Any, tmp_path: Path
) -> None:
    target = tmp_path / "kept.txt"
    handle = _load(agent(WRITER))

    with pytest.raises(StatelessError) as caught:
        await run_call(handle, {"text": "x", "path": str(target)}, default_ctx(0))

    assert f"container 'workload' wrote {target}" in str(caught.value)
    assert not target.exists(), "the write went through"


async def test_a_write_fails_naming_the_chassis_when_the_call_runs_as_the_chassis(
    agent: Any, tmp_path: Path
) -> None:
    target = tmp_path / "chassis.txt"
    handle = _load(agent(WRITER))

    with pytest.raises(StatelessError, match=f"container 'chassis' wrote {target}"):
        await run_call(
            handle, {"text": "x", "path": str(target)}, {"request_id": "r"}, container="chassis"
        )


async def test_a_write_the_agent_hides_still_fails_the_check(agent: Any, tmp_path: Path) -> None:
    target = tmp_path / "hidden.bin"
    handle = _load(agent(QUIET_WRITER))

    with pytest.raises(StatelessError, match=f"container 'workload' wrote {target} \\(open\\)"):
        await run_call(handle, {"path": str(target)}, default_ctx(0))


async def test_a_write_from_another_thread_fails_the_check(agent: Any, tmp_path: Path) -> None:
    target = tmp_path / "thread.log"
    handle = _load(agent(THREAD_WRITER))

    with pytest.raises(StatelessError, match=str(target)):
        await run_call(handle, {"path": str(target)}, default_ctx(0))
    assert not target.exists()


async def test_a_file_write_fails_the_kept_data_run_too(agent: Any, tmp_path: Path) -> None:
    target = tmp_path / "run.txt"
    pods = Pods(agent(WRITER))

    with pytest.raises(StatelessError, match=f"container 'workload' wrote {target}"):
        await check_kept_data(pods, [{"text": "x", "path": str(target)}])


def test_writes_are_allowed_again_when_the_guard_is_off(tmp_path: Path) -> None:
    with WriteGuard() as guard, pytest.raises(OSError, match="Read-only file system"):
        (tmp_path / "inside").write_text("x")
    (tmp_path / "outside").write_text("x")

    assert [w.path for w in guard.writes] == [str(tmp_path / "inside")]


async def test_an_agent_that_keeps_the_last_request_in_a_module_variable_fails(agent: Any) -> None:
    pods = Pods(agent(KEEPER))

    with pytest.raises(StatelessError) as caught:
        await check_kept_data(pods, REQUESTS)

    message = str(caught.value)
    assert "container 'workload' kept data between calls" in message
    assert "differed on the reused pod" in message
    assert f"module global {pods.handle.split(':')[0]}.last moved" in message


async def test_a_module_cache_fails_even_when_every_output_matches(agent: Any) -> None:
    pods = Pods(agent(CACHE))

    with pytest.raises(StatelessError, match=r"module global \S+\.cache moved") as caught:
        await check_kept_data(pods, REQUESTS)
    assert "differed" not in str(caught.value)


async def test_a_stateless_agent_passes_on_fresh_and_reused_pods_in_shuffled_order(
    agent: Any,
) -> None:
    pods = Pods(agent(CLEAN))

    report = await check_kept_data(pods, REQUESTS, seed=3)

    assert sorted(report.fresh_order) == sorted(report.reused_order) == list(range(4))
    assert report.reused_order != tuple(range(4)), "the seed gave the given order"
    assert [out[1]["text"] for out in report.outputs] == [f"REQUEST {i}" for i in range(4)]


async def test_the_check_puts_back_the_agent_modules_it_imported(agent: Any) -> None:
    ref = agent(CLEAN)
    package = ref.split(".")[0]
    original = _load(ref)

    await check_kept_data(Pods(ref), REQUESTS[:2])

    assert sys.modules[f"{package}.agent"].handle is original


def _simplifier_setup() -> None:
    """The fake model adapter for a fresh `echo_python`: the model on an ASGI transport, the tool
    endpoint refused (so no tools are offered)."""

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    handle_module: Any = importlib.import_module("echo_python.handle")
    tools: Any = importlib.import_module("echo_python.tools")
    handle_module.transport = httpx.ASGITransport(app=create_app(Script.from_yaml(EXAMPLE_SCRIPT)))
    tools.client_factory = lambda headers, timeout: httpx2.AsyncClient(
        transport=httpx2.MockTransport(refuse), headers=headers, timeout=timeout
    )


async def test_the_simplifier_gives_the_same_outputs_on_fresh_and_reused_pods() -> None:
    """Shuffled order, fresh pods and one reused pod, the fake model server as the model.
    `echo_python.tools.list_failures` counts failed tool listings and holds no request data
    (out of scope in 057 H-19), so it is left out of the globals compared."""
    pods = Pods("echo_python.handle:handle", setup=_simplifier_setup)
    requests = [
        {"text": "simplify: the quick brown fox", "data": {}},
        {"text": "something else", "data": {}},
        {"text": "please fail", "data": {}},
        {"text": "simplify: a second text", "data": {}},
    ]

    report = await check_kept_data(
        pods, requests, seed=1, ignore_globals=frozenset({"echo_python.tools.list_failures"})
    )

    texts = [
        "".join(e.get("text", "") for e in out if e["type"] == "delta") for out in report.outputs
    ]
    assert texts == ["Plain words. Short sentences. Same facts.", "ok", "", texts[0]]
    assert report.outputs[2][-1]["type"] == "error"
    assert report.reused_order != report.fresh_order
