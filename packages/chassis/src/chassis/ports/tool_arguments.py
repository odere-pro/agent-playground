"""`check_arguments`: the one JSON Schema check of a tool call's arguments (R5b-08).

The fake tools (`chassis.fakes.tool`) and the MCP gateway adapter (`chassis.adapters.mcp.gateway`)
both call it, so the fake and the real adapter refuse the same arguments. It lives in `ports`, not
`core`, because it raises the port's `ToolError`, and `core` imports nothing above it. It imports
no SDK; `jsonschema` is not a chassis dependency.

Checked keywords:

- `type`, one name or a list. A bool is never `integer` or `number`. An unknown name passes.
- `enum`.
- `anyOf` and `oneOf`: the value must match at least one option. `oneOf` is not checked for
  "exactly one"; the tool server stays the final judge.
- `properties` (nested, to any depth the schema has), `required`, and
  `additionalProperties: false`.

Other keywords (`items`, `$ref`, `format`, bounds) are not checked. A refusal is
`ToolError("bad_arguments")` whose message names the tool, the schema's field names, and paths
only, never an argument value. An unexpected key is counted, never named: the model chooses it.
So a value never reaches an error, a log, or a span.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from chassis.ports.tool import ToolDefinition, ToolError

_TYPES: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, Mapping),
    "array": lambda v: isinstance(v, list | tuple),
    "null": lambda v: v is None,
}

MAX_KEY_SHOWN = 64
"""suggested: the longest field name a message shows; a longer one is cut."""

MAX_PROBLEMS_SHOWN = 5
"""suggested: the most problems one message lists."""


def _label(key: Any) -> str:
    text = str(key)
    return text if len(text) <= MAX_KEY_SHOWN else text[:MAX_KEY_SHOWN] + "..."


def _type_ok(value: Any, kind: Any) -> bool:
    names = [kind] if isinstance(kind, str) else kind
    if not isinstance(names, list):
        return True
    checks = [_TYPES.get(n) for n in names if isinstance(n, str)]
    return any(check is None or check(value) for check in checks) if checks else True


def _object_problems(value: Mapping[str, Any], schema: Mapping[str, Any], path: str) -> list[str]:
    properties = schema.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    problems = [
        f"{path}: missing required {_label(key)!r}"
        for key in schema.get("required") or ()
        if key not in value
    ]
    unexpected = 0
    for key, item in value.items():
        if key in properties:
            problems.extend(_problems(item, properties[key], f"{path}.{_label(key)}"))
        elif schema.get("additionalProperties") is False:
            unexpected += 1
    if unexpected:
        # A count, never the name: the model chooses an unexpected key, so it may carry a value.
        problems.append(f"{path}: {unexpected} unexpected field{'s' if unexpected > 1 else ''}")
    return problems


def _problems(value: Any, schema: Any, path: str) -> list[str]:
    """What is wrong with `value` under `schema`, by path only."""
    if not isinstance(schema, Mapping):
        return []
    if "type" in schema and not _type_ok(value, schema["type"]):
        return [f"{path}: expected {_kinds(schema['type'])}"]
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        return [f"{path}: not one of the allowed values"]
    options = schema.get("anyOf") or schema.get("oneOf")
    matched = isinstance(options, list) and any(not _problems(value, o, path) for o in options)
    if isinstance(options, list) and options and not matched:
        return [f"{path}: matches none of the allowed schemas"]
    if isinstance(value, Mapping):
        return _object_problems(value, schema, path)
    return []


def _kinds(kind: Any) -> str:
    names = [kind] if isinstance(kind, str) else kind
    return " or ".join(str(n) for n in names) if isinstance(names, list) else str(kind)


def check_arguments(definition: ToolDefinition, arguments: Mapping[str, Any]) -> None:
    """Raise `ToolError("bad_arguments")` when `arguments` fail `definition.parameters`."""
    problems = _problems(dict(arguments), definition.parameters, "arguments")
    if problems:
        shown = "; ".join(problems[:MAX_PROBLEMS_SHOWN])
        more = len(problems) - MAX_PROBLEMS_SHOWN
        tail = f"; and {more} more" if more > 0 else ""
        raise ToolError("bad_arguments", f"{_label(definition.name)}: {shown}{tail}")
