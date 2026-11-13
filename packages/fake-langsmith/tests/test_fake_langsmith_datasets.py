"""The fake LangSmith dataset routes (CH-12), in process over an ASGI transport: no socket."""

from __future__ import annotations

import httpx
from fake_langsmith import FakeLangSmith, create_app

KEY = "lsv2-test-key"  # pragma: allowlist secret


def client(state: FakeLangSmith, key: str = KEY) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(state)),
        base_url="http://langsmith",
        headers={"x-api-key": key},
    )


async def test_fake_langsmith_dataset_example_experiment_and_run_are_recorded() -> None:
    state = FakeLangSmith(api_key=KEY)
    async with client(state) as http:
        dataset = (await http.post("/api/v1/datasets", json={"name": "golden"})).json()
        dup = await http.post("/api/v1/datasets", json={"name": "golden"})
        found = (await http.get("/api/v1/datasets", params={"name": "golden"})).json()
        example = await http.post(
            "/api/v1/examples", json={"dataset_id": dataset["id"], "inputs": {"text": "hi"}}
        )
        session = await http.post(
            "/api/v1/sessions",
            json={
                "name": "echo-python-deepeval",
                "reference_dataset_id": dataset["id"],
                "extra": {"metadata": {"passed": 1}},
            },
        )
        run = await http.post(
            "/api/v1/runs",
            json={
                "id": "r1",
                "name": "echo-python-deepeval",
                "session_id": session.json()["id"],
                "reference_example_id": example.json()["id"],
                "outputs": {"pass": True},
            },
        )
    assert dup.status_code == 409
    assert [d["id"] for d in found] == [dataset["id"]]
    assert example.status_code == 200 and run.status_code == 202
    assert state.experiments_of(dataset["id"])[0].metadata == {"passed": 1}
    assert state.runs_of(session.json()["id"])[0].outputs == {"pass": True}


async def test_fake_langsmith_dataset_routes_refuse_unknown_ids_and_bad_bodies() -> None:
    state = FakeLangSmith(api_key=KEY)
    async with client(state) as http:
        no_dataset = await http.post(
            "/api/v1/examples", json={"dataset_id": "x", "inputs": {"a": 1}}
        )
        bad = await http.post("/api/v1/examples", json={"inputs": {"a": 1}})
        no_session = await http.post(
            "/api/v1/runs",
            json={"id": "r", "name": "n", "session_id": "s", "reference_example_id": "e"},
        )
    assert (no_dataset.status_code, bad.status_code, no_session.status_code) == (404, 422, 404)
    assert state.runs == []
    assert state.examples == []


async def test_fake_langsmith_an_example_with_empty_inputs_is_accepted() -> None:
    state = FakeLangSmith(api_key=KEY)
    async with client(state) as http:
        dataset = (await http.post("/api/v1/datasets", json={"name": "golden"})).json()
        empty = await http.post(
            "/api/v1/examples", json={"dataset_id": dataset["id"], "inputs": {}}
        )
        null = await http.post(
            "/api/v1/examples", json={"dataset_id": dataset["id"], "inputs": None}
        )
    assert (empty.status_code, null.status_code) == (200, 422)
    assert [e.inputs for e in state.examples_of(dataset["id"])] == [{}]


async def test_fake_langsmith_dataset_routes_need_the_key() -> None:
    state = FakeLangSmith(api_key=KEY)
    async with client(state, key="lsv2-wrong") as http:  # pragma: allowlist secret
        answer = await http.post("/api/v1/datasets", json={"name": "golden"})
    assert answer.status_code == 401
    assert state.datasets == []
