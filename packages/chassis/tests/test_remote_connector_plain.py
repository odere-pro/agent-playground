"""The `remote` connector in plain-A2A mode (`spec.engine.protocol: a2a`, contract v5 draft B.4 to
B.7): a third-party agent that sends no `chassis.event`. The server is an a2a-sdk server over a Unix
socket (`plain_a2a_stub`) that streams the shape the kagent-adk probe recorded. Chassis mode is
unchanged: `test_remote_connector.py` and `test_lane_contract.py` pass without edits.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import pytest
from a2a.client import ClientCallContext
from a2a.server.agent_execution import RequestContext
from a2a.types import CancelTaskRequest, SendMessageRequest, StreamResponse
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from a2a_uds import ASGIApp, Receive, Recorder, Scope, Send, serve_uds
from chassis.adapters.a2a.plain import PlainOptions
from chassis.adapters.a2a.remote import RemoteConnector
from chassis.core.collector import collect
from chassis.core.envelope import Budget, Request, Response, TaskInput, Versions
from chassis.core.events import Delta, End, Error, Event, Metrics, Start
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis_contracts.helpers import make_context, make_request
from plain_a2a_stub import (
    ANSWER,
    CHUNKS,
    KAGENT_USAGE,
    USAGE_KEY,
    Bodies,
    PlainStub,
    S,
    artifact,
    kagent_script,
    probe_card,
    reply,
    status,
    stub_app,
    task,
)

REMOTE_URL = "http://remote.test:8443"
TOKEN_ENV = "POC06_TEST_PLAIN_TOKEN"
SECRET_TEXT = "disk full at /var/lib/secret-path, contact admin@internal.example"
"""Text a failing remote puts in its status message. It must never reach a `Response`."""


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    value = secrets.token_hex(32)
    monkeypatch.setenv(TOKEN_ENV, value)
    yield value


@dataclass
class RequireBearer:
    app: ASGIApp
    expected: str

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            if headers.get("authorization") != f"Bearer {self.expected}":
                await send({"type": "http.response.start", "status": 401, "headers": []})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self.app(scope, receive, send)


def _bundle(engine: Any) -> PortBundle:
    return PortBundle(
        model=ScriptedModel(),
        engine=engine,
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )


def _config(path: str | None, **extra: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "connector": "remote",
        "protocol": "a2a",
        "url": REMOTE_URL,
        "auth": {"scheme": "bearer", "token_env": TOKEN_ENV, "previous_token_env": None},
        "a2a": {"usage_key": USAGE_KEY},
        "probe_timeout_s": 2.0,
    }
    if path is not None:
        config["uds"] = path
    config.update(extra)
    return config


async def _remote(path: str, **extra: Any) -> tuple[RemoteConnector, InMemoryTelemetry]:
    connector = RemoteConnector()
    ports = _bundle(connector)
    await connector.setup(_config(path, **extra), ports)
    telemetry = ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    return connector, telemetry


async def _events(connector: RemoteConnector, request: Request) -> list[Event]:
    try:
        async with asyncio.timeout(10):
            return [e async for e in connector.run(request, make_context(request))]
    finally:
        await connector.close()


async def _response(connector: RemoteConnector, request: Request) -> Response:
    try:
        async with asyncio.timeout(10):
            return await collect(
                connector.run(request, make_context(request)),
                request,
                Versions(chassis="test"),
            )
    finally:
        await connector.close()


def _serve(stub: PlainStub, token: str, card: Any = None) -> Any:
    return serve_uds(Recorder(RequireBearer(stub_app(stub, card), token)))


def _script(*items: StreamResponse) -> Any:
    def script(_: RequestContext) -> Sequence[StreamResponse]:
        return items

    return script


def _kinds(events: Sequence[Event]) -> list[str]:
    return [e.type for e in events]


# --- the probe's stream ---


async def test_the_kagent_stream_becomes_start_deltas_metrics_end(token: str) -> None:
    stub = PlainStub(kagent_script)
    request = make_request(text="simplify this")
    async with serve_uds(RequireBearer(stub_app(stub), token)) as path:
        connector, _ = await _remote(path)
        events = await _events(connector, request)
    assert _kinds(events) == ["start", *["delta"] * 6, "metrics", "end"]
    assert events[0] == Start(request_id=request.request_id)
    assert [e.text for e in events if isinstance(e, Delta)] == list(CHUNKS)
    metrics = events[-2]
    assert isinstance(metrics, Metrics)
    assert (metrics.input_tokens, metrics.output_tokens, metrics.attempt) == (42, 9, 1)
    assert type(metrics.input_tokens) is int
    assert events[-1] == End(status="ok", output=None)


async def test_regression_the_answer_text_is_not_dropped(token: str) -> None:
    """The probe's bug: the chassis mode read this stream as `end ok` with `output: null`, and the
    `Response` had no text. Plain mode keeps the answer, once (the snapshot is not a delta)."""
    stub = PlainStub(kagent_script)
    async with serve_uds(RequireBearer(stub_app(stub), token)) as path:
        connector, _ = await _remote(path)
        response = await _response(connector, make_request())
    assert response.status == "ok"
    assert response.output == {"text": ANSWER}
    assert (response.metrics["input_tokens"], response.metrics["output_tokens"]) == (42, 9)


async def test_the_chassis_mode_still_reads_the_same_stream_as_before(token: str) -> None:
    """The control for the regression test: no `protocol`, and the answer is lost, as the probe
    saw. The default is untouched."""
    stub = PlainStub(kagent_script)
    async with serve_uds(RequireBearer(stub_app(stub), token)) as path:
        config = _config(path)
        for key in ("protocol", "a2a"):
            del config[key]
        connector = RemoteConnector()
        await connector.setup(config, _bundle(connector))
        response = await _response(connector, make_request())
    assert response.status == "ok" and response.output == {"text": ""}


# --- the snapshot, the unary task, the message reply ---


async def test_a_snapshot_with_no_deltas_is_the_output(token: str) -> None:
    stub = PlainStub(
        _script(
            task(S.TASK_STATE_SUBMITTED),
            artifact(ANSWER, last_chunk=True, usage=KAGENT_USAGE),
            status(S.TASK_STATE_COMPLETED),
        )
    )
    async with serve_uds(RequireBearer(stub_app(stub), token)) as path:
        connector, _ = await _remote(path)
        events = await _events(connector, make_request())
    assert _kinds(events) == ["start", "metrics", "end"]
    assert events[-1] == End(status="ok", output={"text": ANSWER})


async def test_a_unary_task_with_the_answer_in_its_artifact(token: str) -> None:
    stub = PlainStub(
        _script(task(S.TASK_STATE_COMPLETED, artifacts=[("A", ANSWER)], usage=KAGENT_USAGE))
    )
    async with serve_uds(RequireBearer(stub_app(stub), token)) as path:
        connector, _ = await _remote(path)
        events = await _events(connector, make_request())
    assert _kinds(events) == ["start", "metrics", "end"]
    assert events[1] == Metrics(input_tokens=42, output_tokens=9)
    assert events[-1] == End(status="ok", output={"text": ANSWER})


async def test_a_message_reply_is_the_answer(token: str) -> None:
    stub = PlainStub(_script(reply(ANSWER)))
    async with serve_uds(RequireBearer(stub_app(stub), token)) as path:
        connector, _ = await _remote(path)
        events = await _events(connector, make_request())
    assert _kinds(events) == ["start", "metrics", "end"]
    assert events[-1] == End(status="ok", output={"text": ANSWER})


# --- failure: fixed text ---


@pytest.mark.parametrize("state", [S.TASK_STATE_FAILED, S.TASK_STATE_REJECTED])
async def test_a_failed_task_has_fixed_text_and_the_remote_text_stays_in_the_log(
    token: str, state: int
) -> None:
    stub = PlainStub(_script(task(S.TASK_STATE_SUBMITTED), status(state, text=SECRET_TEXT)))
    async with serve_uds(RequireBearer(stub_app(stub), token)) as path:
        connector, telemetry = await _remote(path)
        response = await _response(connector, make_request())
    assert response.status == "error"
    error = response.output["error"]
    assert error["code"] == "a2a.failed"
    assert error["message"] == f"the task ended in state {S.Name(state)}"
    assert "secret-path" not in response.model_dump_json()
    assert "internal.example" not in response.model_dump_json()
    logged = [e for e in telemetry.logs if e["message"] == "plain a2a remote failed"]
    assert len(logged) == 1 and "secret-path" in logged[0]["remote_text"]


# --- paused states: cancel ---


@pytest.mark.parametrize("state", [S.TASK_STATE_INPUT_REQUIRED, S.TASK_STATE_AUTH_REQUIRED])
async def test_input_required_is_an_error_and_the_task_is_canceled(token: str, state: int) -> None:
    stub = PlainStub(_script(task(S.TASK_STATE_SUBMITTED), status(state)), hang_after=True)
    recorder = Recorder(RequireBearer(stub_app(stub), token))
    async with serve_uds(recorder) as path:
        connector, _ = await _remote(path)
        events = await _events(connector, make_request())
    assert _kinds(events) == ["start", "error"]
    error = events[-1]
    assert isinstance(error, Error)
    assert error.code == "a2a.unsupported_state" and error.retryable is False
    assert error.message == f"{S.Name(state)} is not in the contract"
    assert [s.method for s in recorder.rpc()] == ["SendStreamingMessage", "CancelTask"]
    assert len(stub.cancels) == 1


async def test_a_completed_run_sends_no_cancel(token: str) -> None:
    stub = PlainStub(kagent_script)
    recorder = Recorder(RequireBearer(stub_app(stub), token))
    async with serve_uds(recorder) as path:
        connector, _ = await _remote(path)
        await _events(connector, make_request())
    assert [s.method for s in recorder.rpc()] == ["SendStreamingMessage"]
    assert stub.cancels == []


class _FakeClient:
    """An a2a-sdk `Client` stand-in that streams chosen items, for first items the SDK server
    cannot send (its own server wants a `task` first)."""

    def __init__(self, items: Sequence[StreamResponse]) -> None:
        self.items = items
        self.canceled: list[str] = []

    async def send_message(
        self, message: SendMessageRequest, *, context: ClientCallContext | None = None
    ) -> AsyncIterator[StreamResponse]:
        for item in self.items:
            yield item

    async def cancel_task(
        self, request: CancelTaskRequest, *, context: ClientCallContext | None = None
    ) -> None:
        self.canceled.append(request.id)

    async def close(self) -> None:
        return None


def _faked(items: Sequence[StreamResponse]) -> tuple[RemoteConnector, _FakeClient]:
    connector = RemoteConnector()
    connector._plain = PlainOptions(usage_key=USAGE_KEY)
    fake = _FakeClient(items)
    connector._client = fake  # type: ignore[assignment]
    connector._ports = _bundle(connector)
    return connector, fake


@pytest.mark.parametrize(
    "first",
    [
        status(S.TASK_STATE_INPUT_REQUIRED),
        artifact("partial", append=True),
    ],
    ids=["status_update", "artifact_update"],
)
async def test_the_cancel_goes_out_when_the_task_id_came_from_an_update_not_a_task(
    first: StreamResponse,
) -> None:
    items = [first]
    if first.HasField("artifact_update"):
        items.append(status(S.TASK_STATE_AUTH_REQUIRED))
    connector, fake = _faked(items)
    events = await _events(connector, make_request())
    assert events[-1].type == "error"
    assert fake.canceled == ["task-1"]


async def test_a_stream_that_ends_without_a_terminal_state_is_a_transport_error() -> None:
    connector, fake = _faked([task(S.TASK_STATE_SUBMITTED), status(S.TASK_STATE_WORKING, text="x")])
    events = await _events(connector, make_request())
    assert _kinds(events) == ["start", "delta", "error"]
    error = events[-1]
    assert isinstance(error, Error) and error.code == "a2a.transport" and error.retryable
    assert fake.canceled == ["task-1"]


async def test_the_deadline_is_a_timeout_error(token: str) -> None:
    stub = PlainStub(_script(task(S.TASK_STATE_SUBMITTED)), hang_after=True)
    request = make_request().model_copy(update={"budget": Budget(timeout_ms=300)})
    recorder = Recorder(RequireBearer(stub_app(stub), token))
    async with serve_uds(recorder) as path:
        connector, _ = await _remote(path)
        events = await _events(connector, request)
    assert _kinds(events) == ["start", "error"]
    error = events[-1]
    assert isinstance(error, Error) and error.code == "a2a.timeout" and error.retryable
    assert [s.method for s in recorder.rpc()] == ["SendStreamingMessage", "CancelTask"]


# --- the request side ---


async def test_the_request_has_no_chassis_metadata_and_no_context_id_by_default(
    token: str,
) -> None:
    stub = PlainStub(kagent_script)
    bodies = Bodies(stub_app(stub))
    recorder = Recorder(RequireBearer(bodies, token))
    request = make_request(text="simplify this")
    async with serve_uds(recorder) as path:
        connector, _ = await _remote(path)
        await _events(connector, request)
    params = bodies.sent[0]["params"]
    assert "metadata" not in params, params
    message = params["message"]
    assert "contextId" not in message and "metadata" not in message, message
    assert message["parts"] == [{"text": "simplify this"}]
    assert "chassis" not in json.dumps(params)
    sent = [s for s in recorder.rpc() if s.method == "SendStreamingMessage"]
    assert sent[0].headers["traceparent"].startswith("00-")


async def test_context_id_trace_id_sets_the_run_trace_id(token: str) -> None:
    stub = PlainStub(kagent_script)
    bodies = Bodies(stub_app(stub))
    request = make_request()
    async with serve_uds(RequireBearer(bodies, token)) as path:
        connector, _ = await _remote(path, a2a={"usage_key": USAGE_KEY, "context_id": "trace_id"})
        await _events(connector, request)
    params = bodies.sent[0]["params"]
    assert params["message"]["contextId"] == request.trace_id
    assert "metadata" not in params and "chassis" not in json.dumps(params)


async def test_a_data_part_goes_out_only_when_data_is_not_empty(token: str) -> None:
    stub = PlainStub(kagent_script)
    request = make_request(text="with data").model_copy(
        update={"input": TaskInput(text="with data", data={"n": 3})}
    )
    async with serve_uds(RequireBearer(stub_app(stub), token)) as path:
        connector, _ = await _remote(path)
        await _events(connector, request)
    parts = stub.messages[0].parts
    assert [p.HasField("text") for p in parts] == [True, False]
    assert parts[1].HasField("data")


# --- the bearer and the card pin are unchanged ---


async def test_the_token_is_on_the_card_fetch_the_message_the_cancel_and_the_probe(
    token: str,
) -> None:
    stub = PlainStub(
        _script(task(S.TASK_STATE_SUBMITTED), status(S.TASK_STATE_INPUT_REQUIRED)),
        hang_after=True,
    )
    recorder = Recorder(RequireBearer(stub_app(stub), token))
    async with serve_uds(recorder) as path:
        connector, _ = await _remote(path)
        assert await connector.probe() is True
        events = await _events(connector, make_request())
    assert events[-1].type == "error"
    assert recorder.seen[0].path == AGENT_CARD_WELL_KNOWN_PATH
    assert [s.method for s in recorder.rpc()] == ["SendStreamingMessage", "CancelTask"]
    assert len(recorder.seen) == 4
    for seen in recorder.seen:
        assert seen.headers.get("authorization") == f"Bearer {token}", seen.path


async def test_a_card_that_names_another_host_is_not_followed(token: str) -> None:
    stub = PlainStub(kagent_script)
    recorder = Recorder(RequireBearer(stub_app(stub, probe_card("http://evil.test:9999")), token))
    async with serve_uds(recorder) as path:
        connector, _ = await _remote(path)
        events = await _events(connector, make_request())
    assert events[-1].type == "end"
    assert recorder.rpc()
    for seen in recorder.seen:
        assert seen.headers["host"] == "remote.test:8443"


async def test_the_span_says_plain_and_whether_usage_was_known(token: str) -> None:
    stub = PlainStub(kagent_script)
    async with serve_uds(RequireBearer(stub_app(stub), token)) as path:
        connector, telemetry = await _remote(path)
        await _events(connector, make_request())
    span = next(s for s in telemetry.spans if s.name == "chassis.engine.run")
    assert span.attributes["a2a.protocol"] == "a2a" and span.attributes["a2a.usage_known"] is True
    assert span.attributes["a2a.task_id"]


async def test_no_usage_key_means_zero_usage_and_an_unknown_span(token: str) -> None:
    stub = PlainStub(kagent_script)
    async with serve_uds(RequireBearer(stub_app(stub), token)) as path:
        connector, telemetry = await _remote(path, a2a={})
        events = await _events(connector, make_request())
    metrics = next(e for e in events if isinstance(e, Metrics))
    assert (metrics.input_tokens, metrics.output_tokens) == (0, 0)
    span = next(s for s in telemetry.spans if s.name == "chassis.engine.run")
    assert span.attributes["a2a.usage_known"] is False


# --- setup ---


@pytest.mark.parametrize(
    ("extra", "match"),
    [
        ({"protocol": "grpc"}, r"engine\.protocol"),
        ({"a2a": {"context_id": "random"}}, r"engine\.a2a\.context_id"),
        ({"a2a": {"usage_key": ""}}, r"engine\.a2a\.usage_key"),
        ({"a2a": "yes"}, r"engine\.a2a"),
        ({"protocol": "chassis"}, r"engine\.a2a needs engine\.protocol: a2a"),
    ],
)
async def test_setup_refuses_a_bad_plain_option(
    token: str, extra: dict[str, Any], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        await RemoteConnector().setup(_config(None, **extra), _bundle(None))
