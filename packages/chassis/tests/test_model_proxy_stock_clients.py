"""A stock `openai` and a stock `anthropic` client, pointed at the proxy app, get complete and
streamed answers from the fake `ModelPort` (CH-2). In process: each SDK gets an HTTP client whose
transport is the proxy app (`httpx2`, the client both SDKs type), so no socket opens and no key
is real.
"""

from __future__ import annotations

from typing import Any

import anthropic
import httpx2
import openai
from anthropic.types import MessageParam
from chassis.fakes import ScriptedModel, ScriptRule
from chassis.server import ChassisConfig, create_app
from chassis.server.proxy_app import create_proxy_app
from openai.types.chat import ChatCompletionMessageParam
from proxy_fakes import proxy_ports

CONFIG: dict[str, Any] = {
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
        "model": {"route": "big-default"},
    },
}
REPLY = "Plain words. Short sentences."
TEXT = "simplify: the quick brown fox"
OPENAI_PROMPT: list[ChatCompletionMessageParam] = [{"role": "user", "content": TEXT}]
ANTHROPIC_PROMPT: list[MessageParam] = [{"role": "user", "content": TEXT}]
BASE = "http://chassis"
PLACEHOLDER_KEY = "not-a-key"  # the SDKs need one; the proxy ignores it


def _app() -> Any:
    model = ScriptedModel([ScriptRule(match="simplify", reply=REPLY)])
    return create_app(ChassisConfig.model_validate(CONFIG), proxy_ports(model))


def _http(app: Any) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=create_proxy_app(app)))


async def test_a_stock_openai_client_gets_a_complete_and_a_streamed_answer() -> None:
    app = _app()
    async with _http(app) as http, app.router.lifespan_context(app):
        client = openai.AsyncOpenAI(
            base_url=f"{BASE}/v1", api_key=PLACEHOLDER_KEY, http_client=http
        )
        done = await client.chat.completions.create(
            model="big-default", messages=OPENAI_PROMPT, max_tokens=64
        )
        stream = await client.chat.completions.create(
            model="big-default", messages=OPENAI_PROMPT, max_tokens=64, stream=True
        )
        parts = [c.choices[0].delta.content or "" async for c in stream if c.choices]
    assert done.choices[0].message.content == REPLY
    assert "".join(parts) == REPLY


async def test_a_stock_anthropic_client_gets_a_complete_and_a_streamed_answer() -> None:
    app = _app()
    async with _http(app) as http, app.router.lifespan_context(app):
        client = anthropic.AsyncAnthropic(base_url=BASE, api_key=PLACEHOLDER_KEY, http_client=http)
        done = await client.messages.create(
            model="big-default", messages=ANTHROPIC_PROMPT, max_tokens=64
        )
        async with client.messages.stream(
            model="big-default", messages=ANTHROPIC_PROMPT, max_tokens=64
        ) as stream:
            streamed = "".join([text async for text in stream.text_stream])
    assert [b.text for b in done.content if b.type == "text"] == [REPLY]
    assert streamed == REPLY
