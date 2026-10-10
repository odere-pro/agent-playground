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
    return [
        json.loads(line[6:])
        for line in text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]


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


TOOL_LOOP: list[dict[str, Any]] = [
    {"role": "user", "content": "glossary: SLM"},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "glossary_lookup", "arguments": '{"term": "SLM"}'},
            }
        ],
    },
    {"role": "tool", "tool_call_id": "c1", "content": "SLM: small language model"},
]


async def test_a_scripted_tool_loop_ends(client: httpx.AsyncClient) -> None:
    """The example script calls the tool on "glossary"; after the tool result its `after_tool`
    rule answers instead of calling the tool again.
    """
    complete = (await client.post("/v1/chat/completions", json={"messages": TOOL_LOOP})).json()
    message = complete["choices"][0]["message"]
    assert "tool_calls" not in message and complete["choices"][0]["finish_reason"] == "stop"
    assert message["content"] == "From the glossary: SLM means small language model."
    chunks = _sse_chunks(
        (
            await client.post("/v1/chat/completions", json={"messages": TOOL_LOOP, "stream": True})
        ).text
    )
    assert not [c for c in chunks if c["choices"][0]["delta"].get("tool_calls")]


def test_after_tool_rules_match_only_after_a_tool() -> None:
    script = Script.model_validate(
        {
            "rules": [
                {"match": "glossary", "tool_call": {"name": "glossary_lookup"}},
                {"after_tool": True, "match": "nothing here", "reply": "wrong"},
                {"after_tool": True, "match": "small language", "reply": "An SLM is small."},
                {"reply": "catch-all"},
            ],
            "default_reply": "done",
        }
    )
    assert script.pick(TOOL_LOOP).reply == "An SLM is small."
    assert script.pick(TOOL_LOOP[:1]).tool_call is not None
    no_match = [*TOOL_LOOP[:2], {**TOOL_LOOP[2], "content": "unrelated"}]
    assert script.pick(no_match).reply == "done"


async def test_the_server_records_each_request_body() -> None:
    app = create_app(Script.from_yaml(SCRIPT))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fake"
    ) as client:
        await client.post("/v1/chat/completions", json={"model": "m", "messages": TOOL_LOOP})
    assert [call["messages"] for call in app.state.calls] == [TOOL_LOOP]


async def test_the_call_log_is_bounded_and_counts_every_call() -> None:
    app = create_app(Script.from_yaml(SCRIPT), max_calls=3)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fake"
    ) as client:
        for n in range(5):
            body = {"model": f"m{n}", "messages": [{"role": "user", "content": "hi"}]}
            await client.post("/v1/chat/completions", json=body)
    assert [call["model"] for call in app.state.calls] == ["m2", "m3", "m4"], "the last 3, in order"
    assert app.state.calls[-1]["model"] == "m4"
    assert app.state.calls_total == 5


def test_the_call_log_keeps_1000_by_default() -> None:
    assert create_app(Script.from_yaml(SCRIPT)).state.calls.maxlen == 1000


def _offered(*names: str) -> list[dict[str, Any]]:
    return [{"type": "function", "function": {"name": n, "parameters": {}}} for n in names]


@pytest.mark.parametrize("stream", [False, True])
async def test_a_scripted_tool_name_resolves_to_the_one_offered_name_ending_with_it(
    client: httpx.AsyncClient, stream: bool
) -> None:
    """A client that prefixes tool names (the Claude CLI: `mcp__<server>__<name>`) gets the call
    by the name it offered, so one script serves every engine."""
    body: dict[str, Any] = {
        "messages": [{"role": "user", "content": "glossary: SLM"}],
        "tools": _offered("mcp__chassis__glossary_lookup", "mcp__chassis__acronym_expand"),
        "stream": stream,
    }
    response = await client.post("/v1/chat/completions", json=body)
    if stream:
        chunks = [
            c for c in _sse_chunks(response.text) if c["choices"][0]["delta"].get("tool_calls")
        ]
        name = chunks[0]["choices"][0]["delta"]["tool_calls"][0]["function"]["name"]
    else:
        name = response.json()["choices"][0]["message"]["tool_calls"][0]["function"]["name"]
    assert name == "mcp__chassis__glossary_lookup"


@pytest.mark.parametrize(
    "tools",
    [
        None,  # no tool list: the scripted name as is
        _offered("glossary_lookup", "mcp__chassis__glossary_lookup"),  # an exact match wins
        _offered("a__glossary_lookup", "b__glossary_lookup"),  # two candidates: no guess
        _offered("acronym_expand"),  # no candidate: the scripted name as is
        _offered("xglossary_lookup"),  # a bare suffix is not a match
    ],
)
async def test_the_scripted_tool_name_stays_when_no_single_offered_name_fits(
    client: httpx.AsyncClient, tools: list[dict[str, Any]] | None
) -> None:
    body: dict[str, Any] = {"messages": [{"role": "user", "content": "glossary: SLM"}]}
    if tools is not None:
        body["tools"] = tools
    complete = (await client.post("/v1/chat/completions", json=body)).json()
    assert (
        complete["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "glossary_lookup"
    )
