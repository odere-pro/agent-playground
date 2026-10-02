"""The wire edges of the public interfaces, pinned next to the code (PoC-3 Schemathesis findings
1 to 3, `pocs/poc-03-one-interface-every-client/tests/test_openapi_props.py`).

1. `POST /v1/run` validates its body strictly: no value of the wrong JSON type is coerced, and
   `budget.max_tokens` and `budget.timeout_ms` must be positive. Every interface refuses a budget
   of 0 or less.
2. An error FastAPI or Starlette raises before the handler (a body that is not JSON, a wrong
   content type, a method the route does not allow) is answered in the route's format. Native
   keeps `{"detail": str}`.
3. The 200 `text/event-stream` schema of the three streaming routes is one shared SSE event.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from chassis.adapters.anthropic_compat.inbound import AnthropicInbound
from chassis.core.envelope import Budget, Versions
from chassis.core.handle import echo
from chassis.core.inbound import Ids, Refused, Served
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.interfaces.native import RunRequest
from chassis.server.interfaces.serve import SSE_EVENT_SCHEMA
from fastapi import FastAPI
from pydantic import ValidationError

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
STREAMING_ROUTES = ("/v1/run", "/v1/chat/completions", "/v1/messages")
SDK_ROUTES = ("/v1/chat/completions", "/v1/messages")


def _app() -> tuple[FastAPI, FakeEngine]:
    engine = FakeEngine(handle=echo)
    ports = PortBundle(
        model=ScriptedModel(),
        engine=engine,
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    return create_app(ChassisConfig.model_validate(CONFIG), ports), engine


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://chassis")


def _is_openai_error(body: Any) -> bool:
    error = body.get("error") if isinstance(body, dict) else None
    return isinstance(error, dict) and isinstance(error.get("message"), str) and "type" in error


def _is_anthropic_error(body: Any) -> bool:
    return (
        isinstance(body, dict)
        and body.get("type") == "error"
        and body["error"]["type"] == "invalid_request_error"
        and isinstance(body["error"]["message"], str)
    )


FORMAT_CHECK = {"/v1/chat/completions": _is_openai_error, "/v1/messages": _is_anthropic_error}


# --- 1. strict native body, positive budgets ----------------------------------------------------

WRONG_TYPES = [
    {"budget": {"max_tokens": True}},
    {"budget": {"timeout_ms": False}},
    {"budget": {"max_tokens": "5"}},
    {"budget": {"max_tokens": 5.0}},
    {"budget": {"timeout_ms": "1000"}},
    {"stream": 0},
    {"stream": "true"},
]
NOT_POSITIVE = [
    {"budget": {"max_tokens": 0}},
    {"budget": {"max_tokens": -1}},
    {"budget": {"timeout_ms": 0}},
    {"budget": {"timeout_ms": -5}},
]


@pytest.mark.parametrize("extra", WRONG_TYPES + NOT_POSITIVE)
def test_run_request_refuses_coercion_and_budgets_of_zero_or_less(extra: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        RunRequest.model_validate({"input": {"text": "x"}, **extra})


def test_run_request_takes_the_right_types_and_keeps_the_envelope_models() -> None:
    body = RunRequest.model_validate(
        {
            "input": {"text": "x", "data": {"k": [1, "a"]}},
            "stream": True,
            "budget": {"max_tokens": 5, "timeout_ms": 1},
        }
    )
    assert type(body.budget) is Budget and body.budget == Budget(max_tokens=5, timeout_ms=1)
    assert body.stream is True and body.input.data == {"k": [1, "a"]}
    assert RunRequest.model_validate({"input": {}}).budget == Budget()


def test_the_strict_boundary_leaves_the_published_schemas_as_they_were() -> None:
    """Strictness and the minimums are at the HTTP boundary; `Budget` itself is unchanged, so the
    JSON Schemas `make schemas` publishes are unchanged (contract v1)."""
    schema = Budget.model_json_schema()
    for field in ("max_tokens", "timeout_ms"):
        assert schema["properties"][field] == {
            "default": Budget.model_fields[field].default,
            "title": field.replace("_", " ").title(),
            "type": "integer",
        }
    budget_ref = RunRequest.model_json_schema()["properties"]["budget"]
    assert budget_ref["$ref"].endswith("/Budget")


@pytest.mark.parametrize("extra", WRONG_TYPES + NOT_POSITIVE)
async def test_run_answers_422_for_a_wrong_type_or_a_budget_of_zero(extra: dict[str, Any]) -> None:
    app, engine = _app()
    async with _client(app) as client, app.router.lifespan_context(app):
        response = await client.post("/v1/run", json={"input": {"text": "x"}, **extra})
    assert response.status_code == 422, response.text
    assert engine.runs == 0


def _anthropic_request(max_tokens: Any) -> None:
    AnthropicInbound().to_request(
        {"model": "echo", "max_tokens": max_tokens, "messages": [{"role": "user", "content": "x"}]},
        {},
        ids=Ids("r", "t", "k"),
        served=Served("echo", "0.0.1", Versions(chassis="test")),
    )


@pytest.mark.parametrize("max_tokens", [0, -1, True])
def test_anthropic_refuses_a_max_tokens_of_zero_or_less(max_tokens: Any) -> None:
    with pytest.raises(Refused) as refused:
        _anthropic_request(max_tokens)
    assert refused.value.reply.status == 400
    assert "max_tokens" in refused.value.reply.body["error"]["message"]


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/messages", {"max_tokens": 0}),
        ("/v1/chat/completions", {"max_tokens": 0}),
        ("/v1/chat/completions", {"max_completion_tokens": -3}),
    ],
)
async def test_every_sdk_interface_refuses_a_budget_of_zero_or_less(
    path: str, body: dict[str, Any]
) -> None:
    app, engine = _app()
    payload = {"model": "echo", "messages": [{"role": "user", "content": "x"}], **body}
    if path == "/v1/messages":
        payload.setdefault("max_tokens", 64)
    async with _client(app) as client, app.router.lifespan_context(app):
        response = await client.post(path, json=payload)
    assert response.status_code == 400, response.text
    assert FORMAT_CHECK[path](response.json()), response.json()
    assert engine.runs == 0


# --- 2. errors before the handler, in the route's format ----------------------------------------


@pytest.mark.parametrize("path", SDK_ROUTES)
@pytest.mark.parametrize(
    ("content", "content_type"),
    [
        (b"\xff", "application/json"),
        (b"{not json", "application/json"),
        (b'{"model": "echo"}', "text/plain"),
    ],
    ids=["not-utf8", "not-json", "text-plain"],
)
async def test_an_unparseable_body_is_400_in_the_routes_format(
    path: str, content: bytes, content_type: str
) -> None:
    app, engine = _app()
    async with _client(app) as client:
        response = await client.post(path, content=content, headers={"content-type": content_type})
    assert response.status_code == 400, response.text
    assert response.headers["content-type"] == "application/json"
    assert FORMAT_CHECK[path](response.json()), response.json()
    assert "x-request-id" in response.headers and "x-trace-id" in response.headers
    assert engine.runs == 0


@pytest.mark.parametrize("path", SDK_ROUTES)
async def test_a_method_the_route_does_not_allow_is_405_in_the_routes_format(path: str) -> None:
    app, _ = _app()
    async with _client(app) as client:
        response = await client.get(path)
    assert response.status_code == 405, response.text
    assert response.headers["allow"] == "POST"
    assert FORMAT_CHECK[path](response.json()), response.json()


async def test_native_keeps_detail_text_before_the_handler() -> None:
    app, _ = _app()
    async with _client(app) as client:
        undecodable = await client.post(
            "/v1/run", content=b"\xff", headers={"content-type": "application/json"}
        )
        method = await client.get("/v1/run")
        missing = await client.get("/no/such/path")
    assert undecodable.status_code == 400
    assert undecodable.json() == {"detail": "There was an error parsing the body"}
    assert method.status_code == 405 and method.json() == {"detail": "Method Not Allowed"}
    assert missing.status_code == 404 and missing.json() == {"detail": "Not Found"}


# --- 3. one SSE event schema --------------------------------------------------------------------


def test_the_three_streaming_routes_declare_one_sse_event_schema() -> None:
    assert SSE_EVENT_SCHEMA == {
        "type": "object",
        "required": ["data"],
        "properties": {
            "event": {"type": "string"},
            "data": {"type": "string"},
            "id": {"type": "string"},
            "retry": {"type": "integer"},
        },
    }
    app, _ = _app()
    spec = app.openapi()
    for path in STREAMING_ROUTES:
        content = spec["paths"][path]["post"]["responses"]["200"]["content"]
        assert content["text/event-stream"]["schema"] == SSE_EVENT_SCHEMA, path
