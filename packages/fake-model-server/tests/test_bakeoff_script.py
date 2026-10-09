"""`scripts/bakeoff.yaml` loads and answers each step of the three PoC-6 tasks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fake_model_server import Script, create_app

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bakeoff.yaml"
GLOSSARY_RESULT = {"term": "SLM", "definition": "A small language model: a model small enough."}
ACRONYM_RESULT = {"acronym": "RAG", "expansion": "retrieval-augmented generation"}


@pytest.fixture
def client() -> httpx.AsyncClient:
    app = create_app(Script.from_yaml(SCRIPT))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fake")


async def _message(client: httpx.AsyncClient, messages: list[dict[str, Any]]) -> dict[str, Any]:
    response = await client.post("/v1/chat/completions", json={"messages": messages})
    message: dict[str, Any] = response.json()["choices"][0]["message"]
    return message


def _tool_call(message: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    call = message["tool_calls"][0]["function"]
    return call["name"], json.loads(call["arguments"])


async def test_smoke_replies_hello(client: httpx.AsyncClient) -> None:
    message = await _message(client, [{"role": "user", "content": "simplify: Hello."}])
    assert message["content"] == "Hello."
    assert "tool_calls" not in message


async def test_simplifier_replies_with_the_two_facts(client: httpx.AsyncClient) -> None:
    text = "simplify: The SLM was released by Acme in 2026, reducing costs by 30 percent."
    message = await _message(client, [{"role": "user", "content": text}])
    assert message["content"] == "Acme put out the SLM in 2026. It cut costs by 30 percent."
    assert "tool_calls" not in message


async def test_lookup_runs_two_tool_calls_then_answers(client: httpx.AsyncClient) -> None:
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "lookup: what does SLM mean, and what does RAG stand for?"}
    ]
    first = await _message(client, messages)
    assert _tool_call(first) == ("glossary_lookup", {"term": "SLM"})

    messages += [
        first,
        {"role": "tool", "tool_call_id": "c1", "content": json.dumps(GLOSSARY_RESULT)},
    ]
    second = await _message(client, messages)
    assert _tool_call(second) == ("acronym_expand", {"acronym": "RAG"})

    messages += [
        second,
        {"role": "tool", "tool_call_id": "c2", "content": json.dumps(ACRONYM_RESULT)},
    ]
    last = await _message(client, messages)
    assert last["content"] == (
        "SLM means a small language model. RAG stands for retrieval-augmented generation."
    )
    assert "tool_calls" not in last


async def test_a_result_with_both_texts_ends_the_loop(client: httpx.AsyncClient) -> None:
    both = {"text": "small language model; retrieval-augmented generation"}
    messages = [
        {"role": "user", "content": "lookup: SLM and RAG"},
        {"role": "tool", "tool_call_id": "c1", "content": json.dumps(both)},
    ]
    message = await _message(client, messages)
    assert message["content"].endswith("retrieval-augmented generation.")
    assert "tool_calls" not in message
