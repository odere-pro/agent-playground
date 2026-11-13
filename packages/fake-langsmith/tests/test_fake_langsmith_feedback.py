"""The fake LangSmith feedback route (130 CH-11), in process over an ASGI transport."""

from __future__ import annotations

import uuid

import httpx
from fake_langsmith import FakeLangSmith, create_app

KEY = "lsv2-test-key"  # pragma: allowlist secret
TRACE = "0af7651916cd43dd8448eb211c80319c"  # pragma: allowlist secret (a W3C example trace id)
RUN = str(uuid.UUID(TRACE))
FEEDBACK = "7d0b6c1e-3c39-5f0f-9a5e-3f6a8b6c2d11"


def client(state: FakeLangSmith, key: str | None = KEY) -> httpx.AsyncClient:
    headers = {"x-api-key": key} if key is not None else {}
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(state)),
        base_url="http://langsmith",
        headers=headers,
    )


def record(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "id": FEEDBACK,
        "run_id": RUN,
        "trace_id": RUN,
        "key": "user-feedback",
        "score": 1.0,
        "feedback_source": {"type": "api", "metadata": {"feedback_id": "f-1"}},
    }
    return body | overrides


async def test_a_feedback_is_stored_and_read_back_by_id_and_by_run() -> None:
    state = FakeLangSmith(api_key=KEY)
    async with client(state) as http:
        answer = await http.post("/api/v1/feedback", json=record())
        by_id = await http.get(f"/fake/feedback/{FEEDBACK}")
        by_run = await http.get(f"/fake/runs/{RUN}/feedback")
        by_hex = await http.get(f"/fake/runs/{TRACE}/feedback")
    assert answer.status_code == 200
    assert answer.json() == record()
    assert by_id.json() == record()
    assert by_run.json() == {"feedback": [record()]}
    assert by_hex.json() == by_run.json()
    assert state.feedback_on(TRACE) == [record()]


async def test_the_same_id_twice_is_one_record_with_the_last_body() -> None:
    state = FakeLangSmith(api_key=KEY)
    async with client(state) as http:
        await http.post("/api/v1/feedback", json=record())
        await http.post("/api/v1/feedback", json=record(score=0.0))
    assert list(state.feedback) == [FEEDBACK]
    assert state.feedback[FEEDBACK]["score"] == 0.0


async def test_a_missing_or_wrong_key_is_401_and_stores_no_feedback() -> None:
    state = FakeLangSmith(api_key=KEY)
    for key in (None, "wrong"):
        async with client(state, key) as http:
            answer = await http.post("/api/v1/feedback", json=record())
        assert answer.status_code == 401
    assert state.feedback == {}


async def test_a_body_without_an_id_or_a_uuid_run_id_is_422() -> None:
    state = FakeLangSmith(api_key=KEY)
    async with client(state) as http:
        no_id = await http.post("/api/v1/feedback", json={"run_id": RUN, "key": "k"})
        bad_run = await http.post("/api/v1/feedback", json=record(run_id="not-a-uuid"))
        not_json = await http.post("/api/v1/feedback", content=b"{nope")
        a_list = await http.post("/api/v1/feedback", json=[record()])
    assert [a.status_code for a in (no_id, bad_run, not_json, a_list)] == [422] * 4
    assert state.feedback == {}


async def test_an_outage_is_503_and_stores_nothing() -> None:
    state = FakeLangSmith(api_key=KEY, unavailable=True)
    async with client(state) as http:
        answer = await http.post("/api/v1/feedback", json=record())
    assert answer.status_code == 503
    assert state.feedback == {}


async def test_unknown_feedback_is_404_and_a_run_without_feedback_is_empty() -> None:
    async with client(FakeLangSmith(api_key=KEY)) as http:
        missing = await http.get(f"/fake/feedback/{FEEDBACK}")
        empty = await http.get(f"/fake/runs/{RUN}/feedback")
        bad = await http.get("/fake/runs/not-a-run/feedback")
    assert missing.status_code == 404
    assert empty.json() == {"feedback": []}
    assert bad.status_code == 404
