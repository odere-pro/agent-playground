"""The OpenAI interface: `POST /v1/chat/completions` on the PUBLIC app, where a client calls the
agent and `model` is the agent's name. Not the model proxy (the same path on the proxy app, where
a workload calls a model; `chassis.server.model_proxy`).

Through the app with `httpx.ASGITransport` and a fake engine, and with the real `openai.AsyncOpenAI`
client over `httpx2.ASGITransport` (the SDK's own HTTP line): only the base URL changes, `model`
is the agent name, and the key is a placeholder. Exit criterion: "The OpenAI and Anthropic SDKs
work with only a base URL change".
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import httpx2
import openai
import pytest
from chassis.core.envelope import Context, TaskInput
from chassis.core.events import Delta, End, Error, Event, Metrics, Start
from chassis.core.handle import echo
from chassis.core.inbound import public_message
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.interfaces.errors import INTERFACE_KEY
from fastapi import FastAPI
from openai import AsyncOpenAI

CONFIG: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
        "model": {"route": "fake-route"},
        "prompt": {"version": "p1"},
    },
}
PATH = "/v1/chat/completions"
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACEPARENT = f"00-{TRACE}-00f067aa0ba902b7-01"


def _app(engine: FakeEngine | None = None, config: dict[str, Any] | None = None) -> FastAPI:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=engine or FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    return create_app(ChassisConfig.model_validate(config or CONFIG), ports)


def _telemetry(app: FastAPI) -> InMemoryTelemetry:
    telemetry = app.state.ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    return telemetry


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://chassis")


def _sdk(app: FastAPI, *, max_retries: int = 2) -> AsyncOpenAI:
    """The official client; only the base URL differs from talking to OpenAI."""
    http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app))
    return AsyncOpenAI(
        base_url="http://chassis/v1",
        api_key="placeholder-not-a-key",
        http_client=http,
        max_retries=max_retries,
    )


def _body(text: str = "hi there", **extra: Any) -> dict[str, Any]:
    return {"model": "echo", "messages": [{"role": "user", "content": text}], **extra}


def _data(text: str) -> list[Any]:
    out: list[Any] = []
    for block in text.split("\n\n"):
        if block.strip():
            assert block.startswith("data: "), block
            raw = block[6:]
            out.append(raw if raw == "[DONE]" else json.loads(raw))
    return out


async def _say(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
    """Scripted by the input text."""
    yield Start(request_id=ctx.request_id)
    yield Metrics(input_tokens=5, output_tokens=2)
    text = input.text or ""
    if text == "fail-first":
        yield Error(code="engine_error", message="boom")
        return
    if text == "retryable":
        yield Error(code="a2a.transport", message="gone", retryable=True)
        return
    if text == "probe":
        yield Delta(text=json.dumps(input.model_dump(), sort_keys=True))
        yield End()
        return
    yield Delta(text="one ")
    if text == "fail-late":
        yield Error(code="a2a.timeout", message="slow", retryable=True)
        return
    yield Delta(text="two")
    yield End()


# --- through the app ----------------------------------------------------------------------------


async def test_complete_through_the_app() -> None:
    app = _app()
    async with _client(app) as client, app.router.lifespan_context(app):
        res = await client.post(PATH, json=_body())
        assert len(app.state.runs) == 0
    assert res.status_code == 200, res.text
    assert res.headers["x-chassis-status"] == "ok"
    body = res.json()
    assert body["id"] == f"chatcmpl-{res.headers['x-request-id']}"
    assert body["model"] == "echo" and body["object"] == "chat.completion"
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "hi there"} | {
        k: v for k, v in body["choices"][0]["message"].items() if k not in {"role", "content"}
    }
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"] == {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4}
    telemetry = _telemetry(app)
    assert telemetry.counter_value("chassis.requests", agent="echo", interface="openai") == 1
    [span] = [s for s in telemetry.spans if s.name == "chassis.run"]
    assert span.attributes["interface"] == "openai" and span.ended


async def test_stream_through_the_app() -> None:
    app = _app(FakeEngine(handle=_say))
    async with _client(app) as client, app.router.lifespan_context(app):
        res = await client.post(PATH, json=_body("go", stream=True))
        assert len(app.state.runs) == 0
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert res.headers["x-trace-id"] and res.headers["x-request-id"]
    assert "x-chassis-status" not in res.headers, "the status is not known when headers go out"
    data = _data(res.text)
    assert data[-1] == "[DONE]"
    assert data[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
    assert [d["choices"][0]["delta"].get("content") for d in data[1:3]] == ["one ", "two"]
    assert data[-2]["choices"][0]["finish_reason"] == "stop"
    assert data[-2]["usage"]["total_tokens"] == 7


async def test_the_workload_gets_system_and_history_in_data_and_nothing_else() -> None:
    app = _app(FakeEngine(handle=_say))
    body = {
        "model": "echo",
        "messages": [
            {"role": "developer", "content": "rules"},
            {"role": "user", "content": "earlier"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "probe"},
        ],
        "temperature": 0.1,
        "max_completion_tokens": 50,
    }
    async with _client(app) as client, app.router.lifespan_context(app):
        res = await client.post(PATH, json=body)
    seen = json.loads(res.json()["choices"][0]["message"]["content"])
    assert seen == {
        "text": "probe",
        "data": {
            "system": "rules",
            "history": [
                {"role": "user", "text": "earlier"},
                {"role": "assistant", "text": "reply"},
            ],
        },
    }


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    ("text", "status", "should_retry"),
    [("fail-first", 500, "false"), ("retryable", 503, "true")],
)
async def test_an_error_before_any_text_is_an_http_error(
    text: str, status: int, should_retry: str, stream: bool
) -> None:
    """The hold rule: nothing is sent before the first delta, so an early error is HTTP."""
    app = _app(FakeEngine(handle=_say))
    async with _client(app) as client, app.router.lifespan_context(app):
        res = await client.post(PATH, json=_body(text, stream=stream))
        assert len(app.state.runs) == 0
    assert res.status_code == status
    assert res.headers["content-type"].startswith("application/json")
    assert res.headers["x-should-retry"] == should_retry
    error = res.json()["error"]
    assert error["type"] == "server_error" and error["retryable"] is (should_retry == "true")


async def test_an_error_after_text_is_an_error_frame_when_streamed() -> None:
    app = _app(FakeEngine(handle=_say))
    async with _client(app) as client, app.router.lifespan_context(app):
        streamed = await client.post(PATH, json=_body("fail-late", stream=True))
        complete = await client.post(PATH, json=_body("fail-late"))
    assert streamed.status_code == 200
    data = _data(streamed.text)
    assert data[-1] == "[DONE]" and data[-2]["error"]["code"] == "a2a.timeout"
    assert all(d["choices"][0]["finish_reason"] is None for d in data[:-2])
    assert complete.status_code == 504 and complete.json()["error"]["code"] == "a2a.timeout"


async def test_refused_validation_and_not_ready_are_openai_errors() -> None:
    engine = FakeEngine(handle=_say)
    app = _app(engine)
    async with _client(app) as client:
        not_ready = await client.post(PATH, json=_body())
        async with app.router.lifespan_context(app):
            wrong_model = await client.post(PATH, json=_body(model="gpt-4o"))
            refused = await client.post(PATH, json=_body(n=3))
            bad_role = await client.post(
                PATH, json={"model": "echo", "messages": [{"role": "bogus", "content": "x"}]}
            )
            bad_part = await client.post(
                PATH,
                json={"model": "echo", "messages": [{"role": "user", "content": [{"x": 1}]}]},
            )
            no_messages = await client.post(PATH, json={"model": "echo"})
            not_json = await client.post(
                PATH, content=b"{", headers={"content-type": "application/json"}
            )
    assert not_ready.status_code == 503 and not_ready.json()["error"]["code"] == "not_ready"
    assert not_ready.headers["x-should-retry"] == "true"
    assert wrong_model.status_code == 404
    assert wrong_model.json()["error"]["code"] == "model_not_found"
    assert refused.status_code == 400 and refused.json()["error"]["param"] == "n"
    for res in (bad_role, bad_part, no_messages, not_json):
        assert res.status_code == 400, res.text
        error = res.json()["error"]
        assert (error["type"], error["code"]) == ("invalid_request_error", "invalid_body")
        assert res.headers["x-should-retry"] == "false"
    assert engine.runs == 0


async def test_ignored_parameters_are_counted_per_name() -> None:
    app = _app()
    body = _body(temperature=0.2, seed=3, user="person", metadata={"k": "v"})
    async with _client(app) as client, app.router.lifespan_context(app):
        res = await client.post(PATH, json=body, headers={"authorization": "Bearer x"})
        await client.post(PATH, json=_body(model="nope", temperature=1.0))
    assert res.status_code == 200
    telemetry = _telemetry(app)

    def ignored(param: str) -> int:
        return telemetry.counter_value("chassis.inbound_ignored", interface="openai", param=param)

    assert (ignored("temperature"), ignored("seed"), ignored("authorization")) == (1, 1, 1)
    assert ignored("user") == ignored("metadata") == 0
    assert all("person" not in str(record) for record in telemetry.logs)


async def test_one_traceparent_twice_at_once_never_conflicts() -> None:
    in_flight = asyncio.Event()
    release = asyncio.Event()

    async def wait(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        if input.text == "first":
            in_flight.set()
            await asyncio.wait_for(release.wait(), timeout=5)
        yield Delta(text=ctx.trace_id)
        yield End()

    app = _app(FakeEngine(handle=wait))
    headers = {"traceparent": TRACEPARENT}
    async with _client(app) as client, app.router.lifespan_context(app):
        first = asyncio.create_task(client.post(PATH, json=_body("first"), headers=headers))
        await asyncio.wait_for(in_flight.wait(), timeout=5)
        second = await client.post(PATH, json=_body("second", stream=True), headers=headers)
        release.set()
        done = await first
    assert done.status_code == 200 and second.status_code == 200
    assert done.headers["x-trace-id"] == TRACE
    assert done.json()["choices"][0]["message"]["content"] == TRACE
    assert second.headers["x-trace-id"] != TRACE
    assert second.headers["x-trace-id"] in second.text
    assert _telemetry(app).counter_value("chassis.trace_id_reminted", interface="openai") == 1


async def test_spec_interfaces_openai_false_unmounts_it() -> None:
    off = {**CONFIG, "spec": {**CONFIG["spec"], "interfaces": {"openai": False}}}
    app = _app(config=off)
    async with _client(app) as client, app.router.lifespan_context(app):
        assert (await client.post(PATH, json=_body())).status_code == 404
    assert PATH not in app.openapi()["paths"]


def test_openapi_declares_the_openai_interface() -> None:
    spec = _app().openapi()
    op = spec["paths"][PATH]["post"]
    assert op["operationId"] == "chat_completions"
    assert op[INTERFACE_KEY] == "openai"
    assert "openai" in op.get("tags", [])
    assert "requestBody" in op and op["requestBody"]["required"] is True
    ok = op["responses"]["200"]
    assert ok["content"]["application/json"]["schema"]["$ref"].endswith("/ChatCompletion")
    assert "text/event-stream" in ok["content"]
    assert "x-request-id" in ok["headers"]
    schemas = spec["components"]["schemas"]
    for status in ("400", "404", "500", "502", "503", "504"):
        ref = op["responses"][status]["content"]["application/json"]["schema"]["$ref"]
        model = schemas[ref.rsplit("/", 1)[-1]]
        assert "error" in model["properties"], status
    assert json.dumps(spec)


# --- the official SDK, base URL change only ------------------------------------------------------


async def test_the_openai_sdk_completes_and_streams() -> None:
    app = _app()
    async with _sdk(app) as client, app.router.lifespan_context(app):
        completion = await client.chat.completions.create(
            model="echo", messages=[{"role": "user", "content": "hi there"}]
        )
        stream = await client.chat.completions.create(
            model="echo",
            messages=[{"role": "user", "content": "hi there"}],
            stream=True,
            stream_options={"include_usage": True},
        )
        chunks = [chunk async for chunk in stream]
    assert completion.choices[0].message.content == "hi there"
    assert completion.usage is not None and completion.usage.total_tokens == 4
    text = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
    assert text == "hi there"
    assert chunks[-1].choices == [] and chunks[-1].usage is not None
    assert chunks[-1].usage.total_tokens == 4


async def test_the_openai_sdk_reads_the_raw_headers() -> None:
    app = _app()
    async with _sdk(app) as client, app.router.lifespan_context(app):
        raw = await client.chat.completions.with_raw_response.create(
            model="echo", messages=[{"role": "user", "content": "x"}]
        )
    assert raw.headers["x-chassis-status"] == "ok"
    assert raw.parse().id == f"chatcmpl-{raw.headers['x-request-id']}"


async def test_the_openai_sdk_request_id_is_the_chassis_request_id() -> None:
    """The OpenAI SDK reads `x-request-id` for `_request_id` and for an error's `request_id`; both
    are the run's request id, on a complete, a stream, and an error.
    """
    app = _app()
    user: list[Any] = [{"role": "user", "content": "hi"}]
    async with _sdk(app) as client, app.router.lifespan_context(app):
        completion = await client.chat.completions.create(model="echo", messages=user)
        raw = await client.chat.completions.with_raw_response.create(
            model="echo", messages=user, stream=True
        )
        chunks = [chunk async for chunk in raw.parse()]
        with pytest.raises(openai.NotFoundError) as info:
            await client.chat.completions.create(model="gpt-4o", messages=user)
    assert completion._request_id == completion.id.removeprefix("chatcmpl-")
    assert raw.request_id == chunks[0].id.removeprefix("chatcmpl-")
    assert info.value.request_id and info.value.request_id == info.value.response.headers.get(
        "x-request-id"
    )


@pytest.mark.parametrize("stream", [False, True])
async def test_the_sdk_raises_the_right_class_and_does_not_retry_a_final_error(
    stream: bool,
) -> None:
    engine = FakeEngine(handle=_say)
    app = _app(engine)
    async with _sdk(app) as client, app.router.lifespan_context(app):
        with pytest.raises(openai.InternalServerError) as caught:
            await client.chat.completions.create(
                model="echo", messages=[{"role": "user", "content": "fail-first"}], stream=stream
            )
    assert caught.value.status_code == 500
    assert engine.runs == 1, "x-should-retry: false stops the SDK's default retries"


async def test_the_sdk_retries_a_retryable_error_as_told() -> None:
    engine = FakeEngine(handle=_say)
    app = _app(engine)
    async with _sdk(app, max_retries=1) as client, app.router.lifespan_context(app):
        client = client.with_options(timeout=5)
        with pytest.raises(openai.InternalServerError) as caught:
            await client.chat.completions.create(
                model="echo", messages=[{"role": "user", "content": "retryable"}]
            )
    assert caught.value.status_code == 503 and engine.runs == 2


async def test_the_sdk_raises_on_a_mid_stream_error_frame() -> None:
    app = _app(FakeEngine(handle=_say))
    got: list[str] = []
    async with _sdk(app) as client, app.router.lifespan_context(app):
        stream = await client.chat.completions.create(
            model="echo", messages=[{"role": "user", "content": "fail-late"}], stream=True
        )
        with pytest.raises(openai.APIError, match=public_message("a2a.timeout")):
            async for chunk in stream:
                got.append(chunk.choices[0].delta.content or "")  # noqa: PERF401 partial kept
    assert "".join(got) == "one "


@pytest.mark.parametrize(
    ("kwargs", "error", "code"),
    [
        ({"model": "gpt-4o"}, openai.NotFoundError, "model_not_found"),
        ({"n": 2}, openai.BadRequestError, "unsupported_parameter"),
        ({"logprobs": True}, openai.BadRequestError, "unsupported_parameter"),
        (
            {"tools": [{"type": "function", "function": {"name": "f"}}]},
            openai.BadRequestError,
            "unsupported_parameter",
        ),
        (
            {"response_format": {"type": "json_object"}},
            openai.BadRequestError,
            "unsupported_parameter",
        ),
        (
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": "https://x.invalid/a.png"}}
                        ],
                    }
                ]
            },
            openai.BadRequestError,
            "unsupported_message",
        ),
    ],
)
async def test_the_sdk_raises_on_a_refused_request(
    kwargs: dict[str, Any], error: type[openai.APIStatusError], code: str
) -> None:
    engine = FakeEngine(handle=_say)
    app = _app(engine)
    call: dict[str, Any] = {
        "model": "echo",
        "messages": [{"role": "user", "content": "x"}],
    } | kwargs
    async with _sdk(app) as client, app.router.lifespan_context(app):
        with pytest.raises(error) as caught:
            await client.chat.completions.create(**call)
    assert caught.value.code == code
    assert engine.runs == 0
