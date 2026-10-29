"""The fake Langfuse API, in process over an ASGI transport: no socket, no network, no key."""

from __future__ import annotations

from typing import Any

import httpx
from fake_langfuse import FakeLangfuse, create_app

PK, SK = "pk-lf-test", "sk-lf-test"
TRACE = "0af7651916cd43dd8448eb211c80319c"  # pragma: allowlist secret


def client(state: FakeLangfuse, auth: tuple[str, str] | None = (PK, SK)) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(state)),
        base_url="http://langfuse",
        auth=auth,
    )


def score(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "fb-1",
        "traceId": TRACE,
        "name": "user-feedback",
        "value": 1.0,
        "dataType": "NUMERIC",
        "comment": "good",
        "metadata": {"request_id": "req-1"},
    }
    body.update(overrides)
    return body


async def test_scores_need_basic_auth_with_the_project_keys() -> None:
    state = FakeLangfuse(PK, SK)
    for auth in (None, (PK, "wrong"), ("other", SK)):
        async with client(state, auth) as http:
            answer = await http.post("/api/public/scores", json=[score()])
        assert answer.status_code == 401
    assert state.scores == {}


async def test_a_batch_is_accepted_with_202_and_stored_by_id() -> None:
    state = FakeLangfuse(PK, SK)
    async with client(state) as http:
        answer = await http.post("/api/public/scores", json=[score()])
    assert answer.status_code == 202
    assert answer.json() == {"message": "Accepted"}
    stored = state.scores["fb-1"]
    assert (stored["traceId"], stored["value"], stored["comment"]) == (TRACE, 1.0, "good")
    assert stored["metadata"] == {"request_id": "req-1"}


async def test_the_same_id_twice_is_one_score_the_last_write_wins() -> None:
    state = FakeLangfuse(PK, SK)
    async with client(state) as http:
        await http.post("/api/public/scores", json=[score()])
        await http.post("/api/public/scores", json=[score(value=0.0)])
    assert list(state.scores) == ["fb-1"]
    assert state.scores["fb-1"]["value"] == 0.0
    assert state.writes["fb-1"] == 2


async def test_a_bad_score_in_a_batch_is_a_207_with_counts() -> None:
    state = FakeLangfuse(PK, SK)
    async with client(state) as http:
        answer = await http.post(
            "/api/public/scores", json=[score(), score(id="fb-2", value="high")]
        )
    assert answer.status_code == 207
    body = answer.json()
    assert (body["accepted"], body["rejected"]) == (1, 1)
    assert body["errors"][0]["message"]
    assert list(state.scores) == ["fb-1"]


async def test_a_body_that_is_not_a_list_is_400() -> None:
    state = FakeLangfuse(PK, SK)
    async with client(state) as http:
        answer = await http.post("/api/public/scores", json=score())
    assert answer.status_code == 400


async def test_unavailable_answers_503_and_stores_nothing() -> None:
    state = FakeLangfuse(PK, SK)
    state.unavailable = True
    async with client(state) as http:
        answer = await http.post("/api/public/scores", json=[score()])
    assert answer.status_code == 503
    assert state.scores == {}


async def test_a_score_reads_back_by_id_and_through_the_v3_list() -> None:
    state = FakeLangfuse(PK, SK)
    async with client(state) as http:
        await http.post("/api/public/scores", json=[score()])
        one = await http.get("/api/public/scores/fb-1")
        missing = await http.get("/api/public/scores/nope")
        listed = await http.get(
            "/api/public/v3/scores", params={"id": "fb-1", "fields": "details,subject"}
        )
    assert one.status_code == 200
    assert one.json()["traceId"] == TRACE
    assert missing.status_code == 404
    data = listed.json()["data"]
    assert len(data) == 1
    assert data[0]["subject"] == {"kind": "trace", "id": TRACE}
    assert data[0]["comment"] == "good"
    assert data[0]["dataType"] == "NUMERIC"


async def test_otlp_trace_bodies_are_recorded_as_sent() -> None:
    state = FakeLangfuse(PK, SK)
    async with client(state) as http:
        answer = await http.post(
            "/api/public/otel/v1/traces",
            content=b"\x0a\x00",
            headers={"content-type": "application/x-protobuf"},
        )
        refused = await http.post("/api/public/otel/v1/traces", content=b"x", auth=(PK, "wrong"))
    assert answer.status_code == 200
    assert refused.status_code == 401
    assert [(t.content_type, t.body) for t in state.traces] == [
        ("application/x-protobuf", b"\x0a\x00")
    ]


async def test_health_needs_no_auth() -> None:
    async with client(FakeLangfuse(PK, SK), auth=None) as http:
        answer = await http.get("/api/public/health")
    assert answer.status_code == 200
