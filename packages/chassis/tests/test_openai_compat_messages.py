"""The OpenAI message mapping lives once, in `chassis.adapters.openai_compat.messages`; the model
proxy uses that module, not a copy (PoC-3 open note, section 1). Its behavior is pinned by
`test_model_proxy.py`; this checks the move and the public names.
"""

from __future__ import annotations

import pytest
from chassis.adapters.openai_compat import messages
from chassis.ports.model import ModelMessage, ToolCallRequest
from chassis.server import model_proxy


def test_the_model_proxy_uses_the_shared_mapping() -> None:
    assert model_proxy.to_port_message is messages.to_port_message
    assert model_proxy.UnsupportedMessage is messages.UnsupportedMessage


def test_the_public_names() -> None:
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
