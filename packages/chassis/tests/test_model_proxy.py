"""The model pass-through: `POST /v1/chat/completions` maps the OpenAI shape to `ModelPort` and
back, streaming and complete, forwards no `Authorization` header, and counts calls per route.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from chassis.adapters.litellm import LiteLLMModel
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel, ScriptRule
from chassis.ports.bundle import PortBundle
from chassis.ports.model import ModelPort, ToolCallRequest
from chassis.server import ChassisConfig, create_app

CONFIG: dict[str, Any] = {
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}

BODY: dict[str, Any] = {
    "model": "big-default",
    "messages": [
        {"role": "system", "content": "Rewrite in plain words."},
        {"role": "user", "content": "simplify: the quick brown fox"},
    ],
    "temperature": 0.2,
    "max_tokens": 64,
}


def _ports(model: ModelPort | None = None) -> PortBundle:
    return PortBundle(
        model=model
        or ScriptedModel(
            [
                ScriptRule(
                    match="lookup", tool_call=ToolCallRequest(call_id="c1", name="glossary_lookup")
                ),
                ScriptRule(match="fail", error="upstream_down", retryable=True),
                ScriptRule(match="simplify", reply="Plain words. Short sentences."),
            ]
        ),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://chassis")


def _chunks(text: str) -> list[Any]:
    out: list[Any] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            out.append(payload if payload == "[DONE]" else json.loads(payload))
    return out


async def test_complete_maps_the_openai_shape_both_ways() -> None:
    ports = _ports()
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    async with _client(app) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/chat/completions", json=BODY)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["object"] == "chat.completion" and data["model"] == "big-default"
    choice = data["choices"][0]
    assert choice["message"] == {"role": "assistant", "content": "Plain words. Short sentences."}
    assert choice["finish_reason"] == "stop"
    assert data["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    model = ports.model
    assert isinstance(model, ScriptedModel)
    assert model.calls[-1] == BODY["messages"]
    telemetry = ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    assert telemetry.counter_value("chassis.model_calls", route="big-default") == 1


async def test_stream_ends_with_usage_and_done() -> None:
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports())
    async with _client(app) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/chat/completions", json={**BODY, "stream": True})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    chunks = _chunks(res.text)
    assert chunks[-1] == "[DONE]"
    last = chunks[-2]
    assert last["object"] == "chat.completion.chunk"
    assert last["choices"][0]["finish_reason"] == "stop"
    assert last["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    text = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks[:-1])
    assert text == "Plain words. Short sentences."
    assert all(c["model"] == "big-default" for c in chunks[:-1])


async def test_tool_calls_cross_in_both_modes() -> None:
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports())
    body = {
        **BODY,
        "messages": [{"role": "user", "content": "lookup SLM"}],
        "tools": [{"type": "function", "function": {"name": "glossary_lookup", "parameters": {}}}],
    }
    async with _client(app) as client, app.router.lifespan_context(app):
        complete = (await client.post("/v1/chat/completions", json=body)).json()
        streamed = _chunks(
            (await client.post("/v1/chat/completions", json={**body, "stream": True})).text
        )
    call = complete["choices"][0]["message"]["tool_calls"][0]
    assert call["function"]["name"] == "glossary_lookup" and call["id"] == "c1"
    assert complete["choices"][0]["finish_reason"] == "tool_calls"
    tool_chunks = [c for c in streamed[:-1] if c["choices"][0]["delta"].get("tool_calls")]
    assert tool_chunks and tool_chunks[0]["choices"][0]["delta"]["tool_calls"][0]["index"] == 0
    assert streamed[-2]["choices"][0]["finish_reason"] == "tool_calls"


async def test_model_error_is_an_openai_error_body() -> None:
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports())
    body = {**BODY, "messages": [{"role": "user", "content": "fail"}]}
    async with _client(app) as client, app.router.lifespan_context(app):
        complete = await client.post("/v1/chat/completions", json=body)
        streamed = await client.post("/v1/chat/completions", json={**body, "stream": True})
    assert complete.status_code == 502
    assert complete.json()["error"]["code"] == "upstream_down"
    chunks = _chunks(streamed.text)
    assert chunks[-1] == "[DONE]" and chunks[-2]["error"]["code"] == "upstream_down"
    assert chunks[-2]["error"]["retryable"] is True


async def test_inbound_authorization_is_never_forwarded() -> None:
    upstream: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        upstream.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    model = LiteLLMModel("http://router/v1", transport=httpx.MockTransport(record))
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports(model))
    async with _client(app) as client, app.router.lifespan_context(app):
        res = await client.post(
            "/v1/chat/completions", json=BODY, headers={"Authorization": "Bearer leaked-key"}
        )
    assert res.status_code == 200
    assert upstream and "authorization" not in {k.lower() for k in upstream[0].headers}
    assert "leaked-key" not in res.text


async def test_not_ready_is_503() -> None:
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports())
    async with _client(app) as client:
        assert (await client.post("/v1/chat/completions", json=BODY)).status_code == 503
