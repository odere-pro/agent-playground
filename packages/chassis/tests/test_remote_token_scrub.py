"""The remote's own bearer token never leaves the `remote` connector in an event or a log line.
An untrusted remote can read the token from its environment and print it. The connector scrubs
the exact token from every event it yields, in chassis mode and in plain-A2A mode. Offline: the
servers are the in-process stubs over a Unix socket.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from a2a_uds import serve_uds
from chassis.adapters.a2a.plain import redact
from chassis.adapters.a2a.remote import RemoteConnector
from chassis.adapters.a2a.server import build_agent_card, build_app
from chassis.core.events import Delta, End, Error, Event, ToolCall
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis_contracts.helpers import make_context, make_request
from plain_a2a_stub import PlainStub, S, artifact, status, stub_app, task
from test_remote_connector import REMOTE_URL, RequireBearer

TOKEN_ENV = "POC06_TEST_SCRUB_TOKEN"
CLEAN = "nothing secret here"


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    value = secrets.token_hex(32)
    monkeypatch.setenv(TOKEN_ENV, value)
    yield value


def _config(path: str, **extra: Any) -> dict[str, Any]:
    return {
        "connector": "remote",
        "url": REMOTE_URL,
        "auth": {"scheme": "bearer", "token_env": TOKEN_ENV, "previous_token_env": None},
        "uds": path,
        **extra,
    }


async def _run(path: str, **extra: Any) -> tuple[list[Event], InMemoryTelemetry, RemoteConnector]:
    connector = RemoteConnector()
    ports = PortBundle(
        model=ScriptedModel(),
        engine=connector,
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    await connector.setup(_config(path, **extra), ports)
    request = make_request()
    try:
        async with asyncio.timeout(10):
            events = [e async for e in connector.run(request, make_context(request))]
    finally:
        await connector.close()
    assert isinstance(ports.telemetry, InMemoryTelemetry)
    return events, ports.telemetry, connector


def _dump(events: list[Event]) -> str:
    return json.dumps([e.model_dump(mode="json") for e in events])


def _chassis_handle(token: str, last: dict[str, Any]) -> Any:
    async def handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
        yield {"type": "start", "request_id": ctx["request_id"]}
        yield {"type": "delta", "text": CLEAN}
        yield {"type": "delta", "text": f"my token is {token}, really"}
        yield {
            "type": "tool_call",
            "call_id": "c1",
            "name": f"look-{token}",
            "arguments": {f"k-{token}": f"v-{token}"},
            "result": {"rows": [{"a": 1, "b": [f"x {token} y", 2]}], "n": 3},
        }
        yield last

    return handle


async def test_chassis_mode_scrubs_delta_tool_call_and_error(token: str) -> None:
    error = {"type": "error", "code": "boom", "message": f"failed with {token}"}
    app = build_app(
        _chassis_handle(token, error), build_agent_card(name="r", version="1", url=REMOTE_URL)
    )
    async with serve_uds(RequireBearer(app, token)) as path:
        events, _, _ = await _run(path)
    assert token not in _dump(events)
    deltas = [e for e in events if isinstance(e, Delta)]
    assert [d.text for d in deltas] == [CLEAN, "my token is [redacted], really"]
    call = next(e for e in events if isinstance(e, ToolCall))
    assert call.name == "look-[redacted]"
    assert call.arguments == {"k-[redacted]": "v-[redacted]"}
    assert call.result == {"rows": [{"a": 1, "b": ["x [redacted] y", 2]}], "n": 3}
    last = events[-1]
    assert isinstance(last, Error) and last.message == "failed with [redacted]"
    assert last.code == "boom"


async def test_chassis_mode_scrubs_the_end_output(token: str) -> None:
    end = {"type": "end", "status": "ok", "output": {"text": f"it is {token}"}}
    app = build_app(
        _chassis_handle(token, end), build_agent_card(name="r", version="1", url=REMOTE_URL)
    )
    async with serve_uds(RequireBearer(app, token)) as path:
        events, _, _ = await _run(path)
    assert token not in _dump(events)
    assert events[-1] == End(status="ok", output={"text": "it is [redacted]"})


async def test_plain_mode_scrubs_the_deltas_and_the_output(token: str) -> None:
    stub = PlainStub(
        lambda _: [
            task(S.TASK_STATE_SUBMITTED),
            artifact(CLEAN),
            artifact(f" leak {token} ", append=True),
            status(S.TASK_STATE_COMPLETED),
        ]
    )
    async with serve_uds(RequireBearer(stub_app(stub), token)) as path:
        events, _, _ = await _run(path, protocol="a2a", a2a={})
    assert token not in _dump(events)
    assert [e.text for e in events if isinstance(e, Delta)] == [CLEAN, " leak [redacted] "]


async def test_plain_mode_scrubs_the_snapshot_output(token: str) -> None:
    stub = PlainStub(
        lambda _: [
            task(S.TASK_STATE_SUBMITTED),
            artifact(f"snapshot {token}", last_chunk=True),
            status(S.TASK_STATE_COMPLETED),
        ]
    )
    async with serve_uds(RequireBearer(stub_app(stub), token)) as path:
        events, _, _ = await _run(path, protocol="a2a", a2a={})
    assert token not in _dump(events)
    assert events[-1] == End(status="ok", output={"text": "snapshot [redacted]"})


async def test_plain_mode_log_of_the_remote_text_has_no_token(token: str) -> None:
    stub = PlainStub(
        lambda _: [
            task(S.TASK_STATE_SUBMITTED),
            status(S.TASK_STATE_FAILED, text=f"denied for {token}"),
        ]
    )
    async with serve_uds(RequireBearer(stub_app(stub), token)) as path:
        events, telemetry, _ = await _run(path, protocol="a2a", a2a={})
    assert token not in _dump(events)
    logged = [e for e in telemetry.logs if e["message"] == "plain a2a remote failed"]
    assert logged and "denied for [redacted]" in logged[0]["remote_text"]
    assert token not in json.dumps(telemetry.logs, default=str)


async def test_an_event_without_the_token_passes_unchanged(token: str) -> None:
    app = build_app(
        _chassis_handle(token, {"type": "end"}),
        build_agent_card(name="r", version="1", url=REMOTE_URL),
    )
    async with serve_uds(RequireBearer(app, token)) as path:
        events, _, _ = await _run(path)
    assert events[1] == Delta(text=CLEAN)
    assert events[-1] == End()


async def test_repr_does_not_show_the_token(token: str) -> None:
    app = build_app(
        _chassis_handle(token, {"type": "end"}),
        build_agent_card(name="r", version="1", url=REMOTE_URL),
    )
    async with serve_uds(RequireBearer(app, token)) as path:
        _, _, connector = await _run(path)
    assert token not in repr(connector)
    assert "RemoteConnector(url=" in repr(connector)


def test_redact_removes_a_short_exact_secret_that_the_shapes_miss() -> None:
    short = "hunter2"  # pragma: allowlist secret
    assert short in redact(f"pw is {short}")
    assert redact(f"pw is {short}", secret=short) == "pw is [redacted]"
