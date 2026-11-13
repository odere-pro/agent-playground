"""012 H-3: request metadata carries the trace and request IDs, and nothing that picks whose
spend it is. Spend is bound by the service's virtual key, never by a tag in the request."""

from __future__ import annotations

import json

import httpx
from chassis.adapters.litellm import LiteLLMModel
from chassis.core.envelope import Budget
from chassis.ports.model import ModelMessage
from chassis.server.correlation import RunRecord
from chassis.server.model_admission import BoundModel

MESSAGES: list[ModelMessage] = [ModelMessage(role="user", content="hello")]
_COMPLETION = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "model": "answered-by",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
}
_SSE = (
    b'data: {"id":"c","object":"chat.completion.chunk","model":"m","choices":'
    b'[{"index":0,"delta":{"content":"hi"},"finish_reason":"stop"}]}\n\n'
    b"data: [DONE]\n\n"
)


def _router() -> tuple[list[httpx.Request], httpx.MockTransport]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if json.loads(request.content).get("stream"):
            return httpx.Response(200, content=_SSE, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=_COMPLETION)

    return seen, httpx.MockTransport(handler)


async def test_request_metadata_carries_the_trace_and_request_ids_only() -> None:
    seen, transport = _router()
    model = LiteLLMModel("http://router/v1", "sk-test", agent="echo", transport=transport)
    record = RunRecord(request_id="req-1", agent="other-agent", trace_id="a" * 32, budget=Budget())
    bound = BoundModel(model, record)

    await bound.complete(MESSAGES, route="big-default")
    _ = [chunk async for chunk in bound.stream(MESSAGES, route="big-default")]

    assert len(seen) == 2
    for request in seen:
        body = json.loads(request.content)
        assert body["metadata"] == {"trace_id": "a" * 32, "request_id": "req-1"}
        assert not [h for h in request.headers if h.lower().startswith("x-litellm-")]


async def test_an_uncorrelated_call_sends_no_metadata_and_no_tag() -> None:
    seen, transport = _router()
    model = LiteLLMModel("http://router/v1", agent="echo", transport=transport)

    await BoundModel(model, None).complete(MESSAGES, route="big-default")

    body = json.loads(seen[0].content)
    assert "metadata" not in body
    assert "x-litellm-tags" not in seen[0].headers
