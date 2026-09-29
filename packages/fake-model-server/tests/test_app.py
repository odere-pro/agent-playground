from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fake_model_server import Script, create_app

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "example.yaml"


@pytest.fixture
def client() -> httpx.AsyncClient:
    app = create_app(Script.from_yaml(SCRIPT))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fake")


def _sse_chunks(text: str) -> list[dict[str, Any]]:
    out = []
    for line in text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            out.append(json.loads(line[6:]))
    return out


async def test_health_and_models(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).json() == {"status": "ok"}
    assert (await client.get("/v1/models")).json()["data"][0]["id"] == "fake-model"


async def test_complete_and_stream_give_the_same_text(client: httpx.AsyncClient) -> None:
    body = {"model": "x", "messages": [{"role": "user", "content": "please simplify this"}]}
    complete = (await client.post("/v1/chat/completions", json=body)).json()
    assert (
        complete["choices"][0]["message"]["content"] == "Plain words. Short sentences. Same facts."
    )
    assert complete["usage"] == {"prompt_tokens": 42, "completion_tokens": 9, "total_tokens": 51}

    streamed = await client.post("/v1/chat/completions", json={**body, "stream": True})
    assert streamed.headers["content-type"].startswith("text/event-stream")
    chunks = _sse_chunks(streamed.text)
    text = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks)
    assert text == complete["choices"][0]["message"]["content"]
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert chunks[-1]["usage"]["total_tokens"] == 51
    assert streamed.text.rstrip().endswith("data: [DONE]")


async def test_tool_call(client: httpx.AsyncClient) -> None:
    body = {"messages": [{"role": "user", "content": "glossary: SLM"}]}
    complete = (await client.post("/v1/chat/completions", json=body)).json()
    call = complete["choices"][0]["message"]["tool_calls"][0]
    assert call["function"]["name"] == "glossary_lookup"
    assert json.loads(call["function"]["arguments"]) == {"term": "SLM"}
    assert complete["choices"][0]["finish_reason"] == "tool_calls"

    chunks = _sse_chunks(
        (await client.post("/v1/chat/completions", json={**body, "stream": True})).text
    )
    tool_chunks = [c for c in chunks if c["choices"][0]["delta"].get("tool_calls")]
    assert (
        tool_chunks[0]["choices"][0]["delta"]["tool_calls"][0]["function"]["name"]
        == "glossary_lookup"
    )


async def test_scripted_error(client: httpx.AsyncClient) -> None:
    body = {"messages": [{"role": "user", "content": "fail now"}]}
    response = await client.post("/v1/chat/completions", json=body)
    assert response.status_code == 500
    assert response.json()["detail"] == "scripted failure"


async def test_default_reply(client: httpx.AsyncClient) -> None:
    body = {"messages": [{"role": "user", "content": "anything else"}]}
    assert (await client.post("/v1/chat/completions", json=body)).json()["choices"][0]["message"][
        "content"
    ] == "ok"
