"""`POST /v1/feedback` (PoC-7, `chassis.server.feedback`): the request-to-trace lookup in
`StatePort`, the answers, the event, and the telemetry. Over the in-memory fakes, no socket.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import pytest
from chassis.core.handle import echo
from chassis.core.trace import trace_id_hex
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.events import InMemoryBus
from chassis.fakes.feedback import InMemoryFeedback
from chassis.ports.bundle import PortBundle
from chassis.ports.feedback import FeedbackPort, NoFeedback
from chassis.ports.state import InMemoryState, StatePort
from chassis.schemas import SCHEMA_DIR
from chassis.server import ChassisConfig, create_app
from chassis.server.feedback import (
    COUNTER,
    DATASCHEMA,
    FEEDBACK_PATH,
    FEEDBACK_RECEIVED,
    SPAN,
    FeedbackEvents,
    default_feedback_id,
    feedback_on,
    lookup_key,
)
from chassis_contracts.events import assert_valid_cloudevent
from fastapi import FastAPI

AGENT = "echo"


def _config(*, adapter: str = "memory", **feedback: Any) -> ChassisConfig:
    return ChassisConfig.model_validate(
        {
            "version": "cfg-1",
            "profile": "fake",
            "agent": {"name": AGENT, "version": "0.0.1"},
            "spec": {
                "adapters": {"feedback": adapter},
                "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
                "feedback": feedback,
            },
        }
    )


def _app(
    *,
    feedback: FeedbackPort | None = None,
    state: StatePort | None = None,
    bus: InMemoryBus | None = None,
    config: ChassisConfig | None = None,
) -> FastAPI:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=state or InMemoryState(),
        events=bus or InMemoryBus(),
        feedback=feedback or InMemoryFeedback(),
    )
    return create_app(config or _config(), ports)


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://chassis") as client:
            yield client


async def _run(client: httpx.AsyncClient) -> dict[str, Any]:
    answer = await client.post("/v1/run", json={"input": {"text": "hello"}})
    assert answer.status_code == 200
    body: dict[str, Any] = answer.json()
    return body


def _telemetry(app: FastAPI) -> InMemoryTelemetry:
    telemetry = app.state.ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    return telemetry


async def test_feedback_after_a_run_lands_on_its_trace() -> None:
    store = InMemoryFeedback()
    app = _app(feedback=store)
    async with _client(app) as client:
        run = await _run(client)
        answer = await client.post(
            FEEDBACK_PATH,
            json={"request_id": run["request_id"], "score": 1, "label": "helpful"},
        )
    assert answer.status_code == 202
    body = answer.json()
    trace = trace_id_hex(run["trace_id"])
    assert body["trace_id"] == trace
    stored = store.records[body["feedback_id"]]
    assert (stored.request_id, stored.trace_id, stored.score) == (run["request_id"], trace, 1.0)
    assert stored.label == "helpful"
    assert stored.subject is None
    assert isinstance(stored.timestamp, datetime)
    assert stored.timestamp.tzinfo is not None
    assert body["feedback_id"] == default_feedback_id(run["request_id"], None)
    telemetry = _telemetry(app)
    assert telemetry.counter_value(COUNTER, label="helpful") == 1
    assert any(s.name == SPAN for s in telemetry.spans)


async def test_an_unknown_request_id_is_404() -> None:
    store = InMemoryFeedback()
    async with _client(_app(feedback=store)) as client:
        answer = await client.post(FEEDBACK_PATH, json={"request_id": "nope", "score": 0})
    assert answer.status_code == 404
    assert answer.json()["detail"]["code"] == "unknown_request"
    assert store.records == {}


async def test_a_repeat_is_one_record_with_the_first_timestamp() -> None:
    store = InMemoryFeedback()
    async with _client(_app(feedback=store)) as client:
        run = await _run(client)
        body = {"request_id": run["request_id"], "score": 0.0, "comment": "meh"}
        first = await client.post(FEEDBACK_PATH, json=body)
        at = store.records[first.json()["feedback_id"]].timestamp
        again = await client.post(FEEDBACK_PATH, json={**body, "score": 1.0})
    assert first.json()["feedback_id"] == again.json()["feedback_id"]
    assert len(store.records) == 1
    stored = store.records[first.json()["feedback_id"]]
    assert (stored.score, stored.timestamp) == (1.0, at)


async def test_a_feedback_id_used_for_another_request_is_409() -> None:
    async with _client(_app()) as client:
        one, two = await _run(client), await _run(client)
        ok = await client.post(
            FEEDBACK_PATH, json={"request_id": one["request_id"], "score": 1, "feedback_id": "f-1"}
        )
        clash = await client.post(
            FEEDBACK_PATH, json={"request_id": two["request_id"], "score": 1, "feedback_id": "f-1"}
        )
    assert ok.status_code == 202
    assert ok.json()["feedback_id"] == "f-1"
    assert clash.status_code == 409
    assert clash.json()["detail"]["code"] == "feedback_id_conflict"


@pytest.mark.parametrize(
    "body",
    [
        {"request_id": "r", "score": 1.5},
        {"request_id": "r", "score": -0.1},
        {"request_id": "r", "score": "1"},
        {"request_id": "r", "score": 1, "subject": "someone-else"},
        {"request_id": "r", "score": 1, "trace_id": "0" * 32},
        {"request_id": "", "score": 1},
        {"score": 1},
        {"request_id": "r", "score": 1, "label": "x" * 65},
        {"request_id": "r", "score": 1, "comment": "x" * 2001},
        {"request_id": "r", "score": 1, "feedback_id": "has space"},
    ],
)
async def test_a_bad_body_is_422(body: dict[str, Any]) -> None:
    async with _client(_app()) as client:
        answer = await client.post(FEEDBACK_PATH, json=body)
    assert answer.status_code == 422


async def test_the_subject_comes_from_the_principal_never_the_body() -> None:
    class Principal:
        subject = "user-42"

    store = InMemoryFeedback()
    app = _app(feedback=store)

    @app.middleware("http")
    async def authenticate(request: Any, call_next: Any) -> Any:
        request.state.principal = Principal()  # what W2's auth middleware sets
        return await call_next(request)

    async with _client(app) as client:
        run = await _run(client)
        answer = await client.post(
            FEEDBACK_PATH, json={"request_id": run["request_id"], "score": 1}
        )
    stored = store.records[answer.json()["feedback_id"]]
    assert stored.subject == "user-42"
    assert answer.json()["feedback_id"] == default_feedback_id(run["request_id"], "user-42")


async def test_no_feedback_port_is_501() -> None:
    async with _client(_app(feedback=NoFeedback())) as client:
        run = await _run(client)
        answer = await client.post(
            FEEDBACK_PATH, json={"request_id": run["request_id"], "score": 1}
        )
    assert answer.status_code == 501
    assert answer.json()["detail"]["code"] == "feedback_not_configured"


async def test_a_store_outage_is_503_retryable() -> None:
    store = InMemoryFeedback()
    store.unavailable = True
    app = _app(feedback=store)
    async with _client(app) as client:
        run = await _run(client)
        answer = await client.post(
            FEEDBACK_PATH, json={"request_id": run["request_id"], "score": 1}
        )
    assert answer.status_code == 503
    assert answer.json()["detail"]["code"] == "feedback_unavailable"
    assert answer.headers["retry-after"]


async def test_a_state_outage_on_lookup_is_503() -> None:
    state = InMemoryState()
    async with _client(_app(state=state)) as client:
        run = await _run(client)
        state.fail_next()
        answer = await client.post(
            FEEDBACK_PATH, json={"request_id": run["request_id"], "score": 1}
        )
    assert answer.status_code == 503
    assert answer.json()["detail"]["code"] == "state_unavailable"


async def test_the_lookup_is_in_state_with_the_spec_ttl() -> None:
    now = [1000.0]
    state = InMemoryState(clock=lambda: now[0])
    app = _app(state=state, config=_config(lookup_ttl_s=60))
    async with _client(app) as client:
        run = await _run(client)
        raw = await state.get(lookup_key(AGENT, run["request_id"]))
        assert raw is not None
        assert json.loads(raw) == {"trace_id": trace_id_hex(run["trace_id"]), "agent": AGENT}
        now[0] += 61
        late = await client.post(FEEDBACK_PATH, json={"request_id": run["request_id"], "score": 1})
    assert late.status_code == 404


async def test_the_route_is_off_without_an_adapter_or_when_disabled() -> None:
    for config in (_config(adapter="none"), _config(enabled=False)):
        assert not feedback_on(config)
        app = _app(config=config)
        assert FEEDBACK_PATH not in app.openapi()["paths"]
        async with _client(app) as client:
            await _run(client)
            answer = await client.post(FEEDBACK_PATH, json={"request_id": "r", "score": 1})
        assert answer.status_code == 404
        assert app.state.ports.state.calls == [], "no lookup is written when the route is off"


async def test_the_openapi_operation_is_named_and_not_an_interface() -> None:
    op = _app().openapi()["paths"][FEEDBACK_PATH]["post"]
    assert op["operationId"] == "feedback"
    assert "x-chassis-interface" not in op
    assert {"202", "404", "409", "422", "501", "503"} <= set(op["responses"])


async def test_the_event_is_published_redacted_and_valid() -> None:
    bus = InMemoryBus()
    app = _app(bus=bus, config=_config(events=True))
    comment = "mail me at ana@example.com or +44 20 7946 0958"
    async with _client(app) as client:
        run = await _run(client)
        answer = await client.post(
            FEEDBACK_PATH,
            json={
                "request_id": run["request_id"],
                "score": 0,
                "label": "wrong",
                "comment": comment,
            },
        )
        events = app.state.feedback_events
        assert isinstance(events, FeedbackEvents)
        await events.wait(5)
    assert answer.status_code == 202
    (event,) = [e for t, e in bus.published if t == FEEDBACK_RECEIVED]
    wire = assert_valid_cloudevent(event)
    assert (wire["type"], wire["dataschema"], wire["subject"]) == (
        FEEDBACK_RECEIVED,
        DATASCHEMA,
        run["request_id"],
    )
    assert wire["traceparent"].split("-")[1] == trace_id_hex(run["trace_id"])
    data = wire["data"]
    schema = json.loads((Path(SCHEMA_DIR) / DATASCHEMA).read_text())
    jsonschema.validate(data, schema)
    assert data["comment"] == "mail me at <EMAIL_ADDRESS> or <PHONE_NUMBER>"
    assert (data["agent"], data["score"], data["label"]) == (AGENT, 0.0, "wrong")
    assert "ana@example.com" not in json.dumps(_telemetry(app).logs, default=str)


async def test_a_failed_publish_never_changes_the_202() -> None:
    bus = InMemoryBus()
    app = _app(bus=bus, config=_config(events=True))
    async with _client(app) as client:
        run = await _run(client)
        bus.fail_next_publish(10)
        answer = await client.post(
            FEEDBACK_PATH, json={"request_id": run["request_id"], "score": 1}
        )
        await app.state.feedback_events.wait(5)
    assert answer.status_code == 202
    assert not [t for t, _ in bus.published if t == FEEDBACK_RECEIVED]


async def test_events_off_publishes_nothing() -> None:
    bus = InMemoryBus()
    app = _app(bus=bus)
    async with _client(app) as client:
        run = await _run(client)
        await client.post(FEEDBACK_PATH, json={"request_id": run["request_id"], "score": 1})
    assert app.state.feedback_events is None
    assert not [t for t, _ in bus.published if t == FEEDBACK_RECEIVED]


async def test_the_score_name_from_the_spec_reaches_the_adapter() -> None:
    store = InMemoryFeedback()
    store.score_name = "default"  # type: ignore[attr-defined]
    async with _client(_app(feedback=store, config=_config(score_name="thumbs"))):
        assert store.score_name == "thumbs"  # type: ignore[attr-defined]
