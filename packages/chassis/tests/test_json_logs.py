"""PoC-8 (014 H-7): every chassis log line is one JSON object.

It carries `time`, `level`, `logger`, `message`, and `agent`; inside a run, `request_id` and the
32-hex `trace_id` too. Messages and fields pass the chassis's regex redaction. Offline.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import httpx
import pytest
import uvicorn
from chassis.adapters.otel import OtelTelemetry
from chassis.core.envelope import Context, TaskInput
from chassis.core.events import Delta, End, Event, Start
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.logs import JsonFormatter, configure_logging, run_context
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, cli, create_app
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

CONFIG: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}
HEX32 = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def lines() -> Iterator[Callable[[], list[dict[str, Any]]]]:
    """A reader of the JSON lines written so far to a root handler with the chassis formatter."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(agent="echo"))
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield lambda: _parse(stream)
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)


def _parse(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def _format(record: logging.LogRecord) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(JsonFormatter(agent="echo").format(record))
    return parsed


def _record(message: str, *args: Any, **extra: Any) -> logging.LogRecord:
    record = logging.LogRecord("chassis.test", logging.WARNING, __file__, 1, message, args, None)
    record.__dict__.update(extra)
    return record


def test_a_log_line_is_one_json_object_with_the_base_fields() -> None:
    line = _format(_record("config loaded: version=%s", "v1"))
    assert line["level"] == "warning"
    assert line["logger"] == "chassis.test"
    assert line["message"] == "config loaded: version=v1"
    assert line["agent"] == "echo"
    assert line["time"].endswith("Z")
    assert "request_id" not in line and "trace_id" not in line, "outside a run"


def test_inside_a_run_a_line_carries_the_request_id_and_a_32_hex_trace_id() -> None:
    with run_context(request_id="req-1", trace_id="trace-1"):
        line = _format(_record("inside"))
    assert line["request_id"] == "req-1"
    assert len(line["trace_id"]) == 32 and int(line["trace_id"], 16) >= 0
    with run_context(request_id="req-2", trace_id=HEX32):
        assert _format(_record("hex"))["trace_id"] == HEX32


def test_the_message_extra_fields_and_the_traceback_are_redacted() -> None:
    try:
        raise ValueError("mail bob@example.com")
    except ValueError:
        import sys

        record = logging.LogRecord(
            "chassis.x", logging.ERROR, __file__, 1, "for %s", ("ann@example.com",), sys.exc_info()
        )
    record.__dict__["topic"] = "card 4111 1111 1111 1111"
    line = _format(record)
    assert line["message"] == "for <EMAIL_ADDRESS>"
    assert line["topic"] == "card <CREDIT_CARD>"
    assert "bob@example.com" not in line["exception"] and "<EMAIL_ADDRESS>" in line["exception"]


def test_a_span_with_ids_sets_the_run_context_in_the_fake(
    lines: Callable[[], list[dict[str, Any]]],
) -> None:
    telemetry = InMemoryTelemetry()
    with telemetry.span("chassis.run", request_id="req-9", trace_id=HEX32):
        logging.getLogger("chassis.inner").info("in the span")
    logging.getLogger("chassis.inner").info("after the span")
    (inside,) = [x for x in lines() if x["message"] == "in the span"]
    (after,) = [x for x in lines() if x["message"] == "after the span"]
    assert (inside["request_id"], inside["trace_id"]) == ("req-9", HEX32)
    assert "request_id" not in after


def test_otel_telemetry_logs_one_json_line_not_json_in_json(
    lines: Callable[[], list[dict[str, Any]]],
) -> None:
    telemetry = OtelTelemetry(span_exporters=[InMemorySpanExporter()])
    with telemetry.span("chassis.run", request_id="req-3", trace_id=HEX32):
        telemetry.log("error", "run error", request_id="req-3", detail="to ann@example.com")
    (line,) = [x for x in lines() if x["logger"] == "chassis"]
    assert line["message"] == "run error"
    assert line["level"] == "error"
    assert (line["request_id"], line["trace_id"]) == ("req-3", HEX32)
    assert line["detail"] == "to <EMAIL_ADDRESS>"
    assert line["agent"] == "echo"


async def _logs_then_answers(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
    logging.getLogger("chassis.test.handle").warning("handling for ann@example.com")
    yield Start(request_id=ctx.request_id)
    yield Delta(text="ok")
    yield End()


async def test_a_run_through_the_app_logs_with_its_ids(
    lines: Callable[[], list[dict[str, Any]]],
) -> None:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=_logs_then_answers),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://chassis") as client,
        app.router.lifespan_context(app),
    ):
        res = await client.post(
            "/v1/run", json={"request_id": "req-77", "trace_id": "t-77", "input": {"text": "x"}}
        )
    assert res.status_code == 200, res.text
    (line,) = [x for x in lines() if x["logger"] == "chassis.test.handle"]
    assert line["request_id"] == "req-77"
    assert len(line["trace_id"]) == 32
    assert line["message"] == "handling for <EMAIL_ADDRESS>"


def test_configure_logging_installs_one_json_handler_and_keeps_uvicorn_in_it() -> None:
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    stream = io.StringIO()
    try:
        configure_logging(agent="echo", level="warning", stream=stream)
        configure_logging(agent="echo", level="warning", stream=stream)  # idempotent
        mine = [h for h in root.handlers if isinstance(h.formatter, JsonFormatter)]
        assert len(mine) == 1
        assert root.level == logging.WARNING
        logging.getLogger("uvicorn.error").warning("listener up")
        logging.getLogger("chassis.x").info("below the level")
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
    (line,) = _parse(stream)
    assert line["logger"] == "uvicorn.error" and line["agent"] == "echo"


def test_serve_logs_json_and_uvicorn_keeps_no_config_of_its_own(tmp_path: Any) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(
        "profile: fake\nagent: {name: echo, version: '1'}\n"
        "spec:\n  engine: {connector: inprocess, handle: 'chassis.core.handle:echo_wire'}\n"
        "  observability: {log_level: debug}\n"
    )
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    try:
        servers = cli.build_all(cli.parse_args(["serve", "--config", str(path)]))
        assert root.level == logging.DEBUG
        assert any(isinstance(h.formatter, JsonFormatter) for h in root.handlers)
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
    for server in (servers.public, servers.proxy):
        config: uvicorn.Config = server.config
        assert config.log_config is None
