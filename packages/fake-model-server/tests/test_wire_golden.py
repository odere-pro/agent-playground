"""Golden wire bytes for `/v1/chat/completions`, with ids and the clock pinned."""

from __future__ import annotations

import uuid

import httpx
import pytest
from fake_model_server import Script, create_app

SCRIPT = Script.model_validate(
    {
        "rules": [
            {
                "match": "lookup",
                "reply": "Calling now.",
                "tool_call": {"name": "glossary_lookup", "arguments": {"term": "SLM"}},
                "usage": {"prompt_tokens": 3, "completion_tokens": 4},
            },
            {"match": "simplify", "reply": "Plain words."},
        ]
    }
)
TOOLS = [{"type": "function", "function": {"name": "mcp__chassis__glossary_lookup"}}]

HEAD = '"id": "chatcmpl-000000000000", "object": "chat.completion.chunk", "created": 1700000000'
CALL = (
    '{"id": "call_00000000", "type": "function", "function": '
    '{"name": "mcp__chassis__glossary_lookup", "arguments": "{\\"term\\": \\"SLM\\"}"}}'
)


def _sse(delta: str, finish: str = "null", extra: str = "") -> str:
    choices = f'[{{"index": 0, "delta": {delta}, "finish_reason": {finish}}}]'
    return f'data: {{{HEAD}, "model": "m", "choices": {choices}{extra}}}\n\n'


TOOL_COMPLETE = (
    '{"id":"chatcmpl-000000000000","object":"chat.completion","created":1700000000,"model":"m",'
    '"choices":[{"index":0,"message":{"role":"assistant","content":"Calling now.","tool_calls":['
    '{"id":"call_00000000","type":"function","function":{"name":"mcp__chassis__glossary_lookup",'
    '"arguments":"{\\"term\\": \\"SLM\\"}"}}]},"finish_reason":"tool_calls"}],'
    '"usage":{"prompt_tokens":3,"completion_tokens":4,"total_tokens":7}}'
)
TEXT_COMPLETE = (
    '{"id":"chatcmpl-000000000000","object":"chat.completion","created":1700000000,"model":"m",'
    '"choices":[{"index":0,"message":{"role":"assistant","content":"Plain words."},'
    '"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}'
)
TOOL_STREAM = (
    _sse('{"role": "assistant", "content": ""}')
    + _sse('{"content": "Calling "}')
    + _sse('{"content": "now."}')
    + _sse(f'{{"tool_calls": [{{"index": 0, {CALL[1:]}]}}')
    + _sse(
        "{}",
        '"tool_calls"',
        ', "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}',
    )
    + "data: [DONE]\n\n"
)
TEXT_STREAM = (
    _sse('{"role": "assistant", "content": ""}')
    + _sse('{"content": "Plain "}')
    + _sse('{"content": "words."}')
    + _sse(
        "{}",
        '"stop"',
        ', "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}',
    )
    + "data: [DONE]\n\n"
)


@pytest.mark.parametrize(
    ("text", "stream", "expected"),
    [
        ("lookup SLM", False, TOOL_COMPLETE),
        ("lookup SLM", True, TOOL_STREAM),
        ("please simplify", False, TEXT_COMPLETE),
        ("please simplify", True, TEXT_STREAM),
    ],
    ids=["tool-complete", "tool-stream", "text-complete", "text-stream"],
)
async def test_chat_completions_returns_golden_bytes_when_ids_and_clock_are_pinned(
    monkeypatch: pytest.MonkeyPatch, text: str, stream: bool, expected: str
) -> None:
    """A scripted reply, with or without a tool call, gives the same bytes streamed or not."""
    monkeypatch.setattr("fake_model_server.app.uuid.uuid4", lambda: uuid.UUID(int=0xABCDEF))
    monkeypatch.setattr("fake_model_server.app.time.time", lambda: 1700000000.0)
    app = create_app(SCRIPT)
    body = {"model": "m", "messages": [{"role": "user", "content": text}], "stream": stream}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fake"
    ) as client:
        response = await client.post("/v1/chat/completions", json={**body, "tools": TOOLS})

    assert response.status_code == 200
    assert response.text == expected
