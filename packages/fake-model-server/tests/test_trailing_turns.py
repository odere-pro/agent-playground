"""Trailing `system` and reminder `user` turns after a tool result (the Claude CLI adds them)."""

from __future__ import annotations

from typing import Any

from fake_model_server import Script

SCRIPT = Script.model_validate(
    {
        "rules": [
            {"match": "lookup:", "tool_call": {"name": "t", "arguments": {}}},
            {"after_tool": True, "match": "found", "reply": "answered"},
        ],
        "default_reply": "dflt",
    }
)
CALL: dict[str, Any] = {"role": "assistant", "content": None, "tool_calls": []}
TOOL: dict[str, Any] = {"role": "tool", "content": "found it"}


def _user(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def test_old_behavior_tool_last_and_plain_user_last() -> None:
    assert SCRIPT.pick([_user("lookup: x"), CALL, TOOL]).reply == "answered"
    assert SCRIPT.pick([_user("lookup: x")]).tool_call is not None
    assert SCRIPT.pick([_user("hi")]).reply == "dflt"


def test_old_behavior_tool_not_last_is_not_a_trigger_without_trailing_turns() -> None:
    assert SCRIPT.pick([TOOL, {"role": "assistant", "content": "x"}]).reply == "dflt"


def test_trailing_system_turns_are_skipped() -> None:
    sys_note: dict[str, Any] = {"role": "system", "content": "<total_tokens>9</total_tokens>"}
    messages = [_user("lookup: x"), CALL, TOOL, sys_note, sys_note]
    assert SCRIPT.pick(messages).reply == "answered"


def test_trailing_reminder_user_turn_is_skipped() -> None:
    sys_note: dict[str, Any] = {"role": "system", "content": "tokens"}
    reminder = _user("<system-reminder>Remember the todo list.</system-reminder>")
    assert SCRIPT.pick([_user("lookup: x"), CALL, TOOL, sys_note, reminder]).reply == "answered"
    assert SCRIPT.pick([_user("lookup: x"), CALL, TOOL, reminder, sys_note]).reply == "answered"


def test_a_new_user_turn_matching_a_plain_rule_is_not_skipped() -> None:
    messages: list[dict[str, Any]] = [
        _user("lookup: x"),
        CALL,
        TOOL,
        {"role": "system", "content": "n"},
    ]
    assert SCRIPT.pick([*messages, _user("lookup: y")]).tool_call is not None


def test_a_catch_all_plain_rule_keeps_the_old_answer_for_a_trailing_user() -> None:
    script = Script.model_validate(
        {
            "rules": [
                {"after_tool": True, "reply": "after"},
                {"reply": "catch-all"},
            ]
        }
    )
    assert script.pick([_user("a"), CALL, TOOL, _user("b")]).reply == "catch-all"


def test_no_tool_message_means_no_trigger() -> None:
    assert SCRIPT.pick([_user("hi"), {"role": "system", "content": "n"}]).reply == "dflt"
