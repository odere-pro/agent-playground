"""Exit criterion 5 (feedback part): `POST /v1/feedback` behaves the same whatever the engine,
because everything lives in the chassis. With the real adapter over the fake Langfuse API: a
repeat is one score, an unknown request id is 404, a second replica that shares only `StatePort`
takes the feedback for a run the first one served (no replica keeps state), and the event
`agents.feedback.received.v1` is published, redacted, and valid against `feedback.v0.json`.
"""

from __future__ import annotations

import json

import jsonschema
from chassis.core.trace import trace_id_hex
from chassis.fakes.events import InMemoryBus
from chassis.ports.state import InMemoryState
from chassis.schemas import SCHEMA_DIR
from chassis.server.feedback import DATASCHEMA, FEEDBACK_RECEIVED, FeedbackEvents
from chassis_contracts.events import assert_valid_cloudevent
from poc07_fb_harness import chassis, client, fake_langfuse, run


async def test_poc07_fb_a_repeat_is_one_score_on_the_trace() -> None:
    """Feedback scope: "onto the call's trace"; a repeat is one score, not two."""
    fake = fake_langfuse()
    async with client(chassis(fake)) as http:
        answer = await run(http)
        body = {"request_id": answer["request_id"], "score": 0, "comment": "too long"}
        first = await http.post("/v1/feedback", json=body)
        second = await http.post("/v1/feedback", json={**body, "score": 1})
    assert (first.status_code, second.status_code) == (202, 202)
    assert first.json() == second.json()
    (score,) = fake.scores.values()
    assert fake.writes[score["id"]] == 2
    assert score["value"] == 1
    assert score["timestamp"], "the same first time is sent again, so Langfuse updates in place"


async def test_poc07_fb_an_unknown_request_id_is_404() -> None:
    """Feedback scope: "onto the call's trace"; an unknown request id is 404, no score."""
    fake = fake_langfuse()
    async with client(chassis(fake)) as http:
        answer = await http.post("/v1/feedback", json={"request_id": "never-ran", "score": 1})
    assert answer.status_code == 404
    assert answer.json()["detail"]["code"] == "unknown_request"
    assert fake.scores == {}


async def test_poc07_fb_a_second_replica_sharing_only_state_takes_the_feedback() -> None:
    """Feedback scope: "onto the call's trace"; a replica sharing only state finds it."""
    fake, shared = fake_langfuse(), InMemoryState()
    one, two = chassis(fake, state=shared), chassis(fake, state=shared)
    async with client(one) as http_one:
        answer = await run(http_one)
    async with client(two) as http_two:
        feedback = await http_two.post(
            "/v1/feedback", json={"request_id": answer["request_id"], "score": 1}
        )
    assert feedback.status_code == 202
    assert fake.scores[feedback.json()["feedback_id"]]["traceId"] == trace_id_hex(
        answer["trace_id"]
    )


async def test_poc07_fb_the_event_is_published_redacted_and_valid() -> None:
    """Feedback scope: "a new event `agents.feedback.received.v1`", redacted and valid."""
    fake, bus = fake_langfuse(), InMemoryBus()
    app = chassis(fake, bus=bus, events=True)
    async with client(app) as http:
        answer = await run(http)
        feedback = await http.post(
            "/v1/feedback",
            json={
                "request_id": answer["request_id"],
                "score": 0,
                "label": "wrong",
                "comment": "call me on +44 20 7946 0958",
            },
        )
        events = app.state.feedback_events
        assert isinstance(events, FeedbackEvents)
        assert await events.wait(5) == 0
    assert feedback.status_code == 202
    (event,) = [e for t, e in bus.published if t == FEEDBACK_RECEIVED]
    wire = assert_valid_cloudevent(event)
    assert (wire["type"], wire["dataschema"]) == (FEEDBACK_RECEIVED, DATASCHEMA)
    schema = json.loads((SCHEMA_DIR / DATASCHEMA).read_text())
    jsonschema.validate(wire["data"], schema)
    assert wire["data"]["trace_id"] == trace_id_hex(answer["trace_id"])
    assert wire["data"]["comment"] == "call me on <PHONE_NUMBER>"
    stored = fake.scores[feedback.json()["feedback_id"]]
    assert stored["comment"] == "call me on +44 20 7946 0958", "the store keeps what the user wrote"
