"""The workload's log format (PoC-8, 014 H-7): one JSON object per line with the chassis's field
names, and inside an A2A request the request's `request_id` and 32-hex `trace_id`.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import re
import shutil
import sys
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from workload_a2a import cli, logs
from workload_a2a.mapping import request_to_message
from workload_a2a.server import DrainingServer, build_agent_card, build_app, build_server

CARD = build_agent_card(name="log-test", version="1", url="http://127.0.0.1:9000")
TRACE = "4bf92f3577b34da6a336f7ef8e2a8f4e"
HEADER_TRACE = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{TRACE}-00f067aa0ba902b7-01"
HEADER_TRACEPARENT = f"00-{HEADER_TRACE}-00f067aa0ba902b7-01"
CTX: dict[str, Any] = {
    "request_id": "req-log-1",
    "trace_id": "trace-1",
    "idempotency_key": "idem-1",
    "agent": "echo",
    "agent_version": "0.0.1",
    "versions": {"chassis": "0.1.0", "config": None, "prompt": None, "model_route": "r"},
    "traceparent": TRACEPARENT,
}
BUSINESS = "my_workload.logic"
TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z")


async def _logging_handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    """A workload whose business logic logs one line inside the request."""
    logging.getLogger(BUSINESS).info("working on it")
    yield {"type": "start", "request_id": ctx["request_id"]}
    yield {"type": "end"}


@pytest.fixture
def captured() -> Iterator[io.StringIO]:
    """A `JsonFormatter` handler on the root logger at INFO; restores the root and uvicorn's
    loggers afterwards, because `configure_logging` changes them."""
    root = logging.getLogger()
    named = [logging.getLogger(n) for n in ("uvicorn", "uvicorn.error", "uvicorn.access")]
    saved = (
        list(root.handlers),
        root.level,
        [(n.handlers[:], n.propagate, n.level) for n in named],
    )
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logs.JsonFormatter(agent="log-test"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        yield stream
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
        for logger, (handlers, propagate, level) in zip(named, saved[2], strict=True):
            logger.handlers[:] = handlers
            logger.propagate = propagate
            logger.setLevel(level)


def _lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


async def _send(http: httpx.AsyncClient, ctx: dict[str, Any]) -> None:
    client = await ClientFactory(ClientConfig(httpx_client=http, streaming=True)).create_from_url(
        "http://127.0.0.1:9000"
    )
    try:
        [r async for r in client.send_message(request_to_message({"text": "hi"}, ctx))]
    finally:
        await client.close()


async def _send_asgi(ctx: dict[str, Any], headers: dict[str, str] | None = None) -> None:
    transport = httpx.ASGITransport(app=build_app(_logging_handle, CARD))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:9000", headers=headers
    ) as http:
        await _send(http, ctx)


def _record(message: str, **extra: Any) -> logging.LogRecord:
    record = logging.LogRecord("my.logger", logging.WARNING, __file__, 1, message, None, None)
    record.__dict__.update(extra)
    return record


def test_a_line_is_one_json_object_with_the_chassis_field_names() -> None:
    line = logs.JsonFormatter(agent="echo").format(_record("two\nlines", route="r1"))
    assert "\n" not in line
    parsed = json.loads(line)
    assert list(parsed)[:5] == ["time", "level", "logger", "message", "agent"]
    assert TIME.fullmatch(parsed["time"])
    assert parsed["level"] == "warning"
    assert parsed["logger"] == "my.logger"
    assert parsed["message"] == "two\nlines"
    assert parsed["agent"] == "echo"
    assert parsed["route"] == "r1"


def test_outside_a_request_the_ids_are_absent() -> None:
    parsed = json.loads(logs.JsonFormatter().format(_record("idle")))
    assert "request_id" not in parsed
    assert "trace_id" not in parsed
    assert "agent" not in parsed


def test_inside_a_request_context_the_line_carries_its_ids() -> None:
    with logs.request_context(request_id="req-9", trace_id=TRACE):
        inside = json.loads(logs.JsonFormatter().format(_record("busy")))
    after = json.loads(logs.JsonFormatter().format(_record("idle")))
    assert (inside["request_id"], inside["trace_id"]) == ("req-9", TRACE)
    assert "request_id" not in after and "trace_id" not in after


def test_a_traceback_is_the_exception_field() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", None, None)
        record.exc_info = sys.exc_info()
    parsed = json.loads(logs.JsonFormatter().format(record))
    assert "ValueError: boom" in parsed["exception"]


def test_trace_id_prefers_the_header_then_ctx_traceparent_then_ctx_trace_id() -> None:
    header = {"TraceParent": HEADER_TRACEPARENT}
    assert logs.trace_id_of(header, CTX) == HEADER_TRACE
    assert logs.trace_id_of({}, CTX) == TRACE
    assert logs.trace_id_of({"traceparent": "garbage"}, CTX) == TRACE
    assert logs.trace_id_of({}, {"trace_id": HEADER_TRACE}) == HEADER_TRACE
    hashed = hashlib.sha256(b"trace-1").hexdigest()[:32]
    assert logs.trace_id_of({}, {"trace_id": "trace-1"}) == hashed
    assert logs.trace_id_of({}, {}) is None


def test_the_trace_id_fallback_matches_the_chassis() -> None:
    """A ctx with no `traceparent` gives the id the chassis logs. Imported only to compare."""
    trace = pytest.importorskip("chassis.core.trace")
    for value in ("trace-1", TRACE.upper(), TRACE):
        assert logs.trace_id_of({}, {"trace_id": value}) == trace.trace_id_hex(value)


async def test_a_line_the_workload_logs_inside_a_request_carries_its_ids(
    captured: io.StringIO,
) -> None:
    await _send_asgi(CTX)
    lines = _lines(captured)
    (business,) = [line for line in lines if line["logger"] == BUSINESS]
    assert business["request_id"] == "req-log-1"
    assert business["trace_id"] == TRACE
    (done,) = [line for line in lines if line["message"] == "handle done"]
    assert done["logger"] == "workload_a2a.server"
    assert (done["request_id"], done["trace_id"]) == ("req-log-1", TRACE)
    logging.getLogger(BUSINESS).info("after")
    assert "trace_id" not in _lines(captured)[-1]


async def test_the_traceparent_header_wins_over_ctx(captured: io.StringIO) -> None:
    await _send_asgi(CTX, headers={"traceparent": HEADER_TRACEPARENT})
    (business,) = [line for line in _lines(captured) if line["logger"] == BUSINESS]
    assert business["trace_id"] == HEADER_TRACE


async def test_two_concurrent_requests_keep_their_own_ids(captured: io.StringIO) -> None:
    other = {**CTX, "request_id": "req-log-2", "traceparent": HEADER_TRACEPARENT}
    await asyncio.gather(_send_asgi(CTX), _send_asgi(other))
    ids = {
        (line["request_id"], line["trace_id"])
        for line in _lines(captured)
        if line["logger"] == BUSINESS
    }
    assert ids == {("req-log-1", TRACE), ("req-log-2", HEADER_TRACE)}


@pytest.fixture
def uds_path() -> Iterator[str]:
    folder = Path(tempfile.mkdtemp(prefix="wl-"))
    yield str(folder / "a2a.sock")
    shutil.rmtree(folder, ignore_errors=True)


async def test_uvicorn_lines_are_json_after_configure_logging(
    captured: io.StringIO, uds_path: str
) -> None:
    stream = io.StringIO()
    logs.configure_logging(agent="log-test", level="info", stream=stream)
    server = build_server(_logging_handle, CARD, port=0, uds=uds_path, log_level="info")
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    task.result()
                    raise RuntimeError("uvicorn exited before it started")
                await asyncio.sleep(0.01)
        transport = httpx.AsyncHTTPTransport(uds=uds_path)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9000") as http:
            await _send(http, CTX)
    finally:
        server.should_exit = True
        await task
    lines = _lines(stream)
    access = [line for line in lines if line["logger"] == "uvicorn.access"]
    assert access, lines
    assert all(line["agent"] == "log-test" for line in lines)
    assert any(line["logger"] == "uvicorn.error" for line in lines)
    assert all(TIME.fullmatch(line["time"]) for line in lines)
    assert not any("color_message" in line for line in lines)


def test_configure_logging_replaces_its_own_handler(captured: io.StringIO) -> None:
    logs.configure_logging(agent="a", stream=io.StringIO())
    logs.configure_logging(agent="b", level="warning", stream=io.StringIO())
    root = logging.getLogger()
    ours = [h for h in root.handlers if isinstance(h.formatter, logs.JsonFormatter)]
    # `captured` adds one handler of its own; `configure_logging` removes it too, leaving one.
    assert [h.formatter.agent for h in ours] == ["b"]  # type: ignore[union-attr]
    assert root.level == logging.WARNING
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        assert logging.getLogger(name).handlers == []
        assert logging.getLogger(name).propagate is True


def test_the_cli_sets_up_json_logs_at_start(
    captured: io.StringIO, uds_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(DrainingServer, "run", lambda self, sockets=None: None)
    cli.main(["serve", "--handle", "echo_python:handle", "--uds", uds_path, "--name", "echo-x"])
    ours = [h for h in logging.getLogger().handlers if isinstance(h.formatter, logs.JsonFormatter)]
    assert [h.formatter.agent for h in ours] == ["echo-x"]  # type: ignore[union-attr]
    assert logging.getLogger().level == logging.INFO
