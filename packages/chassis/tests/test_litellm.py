"""LiteLLMModel: the request shape, the tag header, env loading, error mapping, and streamed
tool-call assembly. Every test uses `httpx.MockTransport`; no socket.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from chassis.adapters.litellm import LiteLLMModel
from chassis.ports.model import (
    ModelChunk,
    ModelError,
    ModelMessage,
    ToolCallRequest,
    ToolSpec,
    Usage,
)

MESSAGES: list[ModelMessage] = [ModelMessage(role="user", content="hello")]


def _completion(text: str = "hi", **extra: Any) -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": "answered-by",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text, **extra},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }


def _capture() -> tuple[list[httpx.Request], httpx.MockTransport]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_completion())

    return seen, httpx.MockTransport(handler)


def _sse(*payloads: dict[str, Any]) -> bytes:
    lines = [f"data: {json.dumps(p)}\n\n" for p in payloads]
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def _chunk(delta: dict[str, Any], finish: str | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "model": "answered-by",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        **extra,
    }


async def test_request_carries_route_messages_and_options() -> None:
    seen, transport = _capture()
    model = LiteLLMModel("http://router/v1", transport=transport)
    result = await model.complete(
        MESSAGES, route="big-default", temperature=0.2, max_tokens=64, tools=[ToolSpec(name="t")]
    )
    body = json.loads(seen[0].content)
    assert str(seen[0].url) == "http://router/v1/chat/completions"
    assert body["model"] == "big-default"
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 64
    assert body["tools"] == [
        {"type": "function", "function": {"name": "t", "description": "", "parameters": {}}}
    ]
    assert "metadata" not in body and "x-litellm-tags" not in seen[0].headers
    assert result.text == "hi" and result.model == "answered-by"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (3, 2)


async def test_agent_tag_goes_in_header_and_metadata() -> None:
    seen, transport = _capture()
    model = LiteLLMModel("http://router/v1", agent="simplifier", transport=transport)
    await model.complete(MESSAGES, route="big-default")
    body = json.loads(seen[0].content)
    assert seen[0].headers["x-litellm-tags"] == "agent:simplifier"
    assert body["metadata"] == {"tags": ["agent:simplifier"]}


async def test_key_goes_in_the_bearer_header_only_and_is_not_in_repr() -> None:
    seen, transport = _capture()
    model = LiteLLMModel("http://router/v1", "sk-secret", transport=transport)
    await model.complete(MESSAGES, route="big-default")
    assert seen[0].headers["authorization"] == "Bearer sk-secret"
    assert "sk-secret" not in seen[0].content.decode()
    assert "sk-secret" not in repr(model)


def test_from_env_reads_url_key_and_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "http://router:4000/v1")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-env")
    model = LiteLLMModel.from_env(agent="echo")
    assert model.base_url == "http://router:4000/v1"
    assert model.agent == "echo"
    assert model._api_key == "sk-env"


PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


def test_the_client_ignores_proxy_variables_so_the_key_cannot_be_routed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """`trust_env=False`: a proxy variable or `.netrc` in the chassis environment never sees the
    bearer key. httpx 0.28 builds a proxy mount per env variable when `trust_env` is on.
    """
    monkeypatch.setenv("LITELLM_BASE_URL", "https://router:4000/v1")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-env-secret")
    for var in PROXY_VARS:
        monkeypatch.setenv(var, "http://proxy.invalid:3128")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    netrc = tmp_path / ".netrc"
    netrc.write_text("machine router login someone password other\n")
    monkeypatch.setenv("NETRC", str(netrc))
    client = LiteLLMModel.from_env()._client
    assert client.trust_env is False
    assert client._mounts == {}, "no proxy mount from the environment"
    target = client._transport_for_url(httpx.URL("https://router:4000/v1/chat/completions"))
    assert target is client._transport
    assert client.auth is None


def test_from_env_works_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LITELLM_BASE_URL", "http://router:4000/v1")
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    assert LiteLLMModel.from_env()._api_key is None


def test_from_env_needs_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    with pytest.raises(LookupError, match="LITELLM_BASE_URL"):
        LiteLLMModel.from_env()


@pytest.mark.parametrize(
    ("raise_", "code"),
    [
        (httpx.ConnectError("refused"), "connect_error"),
        (httpx.ReadTimeout("slow"), "timeout"),
    ],
)
async def test_transport_errors_are_retryable(raise_: Exception, code: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise raise_

    model = LiteLLMModel("http://router/v1", transport=httpx.MockTransport(handler))
    with pytest.raises(ModelError) as exc:
        await model.complete(MESSAGES, route="big-default")
    assert exc.value.code == code and exc.value.retryable
    with pytest.raises(ModelError) as exc:
        [c async for c in model.stream(MESSAGES, route="big-default")]
    assert exc.value.code == code and exc.value.retryable


async def test_401_is_a_model_error_and_not_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    model = LiteLLMModel("http://router/v1", transport=httpx.MockTransport(handler))
    with pytest.raises(ModelError) as exc:
        await model.complete(MESSAGES, route="big-default")
    assert exc.value.code == "http_401" and not exc.value.retryable
    assert "bad key" in exc.value.message
    with pytest.raises(ModelError) as exc:
        [c async for c in model.stream(MESSAGES, route="big-default")]
    assert exc.value.code == "http_401" and not exc.value.retryable


async def test_401_body_that_echoes_the_key_is_redacted() -> None:
    """LiteLLM's 401 text can quote the key it received. It must not reach `ModelError.message`,
    which flows into the proxy's error body, the workload's `error` event, and `/v1/run`.
    """
    key = "sk-1234567890abcdef"
    other = "sk-anotherkey_0987654321"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": f"Authentication Error, received key {key}; not {other}"}},
        )

    model = LiteLLMModel("http://router/v1", key, transport=httpx.MockTransport(handler))
    with pytest.raises(ModelError) as exc:
        await model.complete(MESSAGES, route="big-default")
    assert exc.value.code == "http_401"
    assert key not in exc.value.message and other not in exc.value.message
    assert exc.value.message == "Authentication Error, received key [redacted]; not [redacted]"


# Made-up values in LiteLLM's 401 echo shape (bring-up note item 2). Not a real key or hash.
_FAKE_SUFFIX = "sk-...q9z7"
_FAKE_HASH = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"


async def test_401_litellm_echo_of_key_suffix_and_hash_is_redacted() -> None:
    """Security 1: LiteLLM's 401 quotes `Received API Key = <suffix>` and
    `Key Hash (Token) = <hash>`. Neither may reach `ModelError.message`.
    """
    text = (
        "Authentication Error, Invalid proxy server token passed. "
        f"Received API Key = {_FAKE_SUFFIX}, Key Hash (Token) = {_FAKE_HASH}. "
        "Unable to find token in cache or `LiteLLM_VerificationTokenTable`"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": text}})

    model = LiteLLMModel("http://router/v1", transport=httpx.MockTransport(handler))
    with pytest.raises(ModelError) as exc:
        await model.complete(MESSAGES, route="big-default")
    message = exc.value.message
    assert exc.value.code == "http_401"
    assert _FAKE_SUFFIX not in message and "q9z7" not in message
    assert _FAKE_HASH not in message and _FAKE_HASH[:12] not in message
    assert "Invalid proxy server token passed." in message
    assert "Received API Key = [redacted]" in message
    assert "Key Hash (Token) = [redacted]" in message


async def test_401_without_the_echo_keeps_its_message() -> None:
    """Paired control for security 1: a 401 with no key echo keeps its text as sent."""
    text = "Authentication Error, No api key passed in."

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": text}})

    model = LiteLLMModel("http://router/v1", transport=httpx.MockTransport(handler))
    with pytest.raises(ModelError) as exc:
        await model.complete(MESSAGES, route="big-default")
    assert exc.value.code == "http_401"
    assert exc.value.message == text


async def test_error_message_is_capped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "x" * 1000}})

    model = LiteLLMModel("http://router/v1", transport=httpx.MockTransport(handler))
    with pytest.raises(ModelError) as exc:
        await model.complete(MESSAGES, route="big-default")
    assert len(exc.value.message) <= 300


async def test_503_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="try later")

    model = LiteLLMModel("http://router/v1", transport=httpx.MockTransport(handler))
    with pytest.raises(ModelError) as exc:
        await model.complete(MESSAGES, route="big-default")
    assert exc.value.code == "http_503" and exc.value.retryable


async def test_complete_parses_tool_call_arguments() -> None:
    call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "glossary_lookup", "arguments": '{"term": "SLM"}'},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion("", tool_calls=[call]))

    model = LiteLLMModel("http://router/v1", transport=httpx.MockTransport(handler))
    result = await model.complete(MESSAGES, route="big-default")
    assert result.text == ""
    assert [(t.call_id, t.name, t.arguments) for t in result.tool_calls] == [
        ("call_1", "glossary_lookup", {"term": "SLM"})
    ]


def _stream_model(body: bytes) -> LiteLLMModel:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        assert json.loads(request.content)["stream_options"] == {"include_usage": True}
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    return LiteLLMModel("http://router/v1", transport=httpx.MockTransport(handler))


async def test_stream_assembles_tool_call_arguments_across_fragments() -> None:
    body = _sse(
        _chunk({"role": "assistant", "content": ""}),
        _chunk({"content": "Looking "}),
        _chunk({"content": "up."}),
        _chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "glossary_lookup", "arguments": '{"te'},
                    }
                ]
            }
        ),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'rm": "S'}}]}),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'LM"}'}}]}),
        _chunk({}, "tool_calls"),
        {"id": "chatcmpl-1", "choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 4}},
    )
    chunks = [c async for c in _stream_model(body).stream(MESSAGES, route="big-default")]
    assert "".join(c.text for c in chunks) == "Looking up."
    calls = [c.tool_call for c in chunks if c.tool_call]
    assert len(calls) == 1
    assert (calls[0].call_id, calls[0].name, calls[0].arguments) == (
        "call_1",
        "glossary_lookup",
        {"term": "SLM"},
    )
    assert chunks[-1].finish and chunks[-1].usage is not None
    assert (chunks[-1].usage.input_tokens, chunks[-1].usage.output_tokens) == (7, 4)
    assert sum(1 for c in chunks if c.finish) == 1


async def test_stream_without_usage_ends_with_zero_usage() -> None:
    body = _sse(_chunk({"content": "ok"}), _chunk({}, "stop"))
    chunks = [c async for c in _stream_model(body).stream(MESSAGES, route="big-default")]
    assert chunks[-1] == ModelChunk(finish=True, usage=Usage())


async def test_close_is_idempotent() -> None:
    _, transport = _capture()
    model = LiteLLMModel("http://router/v1", transport=transport)
    await model.aclose()
    await model.aclose()


async def test_a_tool_conversation_goes_out_in_the_openai_shape() -> None:
    seen, transport = _capture()
    model = LiteLLMModel("http://router/v1", transport=transport)
    messages = [
        ModelMessage(role="user", name="sam", content="look up SLM"),
        ModelMessage(
            role="assistant",
            tool_calls=[ToolCallRequest(call_id="c1", name="glossary_lookup", arguments={"n": 3})],
        ),
        ModelMessage(role="tool", tool_call_id="c1", content="SLM: small language model"),
    ]
    await model.complete(messages, route="big-default")
    [c async for c in model.stream(messages, route="big-default")]
    expected = [
        {"role": "user", "content": "look up SLM", "name": "sam"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "glossary_lookup", "arguments": '{"n": 3}'},
                }
            ],
        },
        {"role": "tool", "content": "SLM: small language model", "tool_call_id": "c1"},
    ]
    assert [json.loads(r.content)["messages"] for r in seen] == [expected, expected]
