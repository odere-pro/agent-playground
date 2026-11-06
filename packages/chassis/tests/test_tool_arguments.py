"""`chassis.ports.tool_arguments.check_arguments` (R5b-08): the one JSON Schema argument check the
fake tools and the MCP gateway adapter share. A refusal is `bad_arguments` and names field names
and paths only, never an argument value.
"""

from __future__ import annotations

from typing import Any

import pytest
from chassis.ports.tool import ToolDefinition, ToolError
from chassis.ports.tool_arguments import check_arguments

SECRET = "sk-do-not-echo-0123456789"  # pragma: allowlist secret

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "term": {"type": "string"},
        "count": {"type": "integer"},
        "ratio": {"type": "number"},
        "flag": {"type": "boolean"},
        "note": {"type": ["string", "null"]},
        "mode": {"enum": ["fast", "slow"]},
        "pick": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
        "one": {"oneOf": [{"type": "string"}, {"type": "null"}]},
        "filter": {
            "type": "object",
            "properties": {"limit": {"type": "integer"}, "tag": {"type": "string"}},
            "required": ["limit"],
            "additionalProperties": False,
        },
    },
    "required": ["term"],
    "additionalProperties": False,
}

TOOL = ToolDefinition(name="probe", description="probe", parameters=SCHEMA, read_only=True)


def _refusal(arguments: dict[str, Any]) -> ToolError:
    with pytest.raises(ToolError) as info:
        check_arguments(TOOL, arguments)
    assert info.value.code == "bad_arguments"
    assert info.value.retryable is False
    return info.value


def test_valid_arguments_pass() -> None:
    check_arguments(
        TOOL,
        {
            "term": "SLM",
            "count": 2,
            "ratio": 0.5,
            "flag": True,
            "note": None,
            "mode": "fast",
            "pick": 3,
            "one": None,
            "filter": {"limit": 1, "tag": "x"},
        },
    )


@pytest.mark.parametrize(
    ("arguments", "path"),
    [
        ({}, "arguments"),
        ({"term": 3}, "arguments.term"),
        ({"term": "a", "count": True}, "arguments.count"),
        ({"term": "a", "ratio": False}, "arguments.ratio"),
        ({"term": "a", "note": 1}, "arguments.note"),
        ({"term": "a", "pick": 1.5}, "arguments.pick"),
        ({"term": "a", "one": 1}, "arguments.one"),
        ({"term": "a", "filter": {"limit": "1"}}, "arguments.filter.limit"),
        ({"term": "a", "filter": {}}, "arguments.filter"),
        ({"term": "a", "filter": {"limit": 1, "other": 2}}, "arguments.filter"),
        ({"term": "a", "extra": 1}, "arguments"),
    ],
)
def test_each_rule_refuses_and_names_the_path(arguments: dict[str, Any], path: str) -> None:
    error = _refusal(arguments)
    assert error.message.startswith("probe: ")
    assert f"{path}:" in error.message


def test_bool_is_never_an_integer_or_a_number() -> None:
    assert "arguments.count" in _refusal({"term": "a", "count": False}).message
    assert "arguments.ratio" in _refusal({"term": "a", "ratio": True}).message


@pytest.mark.parametrize(
    "arguments",
    [
        {"term": "a", "mode": SECRET},
        {"term": SECRET, "count": SECRET},
        {"term": "a", "filter": {"limit": SECRET}},
        {"term": "a", "pick": {"k": SECRET}},
    ],
)
def test_a_refusal_never_echoes_an_argument_value(arguments: dict[str, Any]) -> None:
    assert SECRET not in _refusal(arguments).message


def test_enum_refusal_names_the_field_only() -> None:
    error = _refusal({"term": "a", "mode": "medium"})
    assert "arguments.mode: not one of the allowed values" in error.message
    assert "medium" not in error.message


def test_an_unknown_type_name_and_unknown_keywords_are_not_checked() -> None:
    tool = ToolDefinition(
        name="loose",
        description="loose",
        parameters={"type": "object", "properties": {"x": {"type": "date", "format": "ip"}}},
    )
    check_arguments(tool, {"x": 1, "y": 2})


def test_a_long_unexpected_key_is_cut() -> None:
    error = _refusal({"term": "a", "k" * 500: 1})
    assert len(error.message) < 200
