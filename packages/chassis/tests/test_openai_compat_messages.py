"""The chat mapping lives once. The OpenAI message mapping is
`chassis.adapters.openai_compat.messages`, and the model proxy uses that module, not a copy (PoC-3
open note, section 1). The format-neutral helpers are `chassis.adapters.chat_common` and
`chassis.core.inbound`, shared by both chat adapters. Behavior is pinned by `test_model_proxy.py`
and the inbound contract suite; this checks the moves, the public names, and that both chat
adapters decide alike.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from chassis.adapters import anthropic_compat, chat_common
from chassis.adapters.anthropic_compat.inbound import AnthropicInbound
from chassis.adapters.openai_compat import messages
from chassis.adapters.openai_compat.inbound import OpenAIInbound
from chassis.core.envelope import Response, TaskInput, Versions
from chassis.core.inbound import Ids, ReplyMeta, Served
from chassis.ports.model import ModelMessage, ToolCallRequest
from chassis.server import model_proxy

VERSIONS = Versions(chassis="c", config="cfg", prompt="p", model_route="route")
SERVED = Served(agent="echo", agent_version="0.0.1", versions=VERSIONS)
IDS = Ids(request_id="r1", trace_id="t1", idempotency_key="i1")
CHAT_ADAPTERS = [
    pytest.param(OpenAIInbound(), id="openai"),
    pytest.param(AnthropicInbound(), id="anthropic"),
]
ANTHROPIC_MODULES = sorted(Path(anthropic_compat.__file__).parent.glob("*.py"))


def test_the_model_proxy_uses_the_shared_mapping() -> None:
    """The model proxy reuses the shared message mapping and error class, not copies."""
    assert model_proxy.to_port_message is messages.to_port_message
    assert model_proxy.UnsupportedMessage is messages.UnsupportedMessage


def test_the_public_names() -> None:
    """The shared helpers check emptiness, join text, refuse extras, and map tool calls."""
    assert messages.is_empty(None) and messages.is_empty("") and messages.is_empty([])
    assert not messages.is_empty("x") and not messages.is_empty(0)
    assert messages.text_of(
        [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}], "p"
    ) == ("a\nb")
    assert messages.text_of(None, "p") is None
    with pytest.raises(messages.UnsupportedMessage) as refused:
        messages.text_of([{"type": "image_url"}], "messages[0].content")
    assert refused.value.param == "messages[0].content"
    messages.refuse_extra({"role": "user", "refusal": None}, messages.MESSAGE_KEYS, "m")
    with pytest.raises(messages.UnsupportedMessage, match="audio"):
        messages.refuse_extra({"audio": {"id": "a"}}, messages.MESSAGE_KEYS, "m")
    calls = messages.tool_calls_of(
        [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}], "p"
    )
    assert calls == [ToolCallRequest(call_id="c1", name="f", arguments={})]
    assert messages.to_port_message(0, {"role": "user", "content": "hi"}) == ModelMessage(
        role="user", content="hi"
    )


def test_refuse_extra_names_who_does_not_support_the_key() -> None:
    """The model proxy's wording is the default, byte for byte; the OpenAI interface names
    itself, since its client has never heard of a model port.
    """
    with pytest.raises(messages.UnsupportedMessage) as proxy:
        messages.refuse_extra({"audio": {"id": "a"}}, messages.MESSAGE_KEYS, "m")
    assert proxy.value.message == "audio is not supported by the model port"
    with pytest.raises(messages.UnsupportedMessage) as public:
        messages.refuse_extra(
            {"audio": {"id": "a"}}, messages.MESSAGE_KEYS, "m", by="this interface"
        )
    assert public.value.message == "audio is not supported by this interface"


def _imported(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    names = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    names += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    return names


@pytest.mark.parametrize("path", ANTHROPIC_MODULES, ids=[p.name for p in ANTHROPIC_MODULES])
def test_anthropic_adapter_names_no_openai_compat_when_its_imports_are_listed(path: Path) -> None:
    """The Anthropic adapter takes the format-neutral helpers from `chat_common`."""
    names = _imported(path)

    assert [name for name in names if "openai_compat" in name] == []


def test_openai_messages_keeps_the_old_names_when_the_helpers_live_in_chat_common() -> None:
    """The old import path gives the very objects `chat_common` defines."""
    assert messages.UnsupportedMessage is chat_common.UnsupportedMessage
    assert messages.is_empty is chat_common.is_empty
    assert messages.text_of is chat_common.text_of


def _meta(adapter: Any) -> ReplyMeta:
    body = {"model": "echo", "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]}
    request = adapter.to_request(body, {}, ids=IDS, served=SERVED)
    return ReplyMeta(IDS, SERVED, 0).for_request(request)


def _code_and_message(body: dict[str, Any]) -> tuple[str, str]:
    """`(code, message)` from either chat format's error body."""
    if body.get("type") == "error":  # Anthropic: "<code>: <message>"
        code, _, message = body["error"]["message"].partition(": ")
        return code, message
    return body["error"]["code"], body["error"]["message"]


@pytest.mark.parametrize("adapter", CHAT_ADAPTERS)
def test_chat_adapter_answers_engine_error_when_a_run_ended_without_an_end_event(
    adapter: Any,
) -> None:
    """A `Response` with status error and no error event is `engine_error`, same text."""
    response = Response(
        request_id="r1",
        trace_id="t1",
        idempotency_key="i1",
        agent="echo",
        agent_version="0.0.1",
        status="error",
        output={"text": "partial"},
        versions=VERSIONS,
    )

    reply = adapter.complete(response, _meta(adapter))

    assert reply.status == 500
    assert _code_and_message(reply.body) == ("engine_error", "the run ended without an end event")
    assert reply.headers["x-should-retry"] == "false"


@pytest.mark.parametrize("adapter", CHAT_ADAPTERS)
def test_chat_adapter_maps_system_and_history_alike_when_given_one_earlier_turn(
    adapter: Any,
) -> None:
    """Both chat adapters build the same `input.data`, key order included."""
    body = {
        "model": "echo",
        "max_tokens": 64,
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q"},
        ],
    }

    request = adapter.to_request(body, {}, ids=IDS, served=SERVED)

    expected = {"system": "be brief", "history": [{"role": "assistant", "text": "a1"}]}
    assert request.input == TaskInput(text="q", data=expected)
    assert list(request.input.data) == ["system", "history"]
