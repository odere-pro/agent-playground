"""Router compatibility: which request body keys the Agents SDK sends to `/v1/chat/completions`,
and which of them the chassis model proxy keeps.

The proxy (`chassis.server.model_proxy.ChatCompletionRequest`) keeps `model`, `messages`,
`temperature`, `max_tokens`, `tools`, and `stream`, and ignores the rest. The test does not import
the chassis: the keep list is copied below. The README table is generated from these constants.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx2
import oai_support as s
import pytest
from agents import (
    Agent,
    ModelSettings,
    OpenAIChatCompletionsModel,
    RunConfig,
    Runner,
    function_tool,
)
from openai import AsyncOpenAI
from openai.types.shared import Reasoning

PROXY_KEEPS = frozenset({"model", "messages", "temperature", "max_tokens", "tools", "stream"})
"""The body keys the chassis model proxy reads. Copied from `ChatCompletionRequest`."""
PROXY_MESSAGE_KEYS = frozenset({"role", "content", "tool_calls", "tool_call_id", "name"})
"""The message keys the port carries. Any other key with a value is a 400 `unsupported_message`."""

SENT_BY_HANDLE = frozenset({"model", "messages", "stream", "tools"})
"""What `handle` sends: its default settings. All four are kept by the proxy."""
SENT_WITH_EVERY_SETTING = frozenset(
    {
        "model",
        "messages",
        "stream",
        "tools",
        "temperature",
        "max_tokens",
        "top_p",
        "frequency_penalty",
        "presence_penalty",
        "tool_choice",
        "parallel_tool_calls",
        "stream_options",
        "store",
        "reasoning_effort",
        "verbosity",
        "top_logprobs",
        "logprobs",
        "metadata",
    }
)
"""What the SDK can send when every `ModelSettings` field is set (the workload sets none). The
proxy drops `top_p`, `frequency_penalty`, `presence_penalty`, `tool_choice`, `parallel_tool_calls`,
`stream_options`, `store`, `reasoning_effort`, `verbosity`, `top_logprobs`, `logprobs`, `metadata`.
`response_format` (an `output_type`) and `prompt_cache_*` are sent only for those options."""


@pytest.fixture
async def stubs(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[s.Stubs]:
    async with s.running(monkeypatch) as stubs:
        yield stubs


async def test_the_workload_sends_only_keys_the_proxy_keeps(stubs: s.Stubs) -> None:
    await s.run(s.LOOKUP)
    bodies = stubs.model_bodies
    assert len(bodies) == 3
    for body in bodies:
        assert set(body) == SENT_BY_HANDLE
        assert set(body) <= PROXY_KEEPS, f"dropped: {sorted(set(body) - PROXY_KEEPS)}"
    # Not sent, so the proxy's own defaults apply: temperature 0.0, and max_tokens is whatever
    # the run's budget has left.
    assert not {"temperature", "max_tokens", "tool_choice", "parallel_tool_calls"} & set(bodies[0])
    assert "stream_options" not in bodies[0], "the proxy asks the upstream for usage itself"


async def test_message_and_tool_keys_are_ones_the_port_carries(stubs: s.Stubs) -> None:
    await s.run(s.LOOKUP)
    messages = [m for body in stubs.model_bodies for m in body["messages"]]
    assert {m["role"] for m in messages} == {"system", "user", "assistant", "tool"}
    for message in messages:
        assert set(message) <= PROXY_MESSAGE_KEYS, message
    for body in stubs.model_bodies:
        for tool in body["tools"]:
            assert set(tool) == {"type", "function"}
            # `strict` is ignored by the proxy (extra keys on a tool are dropped).
            assert set(tool["function"]) == {"name", "description", "parameters", "strict"}


async def test_every_setting_that_the_sdk_can_send(stubs: s.Stubs) -> None:
    """The full list: each `ModelSettings` field that reaches the body, set at once."""
    http = httpx2.AsyncClient(transport=s.handle_module.transport)
    client = AsyncOpenAI(base_url=s.MODEL_URL, api_key="not-a-key", http_client=http, max_retries=0)

    @function_tool
    def ping() -> str:
        """Ping."""
        return "pong"

    settings = ModelSettings(
        temperature=0.2,
        top_p=0.9,
        frequency_penalty=0.1,
        presence_penalty=0.1,
        max_tokens=50,
        tool_choice="auto",
        parallel_tool_calls=True,
        include_usage=True,
        store=False,
        verbosity="low",
        top_logprobs=1,
        metadata={"k": "v"},
        reasoning=Reasoning(effort="low"),
    )
    agent: Agent[Any] = Agent(
        name="probe",
        model=OpenAIChatCompletionsModel(model="big-default", openai_client=client),
        model_settings=settings,
        tools=[ping],
    )
    result = Runner.run_streamed(
        agent, "simplify: Hello.", run_config=RunConfig(tracing_disabled=True)
    )
    async for _ in result.stream_events():
        pass
    sent = set(stubs.model_bodies[-1])
    assert sent == SENT_WITH_EVERY_SETTING, sorted(sent ^ SENT_WITH_EVERY_SETTING)
    dropped = sorted(sent - PROXY_KEEPS)
    assert dropped == [
        "frequency_penalty",
        "logprobs",
        "metadata",
        "parallel_tool_calls",
        "presence_penalty",
        "reasoning_effort",
        "store",
        "stream_options",
        "tool_choice",
        "top_logprobs",
        "top_p",
        "verbosity",
    ]
    await http.aclose()
