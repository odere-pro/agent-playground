"""`chat_common.materialize`: one reader for both chat formats (contract v8, item 2; R3-14).

The OpenAI interface and the Anthropic interface (public port) read FastAPI's lazy iterators with
the same function, so a failing item is named the same way on both: the bracket form,
`messages[1].content`. Pure: no app, no engine.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pytest
from chassis.adapters.anthropic_compat.inbound import AnthropicInbound
from chassis.adapters.chat_common import TOO_DEEP_MESSAGE, InvalidBody, materialize
from chassis.adapters.openai_compat.inbound import OpenAIInbound
from chassis.core.envelope import Versions
from chassis.core.inbound import Ids, Refused, Served
from pydantic import BaseModel, TypeAdapter

VERSIONS = Versions(chassis="c", config="cfg", prompt="p", model_route="route")
SERVED = Served(agent="echo", agent_version="0.0.1", versions=VERSIONS)
IDS = Ids(request_id="r1", trace_id="t1", idempotency_key="i1")


class _Message(BaseModel):
    role: str
    content: str


def _lazy_messages() -> Iterable[_Message]:
    """A `ValidatorIterator`, as FastAPI hands it over: item 1 fails when it is read."""
    items = [{"role": "user", "content": "a"}, {"role": "user", "content": 5}]
    return TypeAdapter(Iterable[_Message]).validate_python(items)


def _refusal_text(adapter: Any, body: dict[str, Any]) -> str:
    with pytest.raises(Refused) as info:
        adapter.to_request(body, {}, ids=IDS, served=SERVED)
    assert info.value.status == 400
    text = info.value.body["error"]["message"]
    assert isinstance(text, str)
    return text


def _nested(depth: int) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for _ in range(depth):
        value = {"a": value}
    return value


def test_both_chat_routes_name_a_failing_lazy_item_the_same_way() -> None:
    """A lazy messages iterator that fails at item 1 is `messages[1].content` on both routes."""
    openai = _refusal_text(OpenAIInbound(), {"model": "echo", "messages": _lazy_messages()})
    anthropic = _refusal_text(
        AnthropicInbound(), {"model": "echo", "max_tokens": 64, "messages": _lazy_messages()}
    )

    assert "messages[1].content" in openai, openai
    assert "messages[1].content" in anthropic, anthropic


def test_openai_refuses_with_400_when_the_body_nests_past_the_python_stack() -> None:
    """OpenAI gets Anthropic's rule: a body nested too deep is 400, never a plain 500."""
    body = {"model": "echo", "messages": [], "metadata": _nested(5000)}

    text = _refusal_text(OpenAIInbound(), body)

    assert TOO_DEEP_MESSAGE in text


def test_materialize_reads_every_lazy_value_and_leaves_the_input_alone() -> None:
    """Mappings become dicts and iterables lists; the input mapping is not changed."""
    value = {"a": (1, 2), "b": {"c": iter(["x"])}, "s": "text"}

    out = materialize(value)

    assert out == {"a": [1, 2], "b": {"c": ["x"]}, "s": "text"}
    assert isinstance(value["a"], tuple)


class _Wide(BaseModel):
    a: str
    b: str
    c: str
    d: str


def test_invalid_body_keeps_at_most_three_distinct_parts() -> None:
    """At most three distinct `<loc>: <msg>` parts, then `"; ..."`; the input is not echoed."""
    lazy: Iterable[_Wide] = TypeAdapter(Iterable[_Wide]).validate_python(
        [{"a": 1, "b": 2, "c": 3, "d": 4}]
    )

    with pytest.raises(InvalidBody) as info:
        materialize({"messages": lazy})

    text = str(info.value)
    assert text.startswith("messages[0].a: ")
    assert text.endswith("; ...")
    assert text.count("; ") == 3, text


def test_anthropic_compat_still_exports_the_old_names() -> None:
    """The Anthropic module re-exports `materialize`, `InvalidBody`, and `TOO_DEEP_MESSAGE`."""
    from chassis.adapters.anthropic_compat import inbound

    assert inbound.materialize is materialize
    assert inbound.InvalidBody is InvalidBody
    assert inbound.TOO_DEEP_MESSAGE == TOO_DEEP_MESSAGE
