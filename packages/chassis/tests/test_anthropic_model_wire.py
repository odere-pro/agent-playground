"""The Anthropic proxy route's request reader (contract v5, A.5): pure mapping from a raw body onto
`ModelMessage`, `ToolSpec`, and `max_tokens`, with the review corrections of 2026-10-09.
"""

from __future__ import annotations

from typing import Any

import pytest
from chassis.adapters.anthropic_compat import model_wire
from chassis.adapters.anthropic_compat.model_wire import (
    ParsedMessages,
    RefusedField,
    parse_messages_request,
)
from chassis.ports.model import ModelMessage, ToolCallRequest
from pydantic import ValidationError


def _body(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": "big-default",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "hi"}],
    }
    body.update(over)
    return body


def _parse(**over: Any) -> ParsedMessages:
    return parse_messages_request(_body(**over))


def _refused(**over: Any) -> RefusedField:
    with pytest.raises(RefusedField) as info:
        _parse(**over)
    return info.value


def _tool_use(call_id: str = "call_1", name: str = "t") -> dict[str, Any]:
    return {"type": "tool_use", "id": call_id, "name": name, "input": {"a": 1}}


def _result(call_id: str, text: str = "r") -> dict[str, Any]:
    return {"type": "tool_result", "tool_use_id": call_id, "content": text}


# ---- correction 5: empty turns ------------------------------------------------------------------


def test_an_assistant_turn_empty_after_thinking_is_skipped() -> None:
    parsed = _parse(
        messages=[
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": [{"type": "thinking", "thinking": "hm"}]},
            {"role": "user", "content": "b"},
        ]
    )
    assert [m.role for m in parsed.messages] == ["user", "user"]
    assert "content.thinking" in parsed.ignored


@pytest.mark.parametrize("content", ["", [], [{"type": "text", "text": ""}]])
def test_an_empty_assistant_turn_is_skipped(content: Any) -> None:
    parsed = _parse(
        messages=[
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": content},
            {"role": "user", "content": "b"},
        ]
    )
    assert [m.content for m in parsed.messages] == ["a", "b"]


@pytest.mark.parametrize(
    "system",
    [
        "",
        [],
        [{"type": "text", "text": ""}],
        [{"type": "text", "text": "x-anthropic-billing-header: v"}],
    ],
)
def test_an_empty_system_entry_is_skipped(system: Any) -> None:
    parsed = _parse(system=system)
    assert [m.role for m in parsed.messages] == ["user"]


@pytest.mark.parametrize("content", ["", [], [{"type": "text", "text": ""}]])
def test_an_empty_system_role_message_is_skipped(content: Any) -> None:
    parsed = _parse(
        messages=[
            {"role": "system", "content": content},
            {"role": "user", "content": "a"},
            {"role": "system", "content": content},
        ]
    )
    assert [m.role for m in parsed.messages] == ["user"]


@pytest.mark.parametrize(
    "content", ["", [], [{"type": "text", "text": ""}], [{"type": "thinking", "thinking": "x"}]]
)
def test_a_user_turn_with_no_content_is_refused_with_fixed_text(content: Any) -> None:
    refused = _refused(messages=[{"role": "user", "content": content}])
    assert (refused.code, refused.param) == ("invalid_body", "messages[0].content")
    assert refused.message == "a user turn needs content"


