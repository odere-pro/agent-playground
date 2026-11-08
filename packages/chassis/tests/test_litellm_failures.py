"""`LiteLLMModel` under failure (CH-16): LiteLLM's budget refusal maps to one code, `timeout_s`
bounds the call and the httpx timeout it sends, and a call that passes its deadline closes the
upstream connection. The last test runs the fake model server on a real uvicorn over a Unix
socket, so the close is a real socket close that the fake sees; everything else is
`httpx.MockTransport` or the ASGI transport, no socket.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest
from a2a_uds import serve_uds
from chassis.adapters.litellm import LiteLLMModel
from chassis.adapters.litellm.client import DEFAULT_TIMEOUT_S
from chassis.ports.model import SERVICE_BUDGET_EXHAUSTED, TIMEOUT, ModelError, ModelMessage
from fake_model_server import Script, create_app
from fake_model_server.script import Rule
from poc_harness.waits import wait_until_async

MESSAGES: list[ModelMessage] = [ModelMessage(role="user", content="hello")]
LITELLM_REFUSAL = {
    "error": {
        "message": "ExceededBudget: Crossed spend threshold for key: sk-...9Xy2; max_budget 1.0",
        "type": "budget_exceeded",
        "param": None,
        "code": "400",
    }
}
"""LiteLLM's `ProxyException` for `BudgetExceededError`: `type` is the field to match on; the
status has been 400 and 429 across releases."""


def _refusing(status: int, body: dict[str, Any]) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status, json=body))


@pytest.mark.parametrize("status", [400, 429])
async def test_litellms_budget_refusal_is_service_budget_exhausted(status: int) -> None:
    """Whatever status LiteLLM uses, `error.type == budget_exceeded` is the service's budget."""
    model = LiteLLMModel(
        "http://router/v1", "sk-secret", transport=_refusing(status, LITELLM_REFUSAL)
    )
    with pytest.raises(ModelError) as exc:
        await model.complete(MESSAGES, route="big-default")
    assert exc.value.code == SERVICE_BUDGET_EXHAUSTED
    assert exc.value.retryable is False
    assert "sk-secret" not in exc.value.message


async def test_the_refusal_is_the_same_code_on_a_stream() -> None:
    model = LiteLLMModel("http://router/v1", transport=_refusing(400, LITELLM_REFUSAL))
    with pytest.raises(ModelError) as exc:
        async for _ in model.stream(MESSAGES, route="big-default"):
            pass
    assert exc.value.code == SERVICE_BUDGET_EXHAUSTED


async def test_a_plain_400_stays_http_400() -> None:
    """Only the budget type maps; a 400 for any other reason keeps `http_400`."""
    body = {"error": {"message": "bad request", "type": "invalid_request_error", "code": "400"}}
    model = LiteLLMModel("http://router/v1", transport=_refusing(400, body))
    with pytest.raises(ModelError) as exc:
        await model.complete(MESSAGES, route="big-default")
    assert exc.value.code == "http_400"


async def test_timeout_s_caps_the_httpx_timeout_sent_with_the_request() -> None:
    """A call's `timeout_s` below the adapter default becomes the request's httpx timeout, so a
    hung connect or read ends by then too; above it the default stays."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    model = LiteLLMModel("http://router/v1", transport=httpx.MockTransport(handler))
    await model.complete(MESSAGES, route="r", timeout_s=2.5)
    await model.complete(MESSAGES, route="r", timeout_s=DEFAULT_TIMEOUT_S * 10)
    await model.complete(MESSAGES, route="r")
    timeouts = [r.extensions["timeout"]["read"] for r in seen]
    assert timeouts == [2.5, DEFAULT_TIMEOUT_S, DEFAULT_TIMEOUT_S]


async def test_a_call_past_its_deadline_closes_the_upstream_connection() -> None:
    """The fake model server on a real socket, with a 30 s answer: a call with `timeout_s` 0.2 s
    raises `timeout` and the fake sees the client gone (`state.aborted`) soon after."""
    rules = [Rule(match="slow", reply="late", delay_s=30.0)]
    fake = create_app(Script(rules=rules))
    async with serve_uds(fake) as path:
        model = LiteLLMModel("http://fake/v1", transport=httpx.AsyncHTTPTransport(uds=path))
        try:
            started = time.monotonic()
            with pytest.raises(ModelError) as exc:
                await model.complete(
                    [ModelMessage(role="user", content="slow")], route="r", timeout_s=0.2
                )
            assert exc.value.code == TIMEOUT and exc.value.retryable
            assert time.monotonic() - started < 2.0
            await wait_until_async(lambda: fake.state.aborted >= 1, "the fake saw the close", 5)
        finally:
            await model.aclose()
    assert fake.state.calls_total == 1, "no retry at the adapter"


async def test_a_stream_past_its_deadline_closes_the_upstream_connection() -> None:
    rules = [Rule(match="slow", reply="late", delay_s=30.0)]
    fake = create_app(Script(rules=rules))
    async with serve_uds(fake) as path:
        model = LiteLLMModel("http://fake/v1", transport=httpx.AsyncHTTPTransport(uds=path))
        try:
            with pytest.raises(ModelError) as exc:
                async for _ in model.stream(
                    [ModelMessage(role="user", content="slow")], route="r", timeout_s=0.2
                ):
                    pass
            assert exc.value.code == TIMEOUT
            await wait_until_async(lambda: fake.state.aborted >= 1, "the fake saw the close", 5)
        finally:
            await model.aclose()


def test_the_refusal_body_is_what_litellm_sends() -> None:
    """Pin the shape this file replays, so a LiteLLM upgrade that changes it is a visible edit."""
    assert json.loads(json.dumps(LITELLM_REFUSAL))["error"]["type"] == "budget_exceeded"