def test_a_model_message_validation_error_never_escapes(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(raw: Any) -> Any:
        ModelMessage(role="tool")  # a real pydantic ValidationError
        raise AssertionError("unreachable")

    monkeypatch.setattr(model_wire, "_parse", boom)
    with pytest.raises(RefusedField) as info:
        parse_messages_request(_body())
    assert isinstance(info.value.__cause__, ValidationError)
    assert info.value.code == "invalid_body"


# ---- correction 7: booleans and ids -------------------------------------------------------------


@pytest.mark.parametrize("key", ["max_tokens", "temperature"])
def test_a_json_boolean_is_refused_for_numbers(key: str) -> None:
    for value in (True, False):
        refused = _refused(**{key: value})
        assert (refused.code, refused.param) == ("invalid_body", key)


def test_a_tool_result_resolves_to_the_nearest_preceding_tool_use() -> None:
    parsed = _parse(
        messages=[
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": [_tool_use("dup", "first")]},
            {"role": "user", "content": [_result("dup", "one")]},
            {"role": "assistant", "content": [_tool_use("dup", "second")]},
            {"role": "user", "content": [_result("dup", "two")]},
        ]
    )
    tools = [m for m in parsed.messages if m.role == "tool"]
    assert [(m.tool_call_id, m.content) for m in tools] == [("dup", "one"), ("dup", "two")]
    calls = [m.tool_calls for m in parsed.messages if m.tool_calls]
    assert calls == [
        [ToolCallRequest(call_id="dup", name="first", arguments={"a": 1})],
        [ToolCallRequest(call_id="dup", name="second", arguments={"a": 1})],
    ]


def test_a_tool_result_for_a_later_tool_use_is_refused() -> None:
    refused = _refused(
        messages=[
            {"role": "user", "content": [_result("later")]},
            {"role": "assistant", "content": [_tool_use("later")]},
            {"role": "user", "content": "x"},
        ]
    )
    assert refused.code == "unsupported_message"
    assert refused.param == "messages[0].content[0]"


# ---- correction 3: no request content in a refusal ----------------------------------------------

CANARY = "canary-7f3a9c1e5b2d"


def test_a_refusal_never_echoes_request_content() -> None:
    bodies = [
        _body(messages=[{"role": "user", "content": [_result(CANARY)]}]),
        _body(messages=[{"role": CANARY, "content": "x"}]),
        _body(messages=[{"role": "user", "content": [{"type": CANARY}]}]),
        _body(tools=[{"type": CANARY, "name": "n"}]),
        _body(tool_choice={"type": CANARY}),
        _body(system=[{"type": CANARY, "text": "x"}]),
    ]
    for body in bodies:
        with pytest.raises(RefusedField) as info:
            parse_messages_request(body)
        assert CANARY not in info.value.text() and CANARY not in str(info.value)


# ---- the rest of the table, in brief ------------------------------------------------------------


def test_system_blocks_billing_dropped_and_joined_at_position_zero() -> None:
    parsed = _parse(
        system=[
            {"type": "text", "text": "x-anthropic-billing-header: v1"},
            {"type": "text", "text": "You are an agent.", "cache_control": {"type": "ephemeral"}},
        ],
        messages=[
            {"role": "user", "content": "a"},
            {"role": "system", "content": [{"type": "text", "text": "# Environment"}]},
        ],
    )
    assert parsed.messages == [
        ModelMessage(role="system", content="You are an agent."),
        ModelMessage(role="user", content="a"),
        ModelMessage(role="system", content="# Environment"),
    ]
    assert parsed.ignored == ["system.billing_header", "cache_control"]


def test_tools_drop_the_top_level_schema_key_and_keep_a_nested_one() -> None:
    parsed = _parse(
        tools=[
            {
                "name": "t",
                "input_schema": {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "properties": {"x": {"$schema": "nested"}},
                },
            }
        ]
    )
    assert parsed.tools is not None
    assert parsed.tools[0].parameters == {
        "type": "object",
        "properties": {"x": {"$schema": "nested"}},
    }


def test_dropped_fields_are_counted_by_name_and_metadata_is_not() -> None:
    parsed = _parse(
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        context_management={"edits": []},
        safeguards={"cwd": "/secret/path"},
        metadata={"user_id": "someone"},
        brand_new_key=1,
    )
    assert parsed.ignored == [
        "output_config.effort",
        "thinking",
        "context_management",
        "safeguards",
        "other",
    ]


@pytest.mark.parametrize(
    ("over", "param"),
    [
        ({"output_config": {"format": {"type": "json_schema"}}}, "output_config.format"),
        ({"mcp_servers": []}, "mcp_servers"),
        ({"container": "c"}, "container"),
        ({"tool_choice": {"type": "any"}}, "tool_choice"),
        ({"tools": [{"type": "bash_20250124", "name": "bash"}]}, "tools[0].type"),
    ],
)
def test_unsupported_parameters_are_refused(over: dict[str, Any], param: str) -> None:
    refused = _refused(**over)
    assert (refused.code, refused.param) == ("unsupported_parameter", param)


def test_a_last_assistant_turn_is_refused() -> None:
    refused = _refused(
        messages=[{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    )
    assert refused.message == "assistant prefill is not supported"
